import base64
import json
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import CallTimeline
from app.services.store import save_audio


class TimelineEvent(BaseModel):
    kind: Literal["start", "schedule", "interrupt", "suspend", "resume", "stop", "ended", "disconnect"]
    at_ms: float = Field(ge=0, le=14400000)
    wall_ms: float = Field(ge=0, le=14400000)
    duration_ms: float = Field(default=0, ge=0, le=14400000)
    audio_id: int | None = Field(default=None, ge=0)


class CaptureFrame(BaseModel):
    seq: int = Field(ge=0)
    at_ms: float = Field(ge=0, le=14400000)
    mixed: str = Field(max_length=32000)


class TimelineCapture:
    def __init__(self):
        self.pcm = bytearray()
        self.events = []
        self.next_seq = 0

    def append(self, payload: dict):
        frame = CaptureFrame.model_validate(payload)
        if frame.seq != self.next_seq:
            raise ValueError("录音时间轴分片顺序不连续")
        chunk = base64.b64decode(frame.mixed, validate=True)
        if not chunk or len(chunk) % 2 or len(chunk) > 16000:
            raise ValueError("录音分片长度无效")
        offset = round(frame.at_ms * 16) * 2
        if offset < len(self.pcm):
            raise ValueError("录音时间轴重叠")
        self.pcm.extend(bytes(offset - len(self.pcm)))
        self.pcm.extend(chunk)
        self.next_seq += 1

    def event(self, payload: dict):
        self.events.append(TimelineEvent.model_validate(payload).model_dump())

    def save(self, db, fernet, data_dir, owner_id, call_id, complete=False):
        if not self.next_seq:
            return
        save_audio(db, fernet, data_dir, owner_id, call_id, "conversation", bytes(self.pcm))
        row = db.scalar(select(CallTimeline).where(CallTimeline.call_id == call_id, CallTimeline.owner_id == owner_id))
        if row is None:
            row = CallTimeline(call_id=call_id, owner_id=owner_id, payload="")
            db.add(row)
        payload = {"version": 1, "clock": "browser_audio_render", "sample_rate": 16000, "frames": self.next_seq, "duration_ms": len(self.pcm) / 32, "complete": complete, "events": self.events}
        row.payload = fernet.encrypt(json.dumps(payload).encode()).decode()
        db.commit()
