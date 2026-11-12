"""档案、通话和文件的读写。查询都带 owner_id。"""

import uuid
import io
import wave
from PIL import Image, UnidentifiedImageError
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_bytes, encrypt_bytes
from app.services.analytics import server_event
from app.models import Account, AudioAsset, AuditLog, Call, Owner, Photo, Profile, SessionRow, Transcript, utcnow

COOKIE = "yitan_session"
MAX_PHOTO = 10 * 1024 * 1024
PHOTO_TYPES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp",
}


def new_id() -> str:
    return str(uuid.uuid4())


def sniff_image(data: bytes) -> str | None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 25000000:
                return None
            kind = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(image.format)
            image.verify()
            return kind
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return None


def safe_original_name(name: str) -> str:
    base = Path(name or "photo").name.replace("\x00", "")
    base = base.strip() or "photo"
    return base[:80]


def login_owner(db: Session, days: int) -> SessionRow:
    owner = db.scalar(select(Owner).limit(1))
    if owner is None:
        owner = Owner(id=new_id())
        db.add(owner)
        db.flush()
    row = SessionRow(
        id=new_id(),
        owner_id=owner.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=days),
    )
    db.add(row)
    db.commit()
    return row


def owner_from_session(db: Session, session_id: str | None) -> str | None:
    if not session_id:
        return None
    row = db.get(SessionRow, session_id)
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return None
    account = db.scalar(select(Account).where(Account.owner_id == row.owner_id))
    if account is not None and not account.enabled:
        return None
    return row.owner_id


def delete_session(db: Session, session_id: str | None) -> None:
    if not session_id:
        return
    row = db.get(SessionRow, session_id)
    if row is not None:
        db.delete(row)
        db.commit()


def add_audit(db: Session, owner_id: str, action: str, result: str) -> None:
    db.add(AuditLog(id=new_id(), owner_id=owner_id, action=action, result=result))
    db.commit()


def create_profile(db: Session, owner_id: str, fields: dict) -> Profile:
    row = Profile(id=new_id(), owner_id=owner_id, **fields)
    db.add(row)
    server_event(db, "profile_created", owner_id, row.id)
    db.commit()
    db.refresh(row)
    return row


def list_profiles(db: Session, owner_id: str) -> list[Profile]:
    return list(db.scalars(select(Profile).where(Profile.owner_id == owner_id).order_by(Profile.created_at.desc())))


def get_profile(db: Session, owner_id: str, profile_id: str) -> Profile | None:
    row = db.get(Profile, profile_id)
    if row is None or row.owner_id != owner_id:
        return None
    return row


def save_photo(db: Session, fernet: Fernet, data_dir: Path, owner_id: str, profile_id: str, original: str, data: bytes, content_type: str) -> Photo:
    rel = f"files/{new_id()}.bin"
    (data_dir / rel).write_bytes(encrypt_bytes(fernet, data))
    row = Photo(
        id=new_id(),
        owner_id=owner_id,
        profile_id=profile_id,
        original_name=safe_original_name(original),
        content_type=content_type,
        size_bytes=len(data),
        relpath=rel,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_call(db: Session, owner_id: str, profile_id: str, *, commit: bool = True) -> Call:
    row = Call(id=new_id(), owner_id=owner_id, profile_id=profile_id, status="live")
    db.add(row)
    db.add(AuditLog(id=new_id(), owner_id=owner_id, action="consent", result="granted"))
    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(row)
    return row


def get_call(db: Session, owner_id: str, call_id: str) -> Call | None:
    row = db.get(Call, call_id)
    if row is None or row.owner_id != owner_id:
        return None
    return row


def list_calls(db: Session, owner_id: str, profile_id: str) -> list[Call]:
    return list(
        db.scalars(
            select(Call).where(Call.owner_id == owner_id, Call.profile_id == profile_id).order_by(Call.created_at.desc())
        )
    )


def save_audio(db: Session, fernet: Fernet, data_dir: Path, owner_id: str, call_id: str, kind: str, pcm: bytes) -> AudioAsset | None:
    if not pcm:
        return None
    rel = f"files/{call_id}-{kind}.bin"
    blob = encrypt_bytes(fernet, pcm)
    (data_dir / rel).write_bytes(blob)
    row = db.scalar(select(AudioAsset).where(AudioAsset.owner_id == owner_id, AudioAsset.call_id == call_id, AudioAsset.kind == kind))
    if row is None:
        row = AudioAsset(id=new_id(), owner_id=owner_id, call_id=call_id, kind=kind, relpath=rel, size_bytes=len(pcm))
        db.add(row)
    row.size_bytes = len(pcm)
    db.commit()
    return row


def read_audio(db: Session, fernet: Fernet, data_dir: Path, owner_id: str, call_id: str, kind: str) -> bytes | None:
    row = db.scalar(
        select(AudioAsset).where(AudioAsset.owner_id == owner_id, AudioAsset.call_id == call_id, AudioAsset.kind == kind)
    )
    if row is None:
        return None
    return decrypt_bytes(fernet, (data_dir / row.relpath).read_bytes())


def save_transcript(db: Session, owner_id: str, call_id: str, track: str, text: str) -> None:
    db.add(Transcript(id=new_id(), owner_id=owner_id, call_id=call_id, track=track, text=text))
    db.commit()


def replace_transcript(db: Session, owner_id: str, call_id: str, track: str, text: str) -> None:
    db.execute(
        delete(Transcript).where(
            Transcript.owner_id == owner_id,
            Transcript.call_id == call_id,
            Transcript.track == track,
        )
    )
    save_transcript(db, owner_id, call_id, track, text)


def transcripts_for(db: Session, owner_id: str, call_id: str) -> list[Transcript]:
    return list(
        db.scalars(select(Transcript).where(Transcript.owner_id == owner_id, Transcript.call_id == call_id))
    )


def finish_call(db: Session, call: Call, status: str, failure_code: str = "") -> None:
    call.status = status
    call.failure_code = failure_code
    call.ended_at = utcnow()
    db.commit()


def pcm16_to_wav(pcm: bytes, rate: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(pcm)
    return output.getvalue()
