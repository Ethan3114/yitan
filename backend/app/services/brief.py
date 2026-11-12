from dataclasses import dataclass
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AudioAsset
from app.services.draft import chapter_view
from app.services.redact import redact_sensitive
from app.services.store import list_calls

RETURNING_OPENING = "今天咱们接着慢慢聊。"


@dataclass(frozen=True)
class OpeningBrief:
    spoken: str = ""
    context: str = ""


def callback_line(title: str) -> str:
    name = redact_sensitive(title).strip().strip("《》")
    sensitive = any(term in name for term in ("银行卡", "卡号", "身份证", "密码", "验证码", "手机号", "电话号码", "住址", "病历"))
    if not name or len(name) > 24 or name in {"本章草稿", "这段回忆"} or sensitive or re.search(r"\d{6,}", name) or any(char in name for char in "。！？?!；;\n\r"):
        return RETURNING_OPENING
    return f"上次您聊到{name}。今天咱们接着慢慢聊。"


def opening_line(title: str, excerpt: str) -> str:
    body = redact_sensitive(excerpt).strip()
    name = redact_sensitive(title).strip()
    if not body and not name:
        return ""
    remembered = body[:80]
    if name and remembered:
        return f"上次您讲过{name}。我记得您说，{remembered}。今天接着聊。"
    if remembered:
        return f"上次您讲过这些。我记得您说，{remembered}。今天接着聊。"
    return f"上次您讲过{name}。今天接着聊。"


def latest_opening(db: Session, owner_id: str, profile_id: str, current_call_id: str) -> OpeningBrief:
    calls = sorted(list_calls(db, owner_id, profile_id), key=lambda call: (call.created_at, call.id), reverse=True)
    for call in calls:
        if call.id == current_call_id or call.status == "live":
            continue
        view = chapter_view(db, owner_id, call.id)
        if view and view["paragraphs"]:
            excerpt = "".join(item["text"] for item in view["paragraphs"])
            return OpeningBrief(callback_line(view["title"]), opening_line(view["title"], excerpt))
        audio = db.scalar(select(AudioAsset.id).where(AudioAsset.owner_id == owner_id, AudioAsset.call_id == call.id, AudioAsset.size_bytes > 0).limit(1))
        if audio:
            return OpeningBrief(RETURNING_OPENING)
    return OpeningBrief()


def latest_cloud_opening(repository, owner_id: str, profile_id: str, current_call_id: str) -> OpeningBrief:
    calls = repository.rows("calls", filters={"owner_id": "eq." + owner_id, "profile_id": "eq." + profile_id, "id": "neq." + current_call_id, "status": "neq.live"}, order="created_at.desc,id.desc")
    for call in calls:
        chapter = next(repository.rows("chapters", filters={"owner_id": "eq." + owner_id, "call_id": "eq." + call["id"]}, columns="id,title", order="created_at.desc,id.desc", page_size=1), None)
        if chapter:
            paragraphs = list(repository.rows("paragraphs", filters={"owner_id": "eq." + owner_id, "chapter_id": "eq." + chapter["id"]}, columns="text", order="position.asc"))
            if paragraphs:
                excerpt = "".join(item["text"] for item in paragraphs)
                return OpeningBrief(callback_line(chapter["title"]), opening_line(chapter["title"], excerpt))
        audio = next(repository.rows("audio_assets", filters={"owner_id": "eq." + owner_id, "call_id": "eq." + call["id"], "size_bytes": "gt.0"}, columns="id", page_size=1), None)
        if audio:
            return OpeningBrief(RETURNING_OPENING)
    return OpeningBrief()
