"""通话结束时把声音和文字留下。"""

from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.models import Call
from app.services.doubao import VoiceError, transcribe_pcm
from app.services.redact import redact_sensitive
from app.services.store import finish_call, save_audio, save_transcript


async def persist_call(
    db: Session,
    fernet: Fernet,
    data_dir: Path,
    call: Call,
    user_pcm: bytes,
    assistant_pcm: bytes,
    user_text: str,
    assistant_text: str,
    side_text: str | None,
    failure: VoiceError | None,
) -> None:
    save_audio(db, fernet, data_dir, call.owner_id, call.id, "user", user_pcm)
    save_audio(db, fernet, data_dir, call.owner_id, call.id, "assistant", assistant_pcm)
    if user_text:
        save_transcript(db, call.owner_id, call.id, "realtime_user", redact_sensitive(user_text))
    if assistant_text:
        save_transcript(db, call.owner_id, call.id, "realtime_assistant", redact_sensitive(assistant_text))
    if side_text:
        save_transcript(db, call.owner_id, call.id, "side", redact_sensitive(side_text))
    if failure is not None:
        finish_call(db, call, "failed", failure.code)
    else:
        finish_call(db, call, "ready")
