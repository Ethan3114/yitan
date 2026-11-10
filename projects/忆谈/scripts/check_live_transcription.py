import json
import shutil
import sys
import time
import io
import wave
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings
from app.core.crypto import build_fernet
from app.db import make_engine, make_session_factory
from app.models import CallTimeline
from app.services.store import read_audio, transcripts_for
from app.services.timeline import TimelineCapture
from app.services.tingwu import ensure_full_transcript

selection = json.loads((ROOT / ".intermediate/timeline/live.json").read_text())
work = ROOT / ".intermediate/voice-stream"
settings = get_settings().model_copy(update={"database_url": "sqlite:///" + str(work / "yitan.db"), "data_dir": str(work)})
fernet = build_fernet(settings.audio_fernet_key)
started = time.monotonic()
with make_session_factory(make_engine(settings.sqlalchemy_url))() as db:
    capture = TimelineCapture()
    for frame in selection["frames"]:
        capture.append(frame)
    saved = read_audio(db, fernet, work, selection["owner_id"], selection["call_id"], "conversation")
    assert saved == bytes(capture.pcm)
    with httpx.Client(base_url="http://127.0.0.1:8093") as client:
        client.post("/api/v1/auth/login", json={"code": settings.invite_code}).raise_for_status()
        audio = client.get(f"/api/v1/calls/{selection['call_id']}/audio/playback")
        audio.raise_for_status()
        assert audio.headers["X-Recording-Kind"] == "conversation"
        with wave.open(io.BytesIO(audio.content)) as recording:
            assert recording.readframes(recording.getnframes()) == saved
        client.post("/api/v1/auth/logout").raise_for_status()
        assert client.get(f"/api/v1/calls/{selection['call_id']}/audio/conversation").status_code == 401
    row = db.get(CallTimeline, selection["call_id"])
    timeline = json.loads(fernet.decrypt(row.payload.encode()))
    assert timeline["complete"]
    assert {"start", "schedule", "stop"} <= {event["kind"] for event in timeline["events"]}
    ensure_full_transcript(db, settings, fernet, work, selection["owner_id"], selection["call_id"])
    full = next(row.text for row in transcripts_for(db, selection["owner_id"], selection["call_id"]) if row.track == "full")
    assert full.strip() and "这一路没有逐句时间" not in full
    assert next(row.text for row in transcripts_for(db, selection["owner_id"], selection["call_id"]) if row.track == "transcription_source") == "conversation"
    (ROOT / ".intermediate/timeline/live-transcript.txt").write_text(full)
record = ROOT / "docs/evidence/阶段3-3/冒烟记录.md"
now = datetime.now(ZoneInfo("Asia/Shanghai"))
if record.exists():
    archive = ROOT / "data/writing/smoke-history"
    archive.mkdir(exist_ok=True)
    shutil.copy2(record, archive / f"timeline-{now:%Y%m%d-%H%M%S}.md")
record.write_text(f"# 冒烟记录（脚本自动生成）\n- 模型：豆包实时语音 1.2.6.1、通义听悟\n\n| 时间 | 链路 | 请求 ID | 输入 token | 输出 token | 耗时 | 结构校验 | 重试次数 | 结果 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n| {now:%Y-%m-%d %H:%M:%S} | 浏览器真实历史原声重放/实时豆包回答/实际播放合成/听悟转写合成录音 | {selection['call_id']} | 接口未提供 | 接口未提供 | 转写与读取 {time.monotonic() - started:.2f}s | 采样逐字节一致/事件保存/逐字稿非空/未重复追加 AI 文字 | 0 | 通过 |\n")
print(f"真实浏览器合成录音与服务端逐字节一致，时间轴完整；听悟转写完成，{len(full)} 字，产物保存在忽略目录。")
