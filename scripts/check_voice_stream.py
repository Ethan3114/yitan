import asyncio
import base64
import io
import json
import sqlite3
import subprocess
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import uvicorn
import websockets
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings
from app.core.crypto import build_fernet
from app.db import make_engine, make_session_factory
from app.main import create_app
from app.models import AudioAsset, Call
from app.services.store import COOKIE, read_audio

WORK = ROOT / ".intermediate/voice-stream"


async def check():
    settings = get_settings()
    WORK.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.sqlalchemy_url.removeprefix("sqlite:///")) as source, sqlite3.connect(WORK / "yitan.db") as target:
        source.backup(target)
    with make_session_factory(make_engine(settings.sqlalchemy_url))() as db:
        asset = db.scalar(select(AudioAsset).where(AudioAsset.kind == "user", AudioAsset.size_bytes >= 960000).order_by(AudioAsset.created_at))
        assert asset is not None, "需要至少 30 秒的真实历史录音"
        original = db.get(Call, asset.call_id)
        profile_id = original.profile_id
        pcm = read_audio(db, build_fernet(settings.audio_fernet_key), settings.data_path, asset.owner_id, asset.call_id, "user")[:960000]
    log = (WORK / "server.log").open("w")
    process = subprocess.Popen([sys.executable, __file__, "serve"], cwd=ROOT / "backend", stdout=log, stderr=log)
    started = time.monotonic()
    try:
        await asyncio.sleep(2)
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8093", timeout=30) as client:
            (await client.post("/api/v1/auth/login", json={"code": settings.invite_code})).raise_for_status()
            opened = await client.post("/api/v1/calls", json={"profile_id": profile_id, "consent": True, "request_id": str(uuid4()), "topic": "真实历史录音的实时传输验证"})
            opened.raise_for_status()
            call_id = opened.json()["id"]
            received = bytearray()
            uploading = True
            during_upload = 0
            terminal = None
            async with websockets.connect(f"ws://127.0.0.1:8093/api/v1/calls/{call_id}/live", additional_headers={"Cookie": f"{COOKIE}={client.cookies.get(COOKIE)}"}) as socket:
                assert json.loads(await socket.recv())["type"] == "ready"
                upload_start = time.monotonic()

                async def upload():
                    nonlocal uploading
                    for offset in range(0, len(pcm), 640):
                        await asyncio.sleep(max(0, upload_start + offset / 32000 - time.monotonic()))
                        await socket.send(json.dumps({"type": "audio", "pcm": base64.b64encode(pcm[offset:offset + 640]).decode()}))
                    uploading = False
                    await socket.send(json.dumps({"type": "stop"}))

                async def download():
                    nonlocal terminal, during_upload
                    async for raw in socket:
                        event = json.loads(raw)
                        if event["type"] == "audio":
                            chunk = base64.b64decode(event["pcm"])
                            received.extend(chunk)
                            if uploading:
                                during_upload += len(chunk)
                        if event["type"] in {"done", "error"}:
                            terminal = event["type"]
                            return

                await asyncio.wait_for(asyncio.gather(upload(), download()), 100)
            assert terminal == "done", f"通话未正常结束：{terminal}；检查 .intermediate/voice-stream/server.log"
            user = await client.get(f"/api/v1/calls/{call_id}/audio/user")
            assistant = await client.get(f"/api/v1/calls/{call_id}/audio/assistant")
            user.raise_for_status()
            assistant.raise_for_status()
            with wave.open(io.BytesIO(user.content)) as recording:
                assert recording.readframes(recording.getnframes()) == pcm
            with wave.open(io.BytesIO(assistant.content)) as recording:
                assert recording.readframes(recording.getnframes()) == received
            assert received and during_upload > 0, "需要实际验证上传录音期间同时收到 AI 音频"
            elapsed = time.monotonic() - upload_start
            record = ROOT / "docs/evidence/阶段3-3/冒烟记录.md"
            if not record.exists():
                record.write_text(f"# 冒烟记录（脚本自动生成）\n- 模型：豆包实时语音 / {settings.doubao_model}\n\n| 时间 | 链路 | 请求 ID | 输入 token | 输出 token | 总耗时 | 结构校验 | 重试次数 | 结果 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
            with record.open("a") as handle:
                handle.write(f"| {datetime.now(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M:%S} | 真实历史录音30秒/独立上下行/同时收发/两路录音逐字节校验 | {call_id} | 接口未提供 | 接口未提供 | {time.monotonic() - started:.2f}s | ready/audio/done，PCM一致 | 0 | 通过 |\n")
            print(f"真实豆包验证通过：30 秒录音完整保存；实时接收与保存的 AI 音频逐字节一致；上传期间收到 {during_upload} 字节；通话处理 {elapsed:.2f} 秒。未生成回忆录。")
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["serve"]:
        settings = get_settings().model_copy(update={"database_url": "sqlite:///" + str(WORK / "yitan.db"), "data_dir": str(WORK)})
        uvicorn.run(create_app(settings), host="127.0.0.1", port=8093, access_log=False)
    else:
        asyncio.run(check())
