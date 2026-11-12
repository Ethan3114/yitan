import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from app.api.cloud_accounts import cloud_owner
from app.api.family import COOKIE, CommentIn, InviteIn, Publish, Redeem, Selection, decode, digest, encode, token_for
from app.core.errors import error_response
from app.services.cloud_books import owned
from app.services.cloudbase import CloudDatabaseError

router = APIRouter(prefix="/api/v1")


def member_for(request):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    return request.app.state.cloud_repository.one("family_members", filters={"session_hash": "eq." + digest(token), "revoked": "eq.false", "expires_at": "gt." + datetime.now(timezone.utc).isoformat()})


def publication(repository, profile_id):
    return repository.one("family_publications", filters={"profile_id": "eq." + profile_id, "active": "eq.true"})


def comments_for(request, pub, member_id=None):
    repository = request.app.state.cloud_repository
    rows = repository.rows("family_comments", filters={"publication_id": "eq." + pub["id"], "owner_id": "eq." + pub["owner_id"], "deleted": "eq.false"}, order="created_at.asc,id.asc")
    members = {item["id"]: item["name"] for item in repository.rows("family_members", filters={"profile_id": "eq." + pub["profile_id"], "owner_id": "eq." + pub["owner_id"]}, columns="id,name", order="id.asc")}
    return [{"id": row["id"], "chapter_id": row["chapter_id"], "name": members[row["member_id"]], "text": decode(request, row["payload"]), "mine": row["member_id"] == member_id, "created_at": row["created_at"]} for row in rows]


def selection(repository, owner, profile_id, body):
    if owner is None:
        return None
    return repository.rpc("family_selection", {"p_owner": owner, "p_profile": profile_id, "p_chapters": body.chapter_ids, "p_photos": body.photo_ids, "p_audio": body.allow_audio})


def family_error(exc):
    if exc.code == "55000":
        return error_response(410, "family_access_closed", "邀请或阅读权限已变化，请联系家人重新邀请")
    if exc.code == "22023":
        return error_response(409, "request_conflict", "内容或提交记录已变化，请重新打开")
    raise exc


@router.get("/profiles/{profile_id}/family")
def management(profile_id: str, request: Request, owner=Depends(cloud_owner)):
    repository = request.app.state.cloud_repository
    if owned(repository, "profiles", owner, profile_id) is None:
        return error_response(404, "not_found", "找不到这本回忆录")
    pub = publication(repository, profile_id)
    members = repository.rows("family_members", filters={"profile_id": "eq." + profile_id, "owner_id": "eq." + owner, "revoked": "eq.false"}, order="id.asc")
    invitations = repository.rows("family_invites", filters={"profile_id": "eq." + profile_id, "owner_id": "eq." + owner, "revoked": "eq.false", "member_id": "is.null", "expires_at": "gt." + datetime.now(timezone.utc).isoformat()}, order="expires_at.asc")
    return {"publication": {"id": pub["id"], **decode(request, pub["payload"])} if pub else None,
            "members": [{"id": row["id"], "name": row["name"], "allow_audio": row["allow_audio"]} for row in members],
            "invitations": [{"id": row["id"], "expires_at": row["expires_at"]} for row in invitations], "comments": comments_for(request, pub) if pub else []}


@router.post("/profiles/{profile_id}/family/preview")
def preview(profile_id: str, body: Selection, request: Request, owner=Depends(cloud_owner)):
    book = selection(request.app.state.cloud_repository, owner, profile_id, body)
    if book is None:
        return error_response(400, "invalid_selection", "请选择自己这本书中的章节和照片")
    return {"book": book, "digest": digest(json.dumps(book, sort_keys=True, ensure_ascii=False))}


@router.post("/profiles/{profile_id}/family/publish")
def publish(profile_id: str, body: Publish, request: Request, owner=Depends(cloud_owner)):
    repository = request.app.state.cloud_repository
    book = selection(repository, owner, profile_id, body)
    if book is None or digest(json.dumps(book, sort_keys=True, ensure_ascii=False)) != body.digest:
        return error_response(409, "preview_changed", "内容已经变化，请重新预览后展示")
    try:
        return repository.rpc("publish_family", {"p_owner": owner, "p_profile": profile_id, "p_request": body.request_id, "p_id": str(uuid4()), "p_book": book, "p_payload": encode(request, book)})
    except CloudDatabaseError as exc:
        return family_error(exc)


@router.delete("/profiles/{profile_id}/family/publication")
def unpublish(profile_id: str, request: Request, owner=Depends(cloud_owner)):
    repository = request.app.state.cloud_repository
    if owned(repository, "profiles", owner, profile_id) is None:
        return error_response(404, "not_found", "找不到这本回忆录")
    repository.request("PATCH", "yitan_family_publications", params={"profile_id": "eq." + profile_id, "owner_id": "eq." + owner, "active": "eq.true"}, payload={"active": False})
    return {"ok": True}


@router.post("/profiles/{profile_id}/invitations")
def invite(profile_id: str, body: InviteIn, request: Request, owner=Depends(cloud_owner)):
    repository = request.app.state.cloud_repository
    if owned(repository, "profiles", owner, profile_id) is None:
        return error_response(404, "not_found", "找不到这本回忆录")
    identifier = str(uuid4())
    try:
        row = repository.rpc("invite_family", {"p_owner": owner, "p_profile": profile_id, "p_request": body.request_id, "p_id": identifier, "p_token_hash": digest(token_for(request, "invite", identifier)), "p_audio": body.allow_audio})
    except CloudDatabaseError as exc:
        return family_error(exc)
    return {"id": row["id"], "token": token_for(request, "invite", row["id"]), "expires_at": row["expires_at"]}


@router.delete("/invitations/{invite_id}")
def revoke_invite(invite_id: str, request: Request, owner=Depends(cloud_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    rows = request.app.state.cloud_repository.request("PATCH", "yitan_family_invites", params={"id": "eq." + invite_id, "owner_id": "eq." + owner}, payload={"revoked": True})
    if not rows:
        return error_response(404, "not_found", "找不到这份邀请")
    return {"ok": True}


@router.post("/invitations/redeem")
def redeem(body: Redeem, request: Request):
    if not body.name.strip():
        return error_response(400, "invalid_input", "请填写家人称呼")
    identifier = str(uuid4())
    try:
        member = request.app.state.cloud_repository.rpc("redeem_family", {"p_hash": digest(body.token), "p_name": body.name.strip(), "p_request": body.request_id, "p_member_id": identifier, "p_session_hash": digest(token_for(request, "member", identifier))})
    except CloudDatabaseError as exc:
        return family_error(exc)
    response = JSONResponse({"profile_id": member["profile_id"]})
    response.set_cookie(COOKIE, token_for(request, "member", member["id"]), httponly=True, samesite="strict", secure=request.app.state.settings.cookie_secure, max_age=30 * 86400)
    return response


@router.delete("/profiles/{profile_id}/members/{member_id}")
def remove_member(profile_id: str, member_id: str, request: Request, owner=Depends(cloud_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    rows = request.app.state.cloud_repository.request("PATCH", "yitan_family_members", params={"id": "eq." + member_id, "profile_id": "eq." + profile_id, "owner_id": "eq." + owner}, payload={"revoked": True})
    if not rows:
        return error_response(404, "not_found", "找不到这位家人")
    return {"ok": True}


@router.get("/family/reading")
def reading(request: Request):
    repository = request.app.state.cloud_repository
    member = member_for(request)
    if member is None:
        return error_response(401, "unauthorized", "请通过家人发来的邀请进入")
    pub = publication(repository, member["profile_id"])
    if not pub:
        return {"name": member["name"], "publication": None, "comments": []}
    book = decode(request, pub["payload"])
    book["allow_audio"] = book["allow_audio"] and member["allow_audio"]
    repository.rpc("record_family_read", {"p_session_hash": digest(request.cookies[COOKIE]), "p_publication": pub["id"]})
    return {"name": member["name"], "publication": {"id": pub["id"], **book}, "comments": comments_for(request, pub, member["id"])}


@router.get("/family/photos/{photo_id}")
def family_photo(photo_id: str, request: Request):
    repository = request.app.state.cloud_repository
    member = member_for(request)
    pub = publication(repository, member["profile_id"]) if member else None
    if pub is None or photo_id not in pub["photo_ids"]:
        return error_response(404, "not_found", "这张照片尚未分享")
    photo = owned(repository, "photos", member["owner_id"], photo_id)
    if photo is None or photo["profile_id"] != member["profile_id"]:
        return error_response(404, "not_found", "找不到这张照片")
    return Response(request.app.state.cloud_files.get(member["owner_id"], photo["relpath"]), media_type=photo["content_type"], headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.api_route("/family/audio/{call_id}", methods=["GET", "HEAD"])
def family_audio(call_id: str, request: Request):
    repository = request.app.state.cloud_repository
    member = member_for(request)
    pub = publication(repository, member["profile_id"]) if member else None
    book = decode(request, pub["payload"]) if pub else None
    if not book or not member["allow_audio"] or not book["allow_audio"] or call_id not in {item["call_id"] for item in book["chapters"]}:
        return error_response(404, "not_found", "这段原声尚未授权")
    from app.api.cloud_calls import audio_response
    return audio_response(request, member["owner_id"], call_id, "playback")


@router.post("/family/comments")
def comment(body: CommentIn, request: Request):
    if not body.text.strip():
        return error_response(400, "invalid_input", "请填写留言")
    try:
        return request.app.state.cloud_repository.rpc("comment_family", {"p_session_hash": digest(request.cookies.get(COOKIE, "")), "p_publication": body.publication_id, "p_chapter": body.chapter_id, "p_request": body.request_id, "p_id": str(uuid4()), "p_payload": encode(request, body.text.strip()), "p_content_hash": digest(body.text.strip())})
    except CloudDatabaseError as exc:
        return family_error(exc)


@router.delete("/family/comments/{comment_id}")
def delete_comment(comment_id: str, request: Request, owner=Depends(cloud_owner)):
    repository = request.app.state.cloud_repository
    row = repository.one("family_comments", filters={"id": "eq." + comment_id})
    member = member_for(request)
    if row is None or not (owner == row["owner_id"] or (member and member["id"] == row["member_id"])):
        return error_response(404, "not_found", "找不到这条留言")
    repository.request("PATCH", "yitan_family_comments", params={"id": "eq." + comment_id, "owner_id": "eq." + row["owner_id"]}, payload={"deleted": True})
    return {"ok": True}


@router.post("/family/logout")
def family_logout(request: Request):
    member = member_for(request)
    if member:
        request.app.state.cloud_repository.request("PATCH", "yitan_family_members", params={"id": "eq." + member["id"], "owner_id": "eq." + member["owner_id"]}, payload={"revoked": True})
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response
