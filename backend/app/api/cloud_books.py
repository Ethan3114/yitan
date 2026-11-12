from uuid import uuid4

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response

from app.api.cloud_accounts import cloud_owner
from app.api.editions import RestoreIn
from app.api.family import decode, encode
from app.core.errors import error_response
from app.schemas import BookIn, FactIn, ProfileIn, ScoreIn
from app.services import store
from app.services.cloud_books import call_view, chapter_views, owned
from app.services.cloudbase import CloudDatabaseError
from app.services.redact import redact_sensitive

router = APIRouter(prefix="/api/v1")


@router.post("/profiles")
def create_profile(body: ProfileIn, request: Request, owner=Depends(cloud_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    row = request.app.state.cloud_repository.rpc("create_profile", {"p_owner": owner, "p_id": str(uuid4()), "p_data": body.model_dump()})
    return {"id": row["id"], "elder_name": row["elder_name"]}


@router.get("/profiles")
def profiles(request: Request, owner=Depends(cloud_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    return {"items": list(request.app.state.cloud_repository.rows("profiles", filters={"owner_id": "eq." + owner}, columns="id,elder_name,hometown", order="created_at.asc,id.asc"))}


@router.get("/profiles/{profile_id}")
def detail(profile_id: str, request: Request, owner=Depends(cloud_owner)):
    repository = request.app.state.cloud_repository
    profile = owned(repository, "profiles", owner, profile_id)
    if profile is None:
        return error_response(404, "not_found", "找不到这位老人的档案")
    book = repository.one("book_settings", filters={"profile_id": "eq." + profile_id})
    calls = [call_view(repository, owner, call) for call in repository.rows("calls", filters={"owner_id": "eq." + owner, "profile_id": "eq." + profile_id}, order="created_at.desc,id.desc")]
    return {**profile, "book_title": book["title"] if book else profile["elder_name"] + "的故事", "calls": calls}


@router.patch("/profiles/{profile_id}")
def edit_profile(profile_id: str, body: ProfileIn, request: Request, owner=Depends(cloud_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    rows = request.app.state.cloud_repository.request("PATCH", "yitan_profiles", params={"id": "eq." + profile_id, "owner_id": "eq." + owner}, payload=body.model_dump())
    if not rows:
        return error_response(404, "not_found", "找不到这份档案")
    return {"id": rows[0]["id"]}


@router.put("/profiles/{profile_id}/book")
def edit_book(profile_id: str, body: BookIn, request: Request, owner=Depends(cloud_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    if not body.title.strip():
        return error_response(400, "invalid_input", "请填写书名")
    result = request.app.state.cloud_repository.rpc("edit_book_title", {"p_owner": owner, "p_profile": profile_id, "p_title": body.title.strip()})
    if result is None:
        return error_response(404, "not_found", "找不到这本回忆录")
    return {"title": result}


@router.get("/chapters/{chapter_id}")
def chapter(chapter_id: str, request: Request, owner=Depends(cloud_owner)):
    repository = request.app.state.cloud_repository
    row = owned(repository, "chapters", owner, chapter_id)
    if row is None:
        return error_response(404, "not_found", "找不到这一章")
    call = owned(repository, "calls", owner, row["call_id"])
    profile = owned(repository, "profiles", owner, call["profile_id"])
    view = call_view(repository, owner, call)
    return {"profile_id": profile["id"], "elder_name": profile["elder_name"], "call_id": call["id"], "audio_kinds": view["audio_kinds"], "chapter": next(item for item in view["chapters"] if item["id"] == chapter_id)}


@router.post("/facts/{fact_id}")
def revise_fact(fact_id: str, body: FactIn, request: Request, owner=Depends(cloud_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    data = {"status": body.status}
    if body.detail.strip():
        data["detail"] = redact_sensitive(body.detail)[:500]
    rows = request.app.state.cloud_repository.request("PATCH", "yitan_fact_items", params={"id": "eq." + fact_id, "owner_id": "eq." + owner}, payload=data)
    if not rows:
        return error_response(404, "not_found", "找不到这一条核对")
    return {"ok": True}


@router.post("/paragraphs/{paragraph_id}/score")
def score(paragraph_id: str, body: ScoreIn, request: Request, owner=Depends(cloud_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    rows = request.app.state.cloud_repository.request("PATCH", "yitan_paragraphs", params={"id": "eq." + paragraph_id, "owner_id": "eq." + owner}, payload={"score": body.score})
    if not rows:
        return error_response(404, "not_found", "找不到这一段")
    return {"ok": True}


@router.post("/profiles/{profile_id}/photos")
async def upload_photo(profile_id: str, request: Request, file: UploadFile = File(...), owner=Depends(cloud_owner)):
    repository = request.app.state.cloud_repository
    if owned(repository, "profiles", owner, profile_id) is None:
        return error_response(404, "not_found", "找不到这份档案")
    data = await file.read(store.MAX_PHOTO + 1)
    if len(data) > store.MAX_PHOTO:
        return error_response(400, "file_too_large", "照片不能超过 10 MB")
    content_type = store.sniff_image(data)
    if content_type is None:
        return error_response(400, "file_type", "只接收 jpg、png 或 webp 照片")
    photo_id = str(uuid4())
    reference = request.app.state.cloud_files.put(owner, profile_id, None, "photo_" + photo_id.replace("-", "")[:26], data, content_type)
    repository.insert("photos", {"id": photo_id, "owner_id": owner, "profile_id": profile_id, "original_name": store.safe_original_name(file.filename or "photo"), "content_type": content_type, "size_bytes": len(data), "relpath": reference})
    return {"id": photo_id, "content_type": content_type}


@router.get("/profiles/{profile_id}/photos")
def photos(profile_id: str, request: Request, owner=Depends(cloud_owner)):
    repository = request.app.state.cloud_repository
    if owned(repository, "profiles", owner, profile_id) is None:
        return error_response(404, "not_found", "找不到这份档案")
    return {"items": [{"id": item["id"], "name": item["original_name"], "created_at": item["created_at"], "url": "/api/v1/photos/" + item["id"]} for item in repository.rows("photos", filters={"owner_id": "eq." + owner, "profile_id": "eq." + profile_id}, order="created_at.desc,id.desc")]}


@router.get("/photos/{photo_id}")
def photo(photo_id: str, request: Request, owner=Depends(cloud_owner)):
    row = owned(request.app.state.cloud_repository, "photos", owner, photo_id)
    if row is None:
        return error_response(404, "not_found", "找不到这张照片")
    data = request.app.state.cloud_files.get(owner, row["relpath"])
    return Response(data, media_type=row["content_type"], headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/calls/{call_id}/editions")
def editions(call_id: str, request: Request, owner=Depends(cloud_owner)):
    repository = request.app.state.cloud_repository
    if owned(repository, "calls", owner, call_id) is None:
        return error_response(404, "not_found", "找不到这次访谈")
    return {"items": [{"id": item["id"], "created_at": item["created_at"], "chapters": decode(request, item["payload"])} for item in repository.rows("text_editions", filters={"owner_id": "eq." + owner, "call_id": "eq." + call_id}, order="created_at.desc,id.desc")]}


@router.post("/calls/{call_id}/editions/{edition_id}/restore")
def restore(call_id: str, edition_id: str, body: RestoreIn, request: Request, owner=Depends(cloud_owner)):
    if not body.confirm:
        return error_response(400, "confirmation_required", "请先确认恢复这一版本")
    repository = request.app.state.cloud_repository
    edition = owned(repository, "text_editions", owner, edition_id)
    if edition is None or edition["call_id"] != call_id:
        return error_response(404, "not_found", "找不到这个版本")
    current = chapter_views(repository, owner, call_id)
    try:
        return repository.rpc("restore_edition", {"p_owner": owner, "p_call": call_id, "p_edition": edition_id, "p_request": body.request_id, "p_previous": encode(request, current), "p_chapters": decode(request, edition["payload"])})
    except CloudDatabaseError as exc:
        if exc.code == "55000":
            return error_response(409, "writing_active", "请等当前整理结束后恢复")
        raise
