import hashlib
import hmac
import json
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.http import current_owner, db_session
from app.core.errors import error_response
from app.models import AudioAsset, BookSettings, Call, Chapter, FamilyComment, FamilyInvite, FamilyMember, FamilyPublication, Paragraph, Photo, utcnow
from app.services import store

router = APIRouter(prefix="/api/v1")
COOKIE = "yitan_family"


class Selection(BaseModel):
    chapter_ids: list[str] = Field(default_factory=list, max_length=200)
    photo_ids: list[str] = Field(default_factory=list, max_length=200)
    allow_audio: bool = False


class Publish(Selection):
    digest: str = Field(min_length=64, max_length=64)
    request_id: str = Field(min_length=16, max_length=80)


class InviteIn(BaseModel):
    request_id: str = Field(min_length=16, max_length=80)
    allow_audio: bool = False


class Redeem(BaseModel):
    token: str = Field(min_length=32, max_length=200)
    name: str = Field(min_length=1, max_length=40)
    request_id: str = Field(min_length=16, max_length=80)


class CommentIn(BaseModel):
    publication_id: str
    chapter_id: str
    text: str = Field(min_length=1, max_length=1000)
    request_id: str = Field(min_length=16, max_length=80)


def encode(request, value):
    return request.app.state.fernet.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()


def decode(request, value):
    return json.loads(request.app.state.fernet.decrypt(value.encode()))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def token_for(request, kind, identifier):
    return hmac.new(request.app.state.settings.audio_fernet_key.encode(), f"family:{kind}:{identifier}".encode(), hashlib.sha256).hexdigest()


def live_date(value):
    return value.replace(tzinfo=timezone.utc) > utcnow()


def lock(db):
    db.commit()
    db.execute(text("BEGIN IMMEDIATE"))


def publication(db, profile_id):
    return db.scalar(select(FamilyPublication).where(FamilyPublication.profile_id == profile_id, FamilyPublication.active.is_(True)))


def member_for(request, db):
    token = request.cookies.get(COOKIE, "")
    member = db.scalar(select(FamilyMember).where(FamilyMember.session_hash == digest(token)))
    return member if member and not member.revoked and live_date(member.expires_at) else None


def selected_book(db, owner, profile_id, body):
    profile = store.get_profile(db, owner, profile_id)
    if not profile or not body.chapter_ids or len(set(body.chapter_ids)) != len(body.chapter_ids):
        return None
    chapters = []
    for cid in body.chapter_ids:
        chapter = db.get(Chapter, cid)
        call = db.get(Call, chapter.call_id) if chapter else None
        if not chapter or chapter.owner_id != owner or not call or call.profile_id != profile_id:
            return None
        paragraphs = list(db.scalars(select(Paragraph).where(Paragraph.chapter_id == cid, Paragraph.owner_id == owner).order_by(Paragraph.position)))
        chapters.append({"id": cid, "title": chapter.title, "paragraphs": [p.text for p in paragraphs], "call_id": call.id})
    photos = []
    for pid in dict.fromkeys(body.photo_ids):
        photo = db.get(Photo, pid)
        if not photo or photo.owner_id != owner or photo.profile_id != profile_id:
            return None
        photos.append({"id": pid, "name": photo.original_name})
    book = db.get(BookSettings, profile_id)
    result = {"title": book.title if book else f"{profile.elder_name}的故事", "chapters": chapters, "photos": photos, "allow_audio": body.allow_audio}
    return result


def comments_for(request, db, pub, member_id=None):
    rows = db.scalars(select(FamilyComment).where(FamilyComment.publication_id == pub.id, FamilyComment.deleted.is_(False)).order_by(FamilyComment.created_at))
    return [{"id": c.id, "chapter_id": c.chapter_id, "name": db.get(FamilyMember, c.member_id).name, "text": decode(request, c.payload), "mine": c.member_id == member_id, "created_at": c.created_at.isoformat()} for c in rows]


@router.get("/profiles/{profile_id}/family")
def management(profile_id: str, request: Request, db: Session = Depends(db_session), owner=Depends(current_owner)):
    if not store.get_profile(db, owner, profile_id):
        return error_response(404, "not_found", "找不到这本回忆录")
    pub = publication(db, profile_id)
    members = db.scalars(select(FamilyMember).where(FamilyMember.profile_id == profile_id, FamilyMember.revoked.is_(False)))
    invitations = db.scalars(select(FamilyInvite).where(FamilyInvite.profile_id == profile_id, FamilyInvite.revoked.is_(False), FamilyInvite.member_id.is_(None)))
    return {"publication": {"id": pub.id, **decode(request, pub.payload)} if pub else None,
            "members": [{"id": m.id, "name": m.name, "allow_audio": m.allow_audio} for m in members],
            "invitations": [{"id": i.id, "expires_at": i.expires_at.isoformat()} for i in invitations if live_date(i.expires_at)],
            "comments": comments_for(request, db, pub) if pub else []}


@router.post("/profiles/{profile_id}/family/preview")
def preview(profile_id: str, body: Selection, db: Session = Depends(db_session), owner=Depends(current_owner)):
    book = selected_book(db, owner, profile_id, body)
    if book is None:
        return error_response(400, "invalid_selection", "请选择自己这本书中的章节和照片")
    return {"book": book, "digest": digest(json.dumps(book, sort_keys=True, ensure_ascii=False))}


@router.post("/profiles/{profile_id}/family/publish")
def publish(profile_id: str, body: Publish, request: Request, db: Session = Depends(db_session), owner=Depends(current_owner)):
    if not store.get_profile(db, owner, profile_id):
        return error_response(404, "not_found", "找不到这本回忆录")
    lock(db)
    key = f"{profile_id}:{body.request_id}"
    old = db.scalar(select(FamilyPublication).where(FamilyPublication.request_id == key))
    if old:
        if digest(json.dumps(decode(request, old.payload), sort_keys=True, ensure_ascii=False)) != body.digest:
            return error_response(409, "request_conflict", "这次展示已经提交，请重新预览")
        return {"id": old.id}
    book = selected_book(db, owner, profile_id, body)
    if book is None or digest(json.dumps(book, sort_keys=True, ensure_ascii=False)) != body.digest:
        return error_response(409, "preview_changed", "内容已经变化，请重新预览后展示")
    current = publication(db, profile_id)
    if current:
        current.active = False
    row = FamilyPublication(id=store.new_id(), owner_id=owner, profile_id=profile_id, request_id=key, payload=encode(request, book))
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.delete("/profiles/{profile_id}/family/publication")
def unpublish(profile_id: str, db: Session = Depends(db_session), owner=Depends(current_owner)):
    if not store.get_profile(db, owner, profile_id):
        return error_response(404, "not_found", "找不到这本回忆录")
    lock(db)
    row = publication(db, profile_id)
    if row:
        row.active = False
    db.commit()
    return {"ok": True}


@router.post("/profiles/{profile_id}/invitations")
def invite(profile_id: str, body: InviteIn, request: Request, db: Session = Depends(db_session), owner=Depends(current_owner)):
    if not store.get_profile(db, owner, profile_id):
        return error_response(404, "not_found", "找不到这本回忆录")
    lock(db)
    pub = publication(db, profile_id)
    if not pub:
        return error_response(409, "not_published", "请先选择内容并确认展示")
    if body.allow_audio and not decode(request, pub.payload)["allow_audio"]:
        return error_response(409, "audio_not_shared", "请先确认展示访谈原声")
    key = f"{profile_id}:{body.request_id}"
    row = db.scalar(select(FamilyInvite).where(FamilyInvite.request_id == key))
    if row and row.allow_audio != body.allow_audio:
        return error_response(409, "request_conflict", "邀请权限已经确定，请创建新邀请")
    if row and (row.revoked or row.member_id or not live_date(row.expires_at)):
        return error_response(410, "invite_closed", "这份邀请已失效，请创建新邀请")
    if not row:
        iid = store.new_id()
        row = FamilyInvite(id=iid, owner_id=owner, profile_id=profile_id, request_id=key, token_hash=digest(token_for(request, "invite", iid)), allow_audio=body.allow_audio, expires_at=utcnow() + timedelta(days=7))
        db.add(row)
        db.commit()
    return {"id": row.id, "token": token_for(request, "invite", row.id), "expires_at": row.expires_at.isoformat()}


@router.delete("/invitations/{invite_id}")
def revoke_invite(invite_id: str, db: Session = Depends(db_session), owner=Depends(current_owner)):
    lock(db)
    row = db.get(FamilyInvite, invite_id)
    if not row or row.owner_id != owner:
        return error_response(404, "not_found", "找不到这份邀请")
    row.revoked = True
    db.commit()
    return {"ok": True}


@router.post("/invitations/redeem")
def redeem(body: Redeem, request: Request, db: Session = Depends(db_session)):
    if not body.name.strip():
        return error_response(400, "invalid_input", "请填写家人称呼")
    lock(db)
    row = db.scalar(select(FamilyInvite).where(FamilyInvite.token_hash == digest(body.token)))
    if not row or row.revoked or not live_date(row.expires_at):
        return error_response(410, "invite_closed", "邀请已失效，请联系家人重新邀请")
    if row.member_id and row.redeem_request != body.request_id:
        return error_response(410, "invite_used", "邀请已被使用，请联系家人重新邀请")
    if row.member_id:
        member = db.get(FamilyMember, row.member_id)
        if member.revoked or not live_date(member.expires_at):
            return error_response(410, "member_removed", "阅读权限已结束")
    else:
        mid = store.new_id()
        member = FamilyMember(id=mid, owner_id=row.owner_id, profile_id=row.profile_id, name=body.name.strip(), session_hash=digest(token_for(request, "member", mid)), expires_at=utcnow() + timedelta(days=30), allow_audio=row.allow_audio)
        db.add(member)
        row.member_id, row.redeem_request = mid, body.request_id
        db.commit()
    response = JSONResponse({"profile_id": member.profile_id})
    response.set_cookie(COOKIE, token_for(request, "member", member.id), httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=30 * 86400)
    return response


@router.delete("/profiles/{profile_id}/members/{member_id}")
def remove_member(profile_id: str, member_id: str, db: Session = Depends(db_session), owner=Depends(current_owner)):
    lock(db)
    member = db.get(FamilyMember, member_id)
    if not member or member.owner_id != owner or member.profile_id != profile_id:
        return error_response(404, "not_found", "找不到这位家人")
    member.revoked = True
    db.commit()
    return {"ok": True}


@router.get("/family/reading")
def reading(request: Request, db: Session = Depends(db_session)):
    member = member_for(request, db)
    if not member:
        return error_response(401, "unauthorized", "请通过家人发来的邀请进入")
    pub = publication(db, member.profile_id)
    if not pub:
        return {"name": member.name, "publication": None, "comments": []}
    book = decode(request, pub.payload)
    book["allow_audio"] = book["allow_audio"] and member.allow_audio
    return {"name": member.name, "publication": {"id": pub.id, **book}, "comments": comments_for(request, db, pub, member.id)}


@router.get("/family/photos/{photo_id}")
def family_photo(photo_id: str, request: Request, db: Session = Depends(db_session)):
    member = member_for(request, db)
    pub = publication(db, member.profile_id) if member else None
    if not pub or photo_id not in {p["id"] for p in decode(request, pub.payload)["photos"]}:
        return error_response(404, "not_found", "这张照片尚未分享")
    photo = db.get(Photo, photo_id)
    if not photo or photo.owner_id != member.owner_id or photo.profile_id != member.profile_id:
        return error_response(404, "not_found", "找不到这张照片")
    data = request.app.state.fernet.decrypt((request.app.state.settings.data_path / photo.relpath).read_bytes())
    return Response(data, media_type=photo.content_type, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/family/audio/{call_id}")
def family_audio(call_id: str, request: Request, db: Session = Depends(db_session)):
    member = member_for(request, db)
    pub = publication(db, member.profile_id) if member else None
    book = decode(request, pub.payload) if pub else None
    if not book or not member.allow_audio or not book["allow_audio"] or call_id not in {c["call_id"] for c in book["chapters"]}:
        return error_response(404, "not_found", "这段原声尚未授权")
    kinds = set(db.scalars(select(AudioAsset.kind).where(AudioAsset.call_id == call_id, AudioAsset.owner_id == member.owner_id)))
    kind = "conversation" if "conversation" in kinds else "user"
    pcm = store.read_audio(db, request.app.state.fernet, request.app.state.settings.data_path, member.owner_id, call_id, kind)
    if pcm is None:
        return error_response(404, "not_found", "这次访谈没有录音")
    return Response(store.pcm16_to_wav(pcm, 16000), media_type="audio/wav", headers={"Cache-Control": "private, no-store"})


@router.post("/family/comments")
def comment(body: CommentIn, request: Request, db: Session = Depends(db_session)):
    lock(db)
    member = member_for(request, db)
    pub = publication(db, member.profile_id) if member else None
    if not pub or pub.id != body.publication_id or body.chapter_id not in {c["id"] for c in decode(request, pub.payload)["chapters"]}:
        return error_response(404, "not_found", "阅读内容已变化，请重新打开")
    if not body.text.strip():
        return error_response(400, "invalid_input", "请填写留言")
    key = f"{member.id}:{body.request_id}"
    old = db.scalar(select(FamilyComment).where(FamilyComment.request_id == key))
    if old:
        if old.publication_id != pub.id or old.chapter_id != body.chapter_id or decode(request, old.payload) != body.text.strip():
            return error_response(409, "request_conflict", "这条留言已提交，请重新打开")
        return {"id": old.id}
    row = FamilyComment(id=store.new_id(), owner_id=member.owner_id, publication_id=pub.id, member_id=member.id, chapter_id=body.chapter_id, request_id=key, payload=encode(request, body.text.strip()))
    db.add(row)
    db.commit()
    return {"id": row.id}


@router.delete("/family/comments/{comment_id}")
def delete_comment(comment_id: str, request: Request, db: Session = Depends(db_session), owner=Depends(current_owner)):
    lock(db)
    row = db.get(FamilyComment, comment_id)
    member = member_for(request, db)
    if not row or not (row.owner_id == owner or (member and row.member_id == member.id)):
        return error_response(404, "not_found", "找不到这条留言")
    row.deleted = True
    db.commit()
    return {"ok": True}


@router.post("/family/logout")
def family_logout(request: Request, db: Session = Depends(db_session)):
    member = member_for(request, db)
    if member:
        member.revoked = True
        db.commit()
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response
