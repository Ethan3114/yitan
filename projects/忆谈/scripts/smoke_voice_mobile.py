import asyncio
import base64
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings
from app.core.crypto import build_fernet
from app.db import make_engine, make_session_factory
from app.services.store import COOKIE, read_audio


async def main():
    settings = get_settings()
    selection = json.loads((ROOT / ".intermediate/validation/selection.json").read_text())
    base = "http://127.0.0.1:3012"
    started = time.monotonic()
    async with httpx.AsyncClient(base_url=base, timeout=60) as client:
        (await client.post("/api/v1/auth/login", json={"code": settings.invite_code})).raise_for_status()
        owner = (await client.get("/api/v1/me")).json()["owner_id"]
        with make_session_factory(make_engine(settings.sqlalchemy_url))() as db:
            pcm = read_audio(db, build_fernet(settings.audio_fernet_key), settings.data_path, owner, selection["call_id"], "user")
        assert pcm and len(pcm) >= 16000 * 2 * 30
        pcm = pcm[:16000 * 2 * 30]
        body = {"profile_id": selection["profile_id"], "consent": True, "request_id": f"voice-{time.time_ns()}", "topic": "回顾已经讲过的往事"}
        opened = await client.post("/api/v1/calls", json=body)
        opened.raise_for_status()
        call_id = opened.json()["id"]
        duplicate = await client.post("/api/v1/calls", json=body)
        assert duplicate.json()["id"] == call_id
        audio_bytes = 0
        terminal = None
        async with websockets.connect(f"ws://127.0.0.1:3012/api/v1/calls/{call_id}/live", additional_headers={"Cookie": f"{COOKIE}={client.cookies.get(COOKIE)}"}, max_size=8 * 1024 * 1024) as socket:
            ready = json.loads(await asyncio.wait_for(socket.recv(), 30))
            assert ready["type"] == "ready", ready.get("error", {}).get("code")

            async def send():
                for offset in range(0, len(pcm), 3200):
                    await socket.send(json.dumps({"type": "audio", "pcm": base64.b64encode(pcm[offset:offset + 3200]).decode()}))
                    await asyncio.sleep(0.1)
                await socket.send(json.dumps({"type": "stop"}))

            async def receive():
                nonlocal audio_bytes, terminal
                async for raw in socket:
                    event = json.loads(raw)
                    if event["type"] == "audio":
                        audio_bytes += len(base64.b64decode(event["pcm"]))
                    if event["type"] in {"done", "error"}:
                        terminal = event
                        return

            await asyncio.wait_for(asyncio.gather(send(), receive()), 120)
        assert terminal and terminal["type"] == "done", terminal
        audio = await client.get(f"/api/v1/calls/{call_id}/audio/user")
        audio.raise_for_status()
        assert len(audio.content) >= len(pcm)
        response = await client.get(f"/api/v1/calls/{call_id}/audio/assistant")
        response.raise_for_status()
        assert audio_bytes > 0 and len(response.content) > 44
        detail = (await client.get(f"/api/v1/calls/{call_id}")).json()
        assert detail["status"] == "ready"
        task_response = await client.post(f"/api/v1/calls/{call_id}/draft-tasks", json={"request_id": f"voice-draft-{time.time_ns()}"})
        task_response.raise_for_status()
        task_id = task_response.json()["id"]
        for _ in range(240):
            task = (await client.get(f"/api/v1/draft-tasks/{task_id}")).json()
            if task["status"] in {"succeeded", "failed"}:
                break
            await asyncio.sleep(2)
        assert task["status"] == "succeeded", task.get("error_code")
        detail = (await client.get(f"/api/v1/calls/{call_id}")).json()
        assert detail["texts"].get("full"), "需要通义听悟生成真实全量稿"
        selection["voice_call_id"] = call_id
        (ROOT / ".intermediate/validation/selection.json").write_text(json.dumps(selection), encoding="utf-8")
    record = ROOT / "docs/evidence/阶段3-1/冒烟记录.md"
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    with record.open("a", encoding="utf-8") as handle:
        handle.write(f"| {now:%Y-%m-%d %H:%M:%S} | 豆包实时语音、通义听悟、DeepSeek·真实历史录音30秒重放 | {call_id} | 接口未汇总 | 接口未汇总 | {time.monotonic() - started:.2f}s | ready/audio/done、录音保存、全量稿与章节任务 | 0 | 通过 |\n")
    print("真实历史录音重放通过：同源 WebSocket、实时回答声音、两路录音保存、重复创建保护、听悟全量转写与 DeepSeek 章节；实体手机麦克风另行验收")


if __name__ == "__main__":
    asyncio.run(main())
