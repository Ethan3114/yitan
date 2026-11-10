from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.http import current_owner, db_session
from app.core.crypto import decrypt_bytes
from app.core.errors import error_response
from app.models import AudioAsset, BookSettings, Chapter, DraftTask, Photo
from app.schemas import BookIn, DraftTaskIn, ProfileIn
from app.services import store
from app.services.draft import chapter_views
from app.services.tasks import launch_task, task_view

router = APIRouter(prefix="/api/v1")


@router.patch("/profiles/{profile_id}")
def edit_profile(profile_id: str, body: ProfileIn, db: Session = Depends(db_session), owner=Depends(current_owner)):
    row = store.get_profile(db, owner, profile_id)
    if row is None:
        return error_response(404, "not_found", "找不到这份档案")
    for name, value in body.model_dump().items():
        setattr(row, name, value)
    db.commit()
    return {"id": row.id}


@router.put("/profiles/{profile_id}/book")
def edit_book(profile_id: str, body: BookIn, db: Session = Depends(db_session), owner=Depends(current_owner)):
    if store.get_profile(db, owner, profile_id) is None:
        return error_response(404, "not_found", "找不到这本回忆录")
    if not body.title.strip():
        return error_response(400, "invalid_input", "请填写书名")
    row = db.get(BookSettings, profile_id)
    if row is None:
        row = BookSettings(profile_id=profile_id, title=body.title.strip())
        db.add(row)
    row.title = body.title.strip()
    db.commit()
    return {"title": row.title}


@router.get("/profiles/{profile_id}/photos")
def photos(profile_id: str, db: Session = Depends(db_session), owner=Depends(current_owner)):
    if store.get_profile(db, owner, profile_id) is None:
        return error_response(404, "not_found", "找不到这份档案")
    rows = db.scalars(select(Photo).where(Photo.owner_id == owner, Photo.profile_id == profile_id).order_by(Photo.created_at.desc()))
    return {"items": [{"id": row.id, "name": row.original_name,
                        "created_at": row.created_at.isoformat(), "url": f"/api/v1/photos/{row.id}"} for row in rows]}


@router.get("/photos/{photo_id}")
def photo(photo_id: str, request: Request, db: Session = Depends(db_session), owner=Depends(current_owner)):
    row = db.get(Photo, photo_id)
    if row is None or row.owner_id != owner:
        return error_response(404, "not_found", "找不到这张照片")
    content = decrypt_bytes(request.app.state.fernet, (request.app.state.settings.data_path / row.relpath).read_bytes())
    return Response(content, media_type=row.content_type, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/chapters/{chapter_id}")
def chapter(chapter_id: str, db: Session = Depends(db_session), owner=Depends(current_owner)):
    row = db.get(Chapter, chapter_id)
    if row is None or row.owner_id != owner:
        return error_response(404, "not_found", "找不到这一章")
    call = store.get_call(db, owner, row.call_id)
    profile = store.get_profile(db, owner, call.profile_id)
    return {"profile_id": profile.id, "elder_name": profile.elder_name,
            "audio_kinds": list(db.scalars(select(AudioAsset.kind).where(AudioAsset.call_id == call.id, AudioAsset.owner_id == owner))),
            "call_id": call.id, "chapter": next(item for item in chapter_views(db, owner, call.id) if item["id"] == row.id)}


@router.post("/calls/{call_id}/draft-tasks", status_code=202)
async def create_task(call_id: str, body: DraftTaskIn, request: Request, db: Session = Depends(db_session), owner=Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    db.commit()
    db.execute(text("BEGIN IMMEDIATE"))
    call = store.get_call(db, owner, call_id)
    if call is None:
        return error_response(404, "not_found", "找不到这次访谈")
    key = f"{owner}:{body.request_id}"
    old = db.scalar(select(DraftTask).where(DraftTask.request_id == key))
    if old is not None:
        if old.call_id != call_id:
            return error_response(409, "request_conflict", "请求编号已用于另一次访谈")
        return task_view(old)
    active = db.scalar(select(DraftTask).where(DraftTask.call_id == call_id, DraftTask.status.in_(["queued", "running"])))
    if active is not None:
        return task_view(active)
    if call.status not in {"ready", "failed"}:
        return error_response(409, "call_not_ready", "请等访谈结束后再整理")
    if not request.app.state.settings.writing_ready:
        return error_response(409, "model_not_configured", "写作服务尚未准备好")
    if chapter_views(db, owner, call_id) and not body.replace:
        return error_response(409, "confirm_replace", "已有章节，重新整理需要确认")
    row = DraftTask(id=store.new_id(), request_id=key, owner_id=owner, call_id=call_id)
    db.add(row)
    db.commit()
    launch_task(request.app, row.id)
    return task_view(row)


@router.get("/draft-tasks/{task_id}")
def read_task(task_id: str, db: Session = Depends(db_session), owner=Depends(current_owner)):
    row = db.get(DraftTask, task_id)
    if row is None or row.owner_id != owner:
        return error_response(404, "not_found", "找不到这次整理")
    return task_view(row)


@router.post("/calls/{call_id}/abandon")
def abandon(call_id: str, db: Session = Depends(db_session), owner=Depends(current_owner)):
    call = store.get_call(db, owner, call_id)
    if call is None:
        return error_response(404, "not_found", "找不到这次访谈")
    if call.status == "live":
        store.finish_call(db, call, "failed", "connection_closed")
    return {"ok": True}
