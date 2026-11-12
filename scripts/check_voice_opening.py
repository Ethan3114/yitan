import argparse
import asyncio
import audioop
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import wave
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".intermediate/p0-opening"
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.core.crypto import build_fernet
from app.services.cloudbase import CloudRepository


def private_write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
    path.chmod(0o600)


def private_json(path: Path, value: dict) -> None:
    private_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def expect(response: httpx.Response, status: int = 200) -> dict:
    if response.status_code != status:
        body = response.json()
        code = body.get("error", {}).get("code", "unknown") if isinstance(body, dict) else "unknown"
        raise RuntimeError(f"公网验证失败 HTTP {response.status_code} code={code}")
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError("公网接口没有返回对象")
    return body


def assert_idle(repository: CloudRepository) -> dict:
    current = datetime.now(timezone.utc)
    calls = list(repository.rows("calls", filters={"status": "eq.live"}, columns="created_at,heartbeat_at"))
    tasks = list(repository.rows("draft_tasks", filters={"status": "eq.running"}, columns="created_at,lease_until"))
    active_calls = sum(datetime.fromisoformat(row["heartbeat_at"] or row["created_at"]) > current - timedelta(minutes=2) for row in calls)
    active_tasks = sum(not row["lease_until"] or datetime.fromisoformat(row["lease_until"]) > current for row in tasks)
    if active_calls or active_tasks:
        raise RuntimeError("有实际访谈或整理正在进行，收费验证尚未启动")
    return {"active_calls": active_calls, "active_tasks": active_tasks, "stale_live_rows": len(calls), "expired_task_leases": len(tasks)}


async def verify_socket(client: httpx.AsyncClient, origin: str, call_id: str, source: bytes) -> dict:
    cookie = "; ".join(f"{item.name}={item.value}" for item in client.cookies.jar)
    received, playback, rendered, users, mixed_all = [bytearray() for _ in range(5)]
    first_audio = asyncio.Event()
    terminal = None
    opening_requested = False
    first_audio_delay = None
    first_audio_at = None
    last_audio_at = None
    source_started_at = None
    resampling = None
    sequence = 0
    sent_frames = 0
    audio_id = 0
    interrupted_bytes = 0
    received_types = Counter()
    started = None
    ws_origin = origin.replace("https://", "wss://", 1)
    connected_at = time.monotonic()
    async with websockets.connect(ws_origin + f"/api/v1/calls/{call_id}/live", origin=origin, additional_headers={"Cookie": cookie}, max_size=2**20, open_timeout=90, ping_interval=15, ping_timeout=60) as socket:
        ready = json.loads(await asyncio.wait_for(socket.recv(), 90))
        if ready.get("type") != "ready":
            raise RuntimeError("语音链路未接通：" + ready.get("error", {}).get("code", "missing_ready"))
        started = time.monotonic()
        ready_delay = started - connected_at
        await socket.send(json.dumps({"type": "timeline", "kind": "start", "at_ms": 0, "wall_ms": 0}))

        async def timeline(kind: str, **fields) -> None:
            elapsed = (time.monotonic() - started) * 1000
            await socket.send(json.dumps({"type": "timeline", "kind": kind, "at_ms": elapsed, "wall_ms": elapsed, **fields}))

        async def download() -> None:
            nonlocal terminal, opening_requested, first_audio_delay, first_audio_at, last_audio_at, resampling, audio_id, interrupted_bytes
            async for raw in socket:
                event = json.loads(raw)
                kind = event.get("type", "")
                received_types[kind] += 1
                if kind == "opening_requested":
                    opening_requested = True
                elif kind == "audio":
                    chunk = base64.b64decode(event["pcm"], validate=True)
                    if not chunk or len(chunk) % 2:
                        raise ValueError("真实供应商音频没有通过 PCM16 校验")
                    if not first_audio.is_set():
                        if not opening_requested or sent_frames:
                            raise AssertionError("主动开场之前不应发送用户音频")
                        first_audio_at = time.monotonic()
                        first_audio_delay = first_audio_at - started
                    new_schedule = not playback and (last_audio_at is None or time.monotonic() - last_audio_at > 0.3)
                    received.extend(chunk)
                    converted, resampling = audioop.ratecv(chunk, 2, 1, 24000, 16000, resampling)
                    playback.extend(converted)
                    last_audio_at = time.monotonic()
                    if new_schedule:
                        audio_id += 1
                        await timeline("schedule", duration_ms=len(converted) / 32, audio_id=audio_id)
                    first_audio.set()
                elif kind == "interrupted":
                    interrupted_bytes += len(playback)
                    playback.clear()
                    await timeline("interrupt")
                elif kind in {"done", "error"}:
                    terminal = event
                    if kind == "error":
                        raise RuntimeError("公网语音返回错误：" + event.get("error", {}).get("code", "unknown"))
                    return

        async def upload() -> None:
            nonlocal sequence, sent_frames, source_started_at
            await asyncio.wait_for(first_audio.wait(), 25)
            frame_at = round((time.monotonic() - started) * 50)
            mixed_all.extend(bytes(frame_at * 640))
            source_offset = 0
            source_finished_at = None
            while time.monotonic() - started < 55:
                await asyncio.sleep(max(0, started + frame_at * 0.02 - time.monotonic()))
                current = time.monotonic()
                if source_started_at is None and current - first_audio_at >= 2 and not playback and current - last_audio_at >= 0.6:
                    source_started_at = current
                if source_started_at is not None and source_offset < len(source):
                    user = source[source_offset:source_offset + 640]
                    source_offset += len(user)
                    if source_offset == len(source):
                        source_finished_at = current
                else:
                    # 无人讲话的真实等待时间使用数字静音，供应商声音全部来自 WebSocket。
                    user = bytes(640)
                assistant = bytes(playback[:len(user)])
                del playback[:len(user)]
                assistant += bytes(len(user) - len(assistant))
                mixed = audioop.add(user, assistant, 2)
                users.extend(user)
                rendered.extend(assistant)
                mixed_all.extend(mixed)
                await socket.send(json.dumps({"type": "audio", "pcm": base64.b64encode(user).decode(), "mixed": base64.b64encode(mixed).decode(), "seq": sequence, "at_ms": frame_at * 20}))
                sequence += 1
                sent_frames += 1
                frame_at += 1
                if source_finished_at and current - source_finished_at >= 8 and not playback and current - last_audio_at >= 1:
                    break
            else:
                raise TimeoutError("真实语音验证超过限定时长")
            await socket.send(json.dumps({"type": "timeline", "kind": "stop", "at_ms": frame_at * 20, "wall_ms": (time.monotonic() - started) * 1000}))
            await socket.send(json.dumps({"type": "stop", "capture_complete": True}))

        jobs = [asyncio.create_task(download()), asyncio.create_task(upload())]
        try:
            await asyncio.wait_for(asyncio.gather(*jobs), timeout=95)
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
    if not terminal or terminal.get("type") != "done":
        raise AssertionError("公网通话未正常保存并结束")
    if first_audio_delay is None or first_audio_delay >= 20 or not source_started_at or audioop.rms(received, 2) <= 0 or audioop.rms(rendered, 2) <= 0:
        raise AssertionError("主动开场或实际合成声音验证失败")
    expected = {"user": bytes(users), "assistant": bytes(received), "conversation": bytes(mixed_all)}
    saved = {}
    for kind, pcm in expected.items():
        response = await client.get(f"/api/v1/calls/{call_id}/audio/{kind}")
        if response.status_code != 200:
            raise AssertionError("真实录音读取失败：" + kind)
        with wave.open(io.BytesIO(response.content)) as recording:
            rate = 24000 if kind == "assistant" else 16000
            if recording.getnchannels() != 1 or recording.getsampwidth() != 2 or recording.getframerate() != rate or recording.readframes(recording.getnframes()) != pcm:
                raise AssertionError("已保存录音与真实传输音频不一致：" + kind)
        saved[kind] = {"bytes": len(pcm), "rms": audioop.rms(pcm, 2), "sha256": hashlib.sha256(pcm).hexdigest()}
    return {"ready_delay_seconds": round(ready_delay, 3), "first_audio_delay_seconds": round(first_audio_delay, 3), "audio_frames_before_first_audio": 0, "capture_frames": sequence, "duration_seconds": round(len(mixed_all) / 32000, 3), "source_seconds": len(source) / 32000, "source_started_seconds": round(source_started_at - started, 3), "received_types": dict(received_types), "interrupted_playback_bytes": interrupted_bytes, "rendered_assistant_rms": audioop.rms(rendered, 2), "saved_audio": saved}


async def main() -> None:
    parser = argparse.ArgumentParser(description="真实公网 P0 主动开场、短历史录音和统一时间轴验证；只调用豆包实时语音")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--run", action="store_true", required=True, help="确认后端已发布，并授权本次限定时长的真实语音调用")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("必须提供不含路径和凭据的公网 HTTPS origin")
    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    WORK.chmod(0o700)
    run_id = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    run_dir = WORK / run_id
    run_dir.mkdir(mode=0o700)
    result = {"checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "origin": origin, "passed": False, "stage": "preflight", "retries": 0}
    account = json.loads((ROOT / ".intermediate/public-runtime/account.json").read_text())
    if not re.fullmatch(r"release-[a-f0-9]{16}", account["username"]):
        raise ValueError("仅允许使用现存普通公网验证账号")
    settings = Settings(_env_file=ROOT / "backend/.env.deploy")
    repository = CloudRepository(settings.cloudbase_env_id, settings.cloudbase_api_key)
    began = time.monotonic()
    try:
        result["idle_check"] = await asyncio.to_thread(assert_idle, repository)
        stored = await asyncio.to_thread(repository.one, "accounts", filters={"username": "eq." + account["username"]}, columns="owner_id,role,analytics_excluded")
        if not stored or stored["role"] != "member" or stored["analytics_excluded"] is not True:
            raise ValueError("验证账号必须为普通成员，且已从产品统计中排除")
        source_path = run_dir / "authorized-source.pcm"
        private_write(source_path, b"")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "600", "-i", str(ROOT / "data/real-interviews/li-01.m4a"), "-t", "8", "-ac", "1", "-ar", "16000", "-f", "s16le", str(source_path)], check=True, timeout=30)
        source = source_path.read_bytes()
        source = source[:len(source) // 640 * 640]
        if not 7 * 32000 <= len(source) <= 8 * 32000 or audioop.rms(source, 2) < 100:
            raise ValueError("授权历史录音短片段长度或真实声量无效")
        async with httpx.AsyncClient(base_url=origin, timeout=90, headers={"Origin": origin}, follow_redirects=False) as client:
            expect(await client.post("/api/v1/auth/password-login", json={"username": account["username"], "password": account["password"]}))
            me = expect(await client.get("/api/v1/me"))
            if me["owner_id"] != stored["owner_id"]:
                raise AssertionError("公网会话与验证账号归属不一致")
            profile = expect(await client.post("/api/v1/profiles", json={"elder_name": "P0验证·主动开场", "topics_want": "已授权历史录音的语音链路验证", "topics_avoid": ""}))
            result["profile_id"] = profile["id"]
            private_json(run_dir / "result.json", result)
            await asyncio.to_thread(assert_idle, repository)
            call = expect(await client.post("/api/v1/calls", json={"profile_id": profile["id"], "consent": True, "request_id": str(uuid4()), "topic": "P0验证：主动开场后，重放已授权历史录音8秒；只验证语音与保存，不进行回忆录生成"}))
            call_id = call["id"]
            result.update(call_id=call_id, stage="real_voice")
            private_json(run_dir / "result.json", result)
            print(json.dumps({"stage": "real_voice", "call_id": call_id, "source_seconds": len(source) / 32000, "maximum_capture_seconds": 55}), flush=True)
            result["voice"] = await verify_socket(client, origin, call_id, source)
            result["stage"] = "persisted_metadata"
            detail = expect(await client.get(f"/api/v1/calls/{call_id}"))
            if detail["status"] != "ready" or detail["failure_code"]:
                raise AssertionError("通话没有以成功状态保存")
            row = await asyncio.to_thread(repository.one, "call_timelines", filters={"call_id": "eq." + call_id, "owner_id": "eq." + stored["owner_id"]}, columns="payload")
            if row is None:
                raise AssertionError("统一时间轴没有持久保存")
            timeline = json.loads(build_fernet(settings.audio_fernet_key).decrypt(row["payload"].encode()))
            kinds = dict(Counter(event["kind"] for event in timeline["events"]))
            provider_events = timeline.get("provider_events", {})
            if timeline.get("opening_requested") is not True or timeline.get("first_audio_received") is not True or timeline.get("complete") is not True or timeline["frames"] != result["voice"]["capture_frames"] or not all(kinds.get(kind) for kind in ["start", "schedule", "stop"]) or not provider_events.get("response.output_audio.delta"):
                raise AssertionError("真实供应商事件或完整时间轴保存不符合要求")
            result["persisted"] = {"status": detail["status"], "text_lengths": {key: len(value) for key, value in detail["texts"].items()}, "timeline_event_kinds": kinds, "provider_events": provider_events, "first_audio_received": True, "opening_requested": True, "complete": True}
            result.update(passed=True, stage="completed", elapsed_seconds=round(time.monotonic() - began, 3), provider_model=settings.doubao_model, generated_memoir=False, retained_verification_records=True)
            evidence = ROOT / "docs/evidence/阶段4/冒烟记录.md"
            if not evidence.exists():
                raise FileNotFoundError("缺少阶段4冒烟记录文件")
            voice = result["voice"]
            with evidence.open("a", encoding="utf-8") as handle:
                handle.write(f"| {datetime.now(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M:%S} | 豆包 {settings.doubao_model}·真实公网主动开场、历史录音8秒、双方原声与统一时间轴；{result['elapsed_seconds']}秒 | {call_id} | 接口未提供 | 接口未提供 | 无上传音频时{voice['first_audio_delay_seconds']}秒收到真实开场；三路PCM逐字节一致；供应商事件与首声音状态已持久保存 | 0 | 通过 |\n")
    finally:
        private_json(run_dir / "result.json", result)
        repository.close()
    print(json.dumps({"passed": result["passed"], "call_id": result["call_id"], "first_audio_seconds": result["voice"]["first_audio_delay_seconds"], "assistant_pcm_bytes": result["voice"]["saved_audio"]["assistant"]["bytes"], "duration_seconds": result["voice"]["duration_seconds"], "voice_only": True}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
