import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.family import decode, encode, lock
from app.api.http import current_owner, db_session
from app.core.errors import error_response
from app.models import Chapter, DraftTask, Paragraph, ParagraphEvidence, TextEdition, WritingRun
from app.services import store
from app.services.draft import chapter_views

router = APIRouter(prefix="/api/v1")


class RestoreIn(BaseModel):
    confirm: bool
    request_id: str = Field(min_length=16, max_length=80)


def historical(request, db, owner, call_id):
    editions = []
    for row in db.scalars(select(TextEdition).where(TextEdition.owner_id == owner, TextEdition.call_id == call_id).order_by(TextEdition.created_at.desc())):
        editions.append({"id": row.id, "created_at": row.created_at.isoformat(), "chapters": decode(request, row.payload)})
    for run in db.scalars(select(WritingRun).where(WritingRun.owner_id == owner, WritingRun.call_id == call_id, WritingRun.status == "completed").order_by(WritingRun.created_at.desc())):
        trace = json.loads(request.app.state.fernet.decrypt(Path(run.trace_path).read_bytes()))
        previous = next((s["data"] for s in trace["steps"] if s["stage"] == "previous_edition"), [])
        if previous:
            editions.append({"id": run.id + ":previous", "created_at": run.created_at.isoformat(), "chapters": previous})
        accepted = next((s["data"] for s in trace["steps"] if s["stage"] == "accepted"), None)
        if accepted:
            chapters = [{"title": c["title"], "paragraphs": [{"text": p["text"], "source_quote": p["source_quote"], "source_quotes": p.get("evidence", [])} for p in c["paragraphs"]]} for c in accepted["chapters"]]
            editions.append({"id": run.id + ":result", "created_at": run.created_at.isoformat(), "chapters": chapters})
    return editions


@router.get("/calls/{call_id}/editions")
def editions(call_id: str, request: Request, db: Session = Depends(db_session), owner=Depends(current_owner)):
    if not store.get_call(db, owner, call_id):
        return error_response(404, "not_found", "找不到这次访谈")
    return {"items": historical(request, db, owner, call_id)}


@router.post("/calls/{call_id}/editions/{edition_id}/restore")
def restore(call_id: str, edition_id: str, body: RestoreIn, request: Request, db: Session = Depends(db_session), owner=Depends(current_owner)):
    if not body.confirm:
        return error_response(400, "confirmation_required", "请先确认恢复这一版本")
    if not store.get_call(db, owner, call_id):
        return error_response(404, "not_found", "找不到这次访谈")
    lock(db)
    key = f"{call_id}:{edition_id}:{body.request_id}"
    if db.scalar(select(TextEdition).where(TextEdition.request_id == key)):
        return {"ok": True}
    if db.scalar(select(DraftTask).where(DraftTask.call_id == call_id, DraftTask.status.in_(["queued", "running"]))):
        return error_response(409, "writing_active", "请等当前整理结束后恢复")
    edition = next((e for e in historical(request, db, owner, call_id) if e["id"] == edition_id), None)
    if edition is None:
        return error_response(404, "not_found", "找不到这个版本")
    current = chapter_views(db, owner, call_id)
    db.add(TextEdition(id=store.new_id(), owner_id=owner, call_id=call_id, request_id=key, payload=encode(request, current)))
    ids = [c["id"] for c in current]
    paragraphs = select(Paragraph.id).where(Paragraph.chapter_id.in_(ids))
    db.execute(delete(ParagraphEvidence).where(ParagraphEvidence.paragraph_id.in_(paragraphs)))
    db.execute(delete(Paragraph).where(Paragraph.chapter_id.in_(ids)))
    db.execute(delete(Chapter).where(Chapter.id.in_(ids)))
    for saved in edition["chapters"]:
        chapter = Chapter(id=store.new_id(), owner_id=owner, call_id=call_id, title=saved["title"])
        db.add(chapter)
        db.flush()
        for position, item in enumerate(saved["paragraphs"]):
            paragraph = Paragraph(id=store.new_id(), owner_id=owner, chapter_id=chapter.id, position=position, text=item["text"], source_quote=item["source_quote"], score=item.get("score"))
            db.add(paragraph)
            db.flush()
            for ref in item.get("source_quotes", []):
                db.add(ParagraphEvidence(id=store.new_id(), owner_id=owner, paragraph_id=paragraph.id, source_line=ref["line"], quote=ref["quote"]))
    db.commit()
    return {"ok": True}
