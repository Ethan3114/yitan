import argparse
import asyncio
import audioop
import base64
import hashlib
import io
import json
import secrets
import subprocess
import sys
import time
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".intermediate/public-runtime"
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.services.cloudbase import CloudRepository


def private_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


async def main():
    parser = argparse.ArgumentParser(description="通过真实公网验证语音、统一时间轴、转写和 Plus 写作")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--repeat-voice", action="store_true")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    if not origin.startswith("https://"):
        raise ValueError("必须使用公网 HTTPS 地址")
    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = Settings(_env_file=ROOT / "backend/.env.deploy")
    repository = CloudRepository(settings.cloudbase_env_id, settings.cloudbase_api_key)
    credentials_path = WORK / "account.json"
    state = json.loads(credentials_path.read_text()) if credentials_path.exists() else {"username": "release-" + uuid4().hex[:16], "password": secrets.token_urlsafe(24), "request_id": str(uuid4()), "draft_request_id": str(uuid4())}
    if args.repeat_voice:
        current = datetime.now(timezone.utc)
        calls = list(repository.rows("calls", filters={"status": "eq.live"}, columns="created_at,heartbeat_at", order="created_at.asc"))
        tasks = list(repository.rows("draft_tasks", filters={"status": "eq.running"}, columns="lease_until", order="created_at.asc"))
        if any(datetime.fromisoformat(row["heartbeat_at"] or row["created_at"]) > current - timedelta(minutes=2) for row in calls) or any(row["lease_until"] and datetime.fromisoformat(row["lease_until"]) > current for row in tasks):
            raise RuntimeError("有实际访谈或整理正在进行，本次收费验证尚未启动")
        if state.get("task_id"):
            previous = repository.one("draft_tasks", filters={"id": "eq." + state["task_id"], "owner_id": "eq." + state["owner_id"]}, columns="status")
            if not previous or previous["status"] != "succeeded":
                raise ValueError("已有任务尚未成功完成，禁止重复收费验证")
            state["previous_runs"] = [*state.get("previous_runs", []), {"call_id": state["call_id"], "task_id": state.pop("task_id")}]
            state.pop("writing_verified", None)
            state["draft_request_id"] = str(uuid4())
        state["previous_calls"] = [*state.get("previous_calls", []), *([state["call_id"]] if state.get("call_id") else [])]
        state.pop("call_id", None)
        state.pop("voice_verified", None)
        state["request_id"] = str(uuid4())
    private_json(credentials_path, state)
    checks = []
    async with httpx.AsyncClient(base_url=origin, timeout=90, headers={"Origin": origin}, follow_redirects=False) as client:
        def expect(response, status):
            if response.status_code != status:
                body = response.json()
                raise RuntimeError(f"公网验证失败 HTTP {response.status_code} code={body.get('error', {}).get('code', 'unknown')}")
            return response.json()
        if not state.get("registered"):
            admin = json.loads((ROOT / ".intermediate/private/cloud-admin.json").read_text())
            async with httpx.AsyncClient(base_url=origin, timeout=90, headers={"Origin": origin}) as manager:
                expect(await manager.post("/api/v1/auth/password-login", json={"username": admin["username"], "password": admin["password"]}), 200)
                invite = expect(await manager.post("/api/v1/admin/invitations"), 200)["invitation"]
            account = expect(await client.post("/api/v1/auth/register", json={"username": state["username"], "password": state["password"], "invitation": invite}), 200)
            state["registered"] = True
            private_json(credentials_path, state)
        else:
            expect(await client.post("/api/v1/auth/password-login", json={"username": state["username"], "password": state["password"]}), 200)
        checks.append("公网邀请码注册与密码登录")
        me = expect(await client.get("/api/v1/me"), 200)
        state["owner_id"] = me["owner_id"]
        if not state.get("profile_id"):
            profile = expect(await client.post("/api/v1/profiles", json={"elder_name": "李奶奶发布验证", "topics_want": "小时候哥哥照顾一家人的经历", "topics_avoid": ""}), 200)
            state["profile_id"] = profile["id"]
            private_json(credentials_path, state)
        if not state.get("call_id"):
            call = expect(await client.post("/api/v1/calls", json={"profile_id": state["profile_id"], "consent": True, "request_id": state["request_id"], "topic": "使用已授权真实历史录音进行公网音频链路验证"}), 200)
            state["call_id"] = call["id"]
            private_json(credentials_path, state)
        call_id = state["call_id"]
        if not state.get("voice_verified"):
            pcm_path = WORK / "source.pcm"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "600", "-i", str(ROOT / "data/real-interviews/li-01.m4a"), "-t", "65", "-ac", "1", "-ar", "16000", "-f", "s16le", str(pcm_path)], check=True)
            source_pcm = pcm_path.read_bytes()
            # 原始讲话分成真实播放片段，中间停顿让访谈者有时间回应。
            pieces = [bytes(5 * 32000)]
            for offset in range(0, len(source_pcm), 20 * 32000):
                pieces.extend([source_pcm[offset:offset + 20 * 32000], bytes(5 * 32000)])
            pcm = b"".join(pieces)
            cookie = "; ".join(f"{item.name}={item.value}" for item in client.cookies.jar)
            received, rendered = bytearray(), bytearray()
            playback = bytearray()
            mixed_all = bytearray()
            during_upload = 0
            uploading = True
            terminal = None
            ws_origin = origin.replace("https://", "wss://", 1)
            async with websockets.connect(ws_origin + f"/api/v1/calls/{call_id}/live", origin=origin, additional_headers={"Cookie": cookie}, max_size=2**20, open_timeout=90, ping_interval=15, ping_timeout=60) as socket:
                first = json.loads(await socket.recv())
                if first["type"] != "ready":
                    raise AssertionError("豆包实时通话未接通")
                print(json.dumps({"voice": "connected", "historical_audio_seconds": len(source_pcm) / 32000, "capture_seconds_including_pauses": len(pcm) / 32000}), flush=True)
                started = time.monotonic()
                await socket.send(json.dumps({"type": "timeline", "kind": "start", "at_ms": 0, "wall_ms": 0}))
                async def upload():
                    nonlocal uploading
                    for sequence, offset in enumerate(range(0, len(pcm), 3200)):
                        await asyncio.sleep(max(0, started + offset / 32000 - time.monotonic()))
                        user = pcm[offset:offset + 3200]
                        assistant = bytes(playback[:len(user)])
                        del playback[:len(user)]
                        assistant += bytes(len(user) - len(assistant))
                        mixed = audioop.add(user, assistant, 2)
                        mixed_all.extend(mixed)
                        rendered.extend(assistant)
                        await socket.send(json.dumps({"type": "audio", "pcm": base64.b64encode(user).decode(), "mixed": base64.b64encode(mixed).decode(), "seq": sequence, "at_ms": offset / 32}))
                    uploading = False
                    await socket.send(json.dumps({"type": "timeline", "kind": "stop", "at_ms": len(pcm) / 32, "wall_ms": (time.monotonic() - started) * 1000}))
                    await socket.send(json.dumps({"type": "stop", "capture_complete": True}))
                async def download():
                    nonlocal terminal, during_upload
                    resampling = None
                    async for raw in socket:
                        event = json.loads(raw)
                        if event["type"] == "audio":
                            chunk = base64.b64decode(event["pcm"])
                            received.extend(chunk)
                            converted, resampling = audioop.ratecv(chunk, 2, 1, 24000, 16000, resampling)
                            playback.extend(converted)
                            if uploading:
                                during_upload += len(chunk)
                        elif event["type"] == "interrupted":
                            playback.clear()
                            at = (time.monotonic() - started) * 1000
                            await socket.send(json.dumps({"type": "timeline", "kind": "interrupt", "at_ms": at, "wall_ms": at}))
                        elif event["type"] in {"done", "error"}:
                            terminal = event
                            return
                await asyncio.wait_for(asyncio.gather(upload(), download()), 300)
            if not terminal or terminal["type"] != "done":
                raise AssertionError("公网通话没有正常完成：" + str((terminal or {}).get("error", {}).get("code", "missing")))
            if not received or not during_upload:
                raise AssertionError("上传期间没有同时收到真实豆包音频")
            print(json.dumps({"voice": "completed", "assistant_bytes": len(received)}), flush=True)
            for kind, expected in [("user", pcm), ("assistant", bytes(received)), ("conversation", bytes(mixed_all))]:
                response = await client.get(f"/api/v1/calls/{call_id}/audio/{kind}")
                if response.status_code != 200:
                    raise AssertionError("公网录音读取失败")
                with wave.open(io.BytesIO(response.content)) as recording:
                    if recording.readframes(recording.getnframes()) != expected:
                        raise AssertionError("公网保存录音与真实上下行音频不一致")
            state.update(voice_verified=True, source_sha256=hashlib.sha256(pcm).hexdigest(), duration_seconds=len(pcm) / 32000, assistant_received_bytes=len(received))
            private_json(credentials_path, state)
            checks.extend(["真实历史录音65秒按实际速度传输", "上传期间收到真实豆包语音", "用户与豆包原声音轨逐字节一致", "同一时间轴合成录音逐字节一致"])
        if args.generate:
            if not state.get("task_id"):
                task = expect(await client.post(f"/api/v1/calls/{call_id}/draft-tasks", json={"request_id": state["draft_request_id"]}), 202)
                state["task_id"] = task["id"]
                private_json(credentials_path, state)
            deadline = time.monotonic() + 2400
            last = None
            while time.monotonic() < deadline:
                task = expect(await client.get("/api/v1/draft-tasks/" + state["task_id"]), 200)
                status = (task["status"], task["step"])
                if status != last:
                    print(json.dumps({"task_status": task["status"], "step": task["step"]}), flush=True)
                    last = status
                if task["status"] == "failed":
                    raise AssertionError("真实云端生成失败：" + task["error_code"])
                if task["status"] == "succeeded":
                    break
                await asyncio.sleep(10)
            else:
                raise TimeoutError("生成观察超时，任务记录继续保留")
            profile = expect(await client.get("/api/v1/profiles/" + state["profile_id"]), 200)
            call = next(item for item in profile["calls"] if item["id"] == call_id)
            if not call["texts"].get("full") or not call["chapters"]:
                raise AssertionError("真实转写或回忆录尚未保存")
            if any(not p["source_quotes"] for c in call["chapters"] for p in c["paragraphs"]):
                raise AssertionError("生成正文缺少原文证据")
            checks.extend(["通义听悟真实转写完成", "Qwen Plus真实写作及逐句原文证据保存"])
            state["writing_verified"] = True
            private_json(credentials_path, state)
        private_json(WORK / "result.json", {"checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "origin": origin, "checks": checks, "voice_verified": state.get("voice_verified", False), "writing_verified": state.get("writing_verified", False), "test_account_retained_for_verification": True})
        if state.get("writing_verified"):
            models = list(repository.rows("writing_cache", filters={"owner_id": "eq." + state["owner_id"], "call_id": "eq." + call_id, "status": "eq.completed"}, columns="usage", order="created_at.asc"))
            input_tokens = sum(item["usage"].get("prompt_tokens", 0) for item in models)
            output_tokens = sum(item["usage"].get("completion_tokens", 0) for item in models)
            evidence = ROOT / "docs/evidence/阶段4/冒烟记录.md"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            if not evidence.exists():
                evidence.write_text("# 冒烟记录\n- 模型：豆包实时语音、通义听悟、qwen3.7-plus 思考模式\n\n| 时间 | 链路 | 请求 ID | 输入 token | 输出 token | 结构校验 | 重试次数 | 结果 |\n| --- | --- | --- | --- | --- | --- | --- | --- |\n", encoding="utf-8")
            with evidence.open("a", encoding="utf-8") as handle:
                handle.write(f"| {datetime.now(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M:%S} | 真实公网65秒历史录音、双方原声、统一时间轴、听悟转写、Plus成稿 | {state['task_id']} | {input_tokens} | {output_tokens} | 全部音轨逐字节一致、逐句证据已保存 | 0 | 通过 |\n")
        print(json.dumps({"passed": len(checks), "voice_verified": state.get("voice_verified", False), "writing_verified": state.get("writing_verified", False)}), flush=True)
    repository.close()


if __name__ == "__main__":
    asyncio.run(main())
