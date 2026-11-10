import base64
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings
from app.core.crypto import build_fernet
from app.db import make_engine, make_session_factory
from app.main import create_app
from app.models import AudioAsset, Call, CallTimeline
from app.services.store import read_audio, pcm16_to_wav, transcripts_for
from app.services.timeline import TimelineCapture
from app.services.tingwu import ensure_full_transcript

WORK = ROOT / ".intermediate/timeline"


def prepare():
    settings = get_settings()
    WORK.mkdir(parents=True, exist_ok=True)
    settings = settings.model_copy(update={"database_url": "sqlite:///" + str(ROOT / ".intermediate/voice-stream/yitan.db"), "data_dir": str(ROOT / ".intermediate/voice-stream")})
    with make_session_factory(make_engine(settings.sqlalchemy_url))() as db:
        asset = db.scalar(select(AudioAsset).where(AudioAsset.kind == "assistant", AudioAsset.size_bytes >= 96000).order_by(AudioAsset.created_at.desc()))
        assert asset is not None
        call = db.get(Call, asset.call_id)
        fernet = build_fernet(settings.audio_fernet_key)
        payload = {"profile_id": call.profile_id, "owner_id": call.owner_id}
        for kind, rate in [("user", 16000), ("assistant", 24000)]:
            pcm = read_audio(db, fernet, settings.data_path, call.owner_id, call.id, kind)
            payload[kind] = base64.b64encode(pcm16_to_wav(pcm, rate)).decode()
        (WORK / "input.json").write_text(json.dumps(payload))
    print("已准备真实双音轨，仅存放在忽略目录")


def verify(transcribe=False):
    settings = get_settings()
    original = json.loads((WORK / "input.json").read_text())
    rendered = json.loads((WORK / "render.json").read_text())
    with sqlite3.connect(settings.sqlalchemy_url.removeprefix("sqlite:///")) as source, sqlite3.connect(WORK / "yitan.db") as destination:
        source.backup(destination)
    settings = settings.model_copy(update={"database_url": "sqlite:///" + str(WORK / "yitan.db"), "data_dir": str(WORK)})
    app = create_app(settings)
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"code": settings.invite_code}).raise_for_status()
        response = client.post("/api/v1/calls", json={"profile_id": original["profile_id"], "consent": True, "request_id": str(uuid4()), "topic": "真实双音轨时间轴验证"})
        response.raise_for_status()
        call_id = response.json()["id"]
        capture = TimelineCapture()
        with app.state.db_factory() as db:
            for event in rendered["events"]:
                capture.event(event)
            for frame in rendered["frames"]:
                capture.append(frame)
            capture.save(db, app.state.fernet, settings.data_path, original["owner_id"], call_id, True)
            call = db.get(Call, call_id)
            call.status = "ready"
            db.commit()
        response = client.get(f"/api/v1/calls/{call_id}/audio/playback")
        response.raise_for_status()
        assert response.headers["X-Recording-Kind"] == "conversation"
        (WORK / "conversation.wav").write_bytes(response.content)
        with TestClient(create_app(settings)) as restarted:
            restarted.post("/api/v1/auth/login", json={"code": settings.invite_code}).raise_for_status()
            assert restarted.get(f"/api/v1/calls/{call_id}/audio/conversation").content == response.content
        with app.state.db_factory() as db:
            timeline = db.get(CallTimeline, call_id)
            payload = json.loads(app.state.fernet.decrypt(timeline.payload.encode()))
            assert payload["complete"] and len(payload["events"]) == len(rendered["events"])
            assert payload["duration_ms"] == 12000
            if transcribe:
                ensure_full_transcript(db, settings, app.state.fernet, settings.data_path, original["owner_id"], call_id)
                transcript = next(row.text for row in transcripts_for(db, original["owner_id"], call_id) if row.track == "full")
                assert transcript.strip() and "这一路没有逐句时间" not in transcript
                (WORK / "transcript.txt").write_text(transcript)
                record = ROOT / "docs/evidence/阶段3-3/冒烟记录.md"
                with record.open("a") as handle:
                    handle.write(f"| {datetime.now(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M:%S} | 听悟真实双音轨/浏览器合成12秒/实际转写/不追加采访者文字 | {call_id} | 接口未提供 | 接口未提供 | 脚本实测 | 时间轴与非空逐字稿校验 | 0 | 通过 |\n")
    print("真实双音轨合成、播放/停顿/打断、加密时间轴、重启读取与授权回放通过" + ("；听悟已实际转写合成录音" if transcribe else ""))


if __name__ == "__main__":
    if sys.argv[1:] == ["prepare"]:
        prepare()
    else:
        verify(sys.argv[1:] == ["transcribe"])
