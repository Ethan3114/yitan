"""第二次开场用的上一章摘要。不新叫模型。"""

from sqlalchemy.orm import Session

from app.services.draft import chapter_view
from app.services.redact import redact_sensitive
from app.services.store import list_calls


def opening_line(title: str, excerpt: str) -> str:
    body = redact_sensitive(excerpt).strip()
    name = (title or "").strip()
    if not body and not name:
        return ""
    remembered = body[:80]
    if name and remembered:
        return f"上次您讲过{name}。我记得您说，{remembered}。今天接着聊。"
    if remembered:
        return f"上次您讲过这些。我记得您说，{remembered}。今天接着聊。"
    return f"上次您讲过{name}。今天接着聊。"


def latest_opening(db: Session, owner_id: str, profile_id: str, current_call_id: str) -> str:
    for call in list_calls(db, owner_id, profile_id):
        if call.id == current_call_id:
            continue
        view = chapter_view(db, owner_id, call.id)
        if not view or not view["paragraphs"]:
            continue
        excerpt = "".join(item["text"] for item in view["paragraphs"])
        return opening_line(view["title"], excerpt)
    return ""
