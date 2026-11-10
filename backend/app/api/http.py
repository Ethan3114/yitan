"""HTTP 接口。"""

import asyncio
from collections.abc import Generator

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import select, text

from app.core.config import Settings
from app.core.errors import error_response
from app.schemas import CallIn, FactIn, LoginIn, ProfileIn, ScoreIn
from app.services import store
from app.models import AudioAsset, BookSettings, CallContext, DraftTask, Photo
from app.services.tasks import task_view
from app.services.draft import DraftError, build_draft, chapter_views, score_paragraph, update_fact
from app.services.writing import WritingError
from app.services.tingwu import TingwuError, ensure_full_transcript

router = APIRouter(prefix="/api/v1")


def db_session(request: Request) -> Generator[Session, None, None]:
    db = request.app.state.db_factory()
    try:
        yield db
    finally:
        db.close()


def current_owner(request: Request, db: Session = Depends(db_session)) -> str | None:
    return store.owner_from_session(db, request.cookies.get(store.COOKIE))


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(db_session)):
    settings: Settings = request.app.state.settings
    if body.code != settings.invite_code:
        return error_response(401, "invite_invalid", "邀请码不正确")
    row = store.login_owner(db, settings.session_days)
    response = Response(content='{"ok":true}', media_type="application/json")
    response.set_cookie(store.COOKIE, row.id, httponly=True, secure=request.url.scheme == "https", samesite="lax", max_age=settings.session_days * 86400)
    return response


@router.post("/auth/logout")
def logout(request: Request, db: Session = Depends(db_session)):
    store.delete_session(db, request.cookies.get(store.COOKIE))
    response = Response(content='{"ok":true}', media_type="application/json")
    response.delete_cookie(store.COOKIE)
    return response


@router.get("/me")
def me(request: Request, owner: str | None = Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    settings: Settings = request.app.state.settings
    return {"owner_id": owner, "voice_ready": settings.voice_ready, "writing_ready": settings.writing_ready}


@router.post("/profiles")
def create_profile(body: ProfileIn, request: Request, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    row = store.create_profile(db, owner, body.model_dump())
    return {"id": row.id, "elder_name": row.elder_name}


@router.get("/profiles")
def profiles(db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    return {"items": [{"id": row.id, "elder_name": row.elder_name, "hometown": row.hometown} for row in store.list_profiles(db, owner)]}


@router.get("/profiles/{profile_id}")
def profile_detail(profile_id: str, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    row = store.get_profile(db, owner, profile_id)
    if row is None:
        return error_response(404, "not_found", "找不到这位老人的档案")
    calls = []
    for call in store.list_calls(db, owner, row.id):
        texts = {item.track: item.text for item in store.transcripts_for(db, owner, call.id)}
        views = chapter_views(db, owner, call.id)
        calls.append(
            {
                "id": call.id,
                "status": call.status,
                "failure_code": call.failure_code,
                "texts": texts,
                "chapter": views[0] if views else None,
                "chapters": views,
                "created_at": call.created_at.isoformat(),
                "audio_kinds": list(db.scalars(select(AudioAsset.kind).where(AudioAsset.call_id == call.id, AudioAsset.owner_id == owner))),
                "task": task_view(task) if (task := db.scalar(select(DraftTask).where(DraftTask.call_id == call.id).order_by(DraftTask.created_at.desc()))) else None,
            }
        )
    return {
        "id": row.id,
        "book_title": book.title if (book := db.get(BookSettings, row.id)) else f"{row.elder_name}的故事",
        "elder_name": row.elder_name,
        "birth_year": row.birth_year,
        "hometown": row.hometown,
        "family_names": row.family_names,
        "topics_want": row.topics_want,
        "topics_avoid": row.topics_avoid,
        "calls": calls,
    }


@router.post("/profiles/{profile_id}/photos")
async def upload_photo(profile_id: str, request: Request, file: UploadFile = File(...), db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    row = store.get_profile(db, owner, profile_id)
    if row is None:
        return error_response(404, "not_found", "找不到这位老人的档案")
    data = await file.read(store.MAX_PHOTO + 1)
    if len(data) > store.MAX_PHOTO:
        return error_response(400, "file_too_large", "照片不能超过 10 MB")
    kind = store.sniff_image(data)
    if kind is None:
        return error_response(400, "file_type", "只接收 jpg、png 或 webp 照片")
    saved = store.save_photo(db, request.app.state.fernet, request.app.state.settings.data_path, owner, profile_id, file.filename or "photo", data, kind)
    return {"id": saved.id, "content_type": saved.content_type}


@router.post("/calls")
def open_call(body: CallIn, request: Request, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    db.commit()
    db.execute(text("BEGIN IMMEDIATE"))
    key = f"{owner}:{body.request_id or store.new_id()}"
    previous = db.get(CallContext, key)
    if previous is not None:
        call = store.get_call(db, owner, previous.call_id)
        if call.profile_id != body.profile_id:
            return error_response(409, "request_conflict", "请求编号已用于另一份档案")
        return {"id": call.id, "status": call.status}
    profile = store.get_profile(db, owner, body.profile_id)
    if profile is None:
        return error_response(404, "not_found", "找不到这位老人的档案")
    if not body.consent:
        store.add_audit(db, owner, "consent", "denied")
        return error_response(400, "consent_required", "老人还没有同意录音")
    settings: Settings = request.app.state.settings
    if not settings.voice_ready:
        return error_response(409, "voice_not_configured", "还没有配置豆包语音钥匙，通话暂时不能开始")
    if body.photo_id:
        photo = db.get(Photo, body.photo_id)
        if photo is None or photo.owner_id != owner or photo.profile_id != profile.id:
            return error_response(404, "not_found", "找不到这张照片")
    call = store.create_call(db, owner, profile.id, commit=False)
    db.add(CallContext(id=key, owner_id=owner, call_id=call.id, photo_id=body.photo_id, topic=body.topic))
    db.commit()
    return {"id": call.id, "status": call.status}


@router.get("/calls/{call_id}")
def call_detail(call_id: str, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    call = store.get_call(db, owner, call_id)
    if call is None:
        return error_response(404, "not_found", "找不到这通通话")
    texts = {item.track: item.text for item in store.transcripts_for(db, owner, call.id)}
    return {"id": call.id, "status": call.status, "failure_code": call.failure_code, "texts": texts}


@router.get("/calls/{call_id}/audio/{kind}")
def call_audio(call_id: str, kind: str, request: Request, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    if kind not in {"user", "assistant", "conversation", "playback"}:
        return error_response(400, "audio_kind", "不支持这种录音类型")
    call = store.get_call(db, owner, call_id)
    if call is None:
        return error_response(404, "not_found", "找不到这通通话")
    if kind == "playback":
        kinds = set(db.scalars(select(AudioAsset.kind).where(AudioAsset.call_id == call_id, AudioAsset.owner_id == owner)))
        kind = "conversation" if "conversation" in kinds else "user"
    pcm = store.read_audio(db, request.app.state.fernet, request.app.state.settings.data_path, owner, call_id, kind)
    if pcm is None:
        return error_response(404, "not_found", "这通通话还没有这段录音")
    rate = 24000 if kind == "assistant" else 16000
    return Response(content=store.pcm16_to_wav(pcm, rate), media_type="audio/wav", headers={"Cache-Control": "private, no-store", "X-Recording-Kind": kind})


@router.post("/calls/{call_id}/draft")
async def draft_call(call_id: str, request: Request, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    call = store.get_call(db, owner, call_id)
    if call is None:
        return error_response(404, "not_found", "找不到这通通话")
    if call.status not in {"ready", "failed"}:
        return error_response(409, "call_not_ready", "这通通话还没结束，不能整理成章")
    if not request.app.state.settings.writing_ready:
        return error_response(409, "model_not_configured", "写作服务尚未准备好")
    try:
        await asyncio.to_thread(
            ensure_full_transcript,
            db,
            request.app.state.settings,
            request.app.state.fernet,
            request.app.state.settings.data_path,
            owner,
            call.id,
        )
        chapter = await build_draft(db, request.app.state.settings, owner, call.id, call.profile_id)
    except (DraftError, TingwuError, WritingError) as exc:
        return error_response(409, exc.code, exc.message)
    views = chapter_views(db, owner, call.id)
    return {"id": chapter.id, "chapters": views}


@router.post("/facts/{fact_id}")
def revise_fact(fact_id: str, body: FactIn, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    if not update_fact(db, owner, fact_id, body.status, body.detail):
        return error_response(404, "not_found", "找不到这一条核对")
    return {"ok": True}


@router.post("/paragraphs/{paragraph_id}/score")
def score(paragraph_id: str, body: ScoreIn, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    if not score_paragraph(db, owner, paragraph_id, body.score):
        return error_response(404, "not_found", "找不到这一段")
    return {"ok": True}
