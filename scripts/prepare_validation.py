import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import Call, Chapter, Photo, Transcript
from sqlalchemy import select

settings = get_settings()
target = ROOT / ".intermediate" / "validation"
target.mkdir(parents=True, exist_ok=True)
source_path = settings.sqlalchemy_url.removeprefix("sqlite:///")
with sqlite3.connect(source_path) as source, sqlite3.connect(target / "yitan.db") as destination:
    source.backup(destination)
shutil.copytree(settings.data_path / "files", target / "files", dirs_exist_ok=True)
with make_session_factory(make_engine(settings.sqlalchemy_url))() as db:
    chapter = db.scalar(select(Chapter).order_by(Chapter.created_at.desc()))
    assert chapter is not None, "需要已有真实访谈章节"
    call = db.get(Call, chapter.call_id)
    photo = db.scalar(select(Photo).where(Photo.owner_id == call.owner_id))
    transcripts = list(db.scalars(select(Transcript).where(Transcript.call_id == call.id)))
    assert any(row.text for row in transcripts), "需要真实访谈原话"
    metadata = {"profile_id": call.profile_id, "call_id": call.id, "chapter_id": chapter.id,
                "photo_id": photo.id if photo else None, "photo_profile_id": photo.profile_id if photo else None}
    (target / "selection.json").write_text(json.dumps(metadata), encoding="utf-8")
print("已复制真实访谈数据至隔离验证目录")
