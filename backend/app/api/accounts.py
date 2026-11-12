import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.http import current_owner, db_session
from app.core.errors import error_response
from app.models import Account, AuditLog, Owner, PasswordRecovery, RegistrationInvite, SessionRow, utcnow
from app.services import accounts, store
from app.services.analytics import VISITOR_COOKIE, link_visitor, server_event

router = APIRouter(prefix="/api/v1")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize(cls, value: str) -> str:
        return value.lower()


class Registration(Credentials):
    invitation: str = Field(min_length=20, max_length=128)


class Recovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=128)
    password: str = Field(min_length=12, max_length=128)


def allowed(request: Request, db: Session, username: str) -> bool:
    settings = request.app.state.settings
    address = request.client.host if request.client else "unknown"
    return accounts.admit_attempt(db, settings.auth_pepper or settings.audio_fernet_key, address, username)


def session_response(request: Request, account: Account, session: SessionRow):
    settings = request.app.state.settings
    response = JSONResponse({"ok": True, "role": account.role}, headers={"Cache-Control": "no-store"})
    response.set_cookie(store.COOKIE, session.id, httponly=True, secure=settings.cookie_secure or request.url.scheme == "https", samesite="lax", max_age=settings.session_days * 86400)
    return response


def admin_account(db: Session, owner: str | None) -> Account | None:
    if not owner:
        return None
    return db.scalar(select(Account).where(Account.owner_id == owner, Account.enabled.is_(True), Account.role == "admin"))


@router.post("/auth/register")
def register(body: Registration, request: Request, db: Session = Depends(db_session)):
    if not allowed(request, db, body.username):
        return error_response(429, "auth_rate_limited", "尝试次数过多，请在十五分钟后重试")
    invitation = db.scalar(select(RegistrationInvite).where(RegistrationInvite.token_hash == accounts.token_hash(body.invitation), RegistrationInvite.used_at.is_(None), RegistrationInvite.revoked.is_(False), RegistrationInvite.expires_at > utcnow()))
    if invitation is None:
        return error_response(400, "registration_unavailable", "邀请码不可用，请向邀请人获取新的邀请码")
    encoded = accounts.password_hash(body.password)
    owner = Owner(id=store.new_id())
    account = Account(id=store.new_id(), owner_id=owner.id, username=body.username, password_hash=encoded, role="member")
    try:
        db.add(owner)
        db.flush()
        used = db.execute(update(RegistrationInvite).where(RegistrationInvite.id == invitation.id, RegistrationInvite.used_at.is_(None), RegistrationInvite.revoked.is_(False), RegistrationInvite.expires_at > utcnow()).values(used_at=utcnow(), used_by=owner.id).execution_options(synchronize_session=False))
        if used.rowcount != 1:
            db.rollback()
            return error_response(400, "registration_unavailable", "邀请码不可用，请向邀请人获取新的邀请码")
        db.add(account)
        db.flush()
        session = accounts.create_session(db, account, request.app.state.settings.session_days)
        db.add(AuditLog(id=store.new_id(), owner_id=owner.id, action="account_registered", result="success"))
        link_visitor(db, request.cookies.get(VISITOR_COOKIE), owner.id)
        server_event(db, "account_registered", owner.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        return error_response(409, "registration_conflict", "注册未完成，请更换账号名称后重试")
    return session_response(request, account, session)


@router.post("/auth/password-login")
def login(body: Credentials, request: Request, db: Session = Depends(db_session)):
    if not allowed(request, db, body.username):
        return error_response(429, "auth_rate_limited", "尝试次数过多，请在十五分钟后重试")
    account = db.scalar(select(Account).where(Account.username == body.username))
    matches = accounts.password_matches(account.password_hash if account else accounts.UNKNOWN_HASH, body.password)
    if not account or not account.enabled or not matches:
        return error_response(401, "credentials_invalid", "账号或密码不正确")
    if accounts.HASHER.check_needs_rehash(account.password_hash):
        account.password_hash = accounts.password_hash(body.password)
    session = accounts.create_session(db, account, request.app.state.settings.session_days)
    link_visitor(db, request.cookies.get(VISITOR_COOKIE), account.owner_id)
    db.commit()
    return session_response(request, account, session)


@router.get("/auth/methods")
def methods(request: Request):
    return {"password": True, "invitation_required": True, "local_invite_login": request.app.state.settings.deployment_mode == "local"}


@router.get("/account")
def account_view(db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if not owner:
        return error_response(401, "unauthorized", "请先登录")
    account = db.scalar(select(Account).where(Account.owner_id == owner, Account.enabled.is_(True)))
    return {"username": account.username if account else "本机家庭", "role": account.role if account else "member"}


@router.post("/admin/invitations")
def invitation_create(db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if not admin_account(db, owner):
        return error_response(403, "forbidden", "仅管理员可以创建邀请码")
    token = secrets.token_urlsafe(32)
    row = RegistrationInvite(id=store.new_id(), token_hash=accounts.token_hash(token), created_by=owner, expires_at=utcnow() + timedelta(days=7))
    db.add(row)
    db.add(AuditLog(id=store.new_id(), owner_id=owner, action="invitation_created", result=row.id))
    db.commit()
    return JSONResponse({"id": row.id, "invitation": token, "expires_at": row.expires_at.isoformat()}, headers={"Cache-Control": "no-store"})


@router.get("/admin/accounts")
def accounts_list(db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if not admin_account(db, owner):
        return error_response(403, "forbidden", "仅管理员可以查看账户")
    return {"items": [{"id": row.id, "username": row.username, "role": row.role, "enabled": row.enabled, "created_at": row.created_at.isoformat()} for row in db.scalars(select(Account).order_by(Account.created_at.desc()))]}


@router.get("/admin/invitations")
def invitations_list(db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if not admin_account(db, owner):
        return error_response(403, "forbidden", "仅管理员可以查看邀请码")
    return {"items": [{"id": row.id, "created_at": row.created_at.isoformat(), "expires_at": row.expires_at.isoformat(), "used": row.used_at is not None, "revoked": row.revoked} for row in db.scalars(select(RegistrationInvite).order_by(RegistrationInvite.created_at.desc()))]}


@router.delete("/admin/invitations/{invite_id}")
def invitation_revoke(invite_id: str, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if not admin_account(db, owner):
        return error_response(403, "forbidden", "仅管理员可以撤销邀请码")
    row = db.get(RegistrationInvite, invite_id)
    if row is None:
        return error_response(404, "not_found", "邀请码不存在")
    row.revoked = True
    db.add(AuditLog(id=store.new_id(), owner_id=owner, action="invitation_revoked", result=row.id))
    db.commit()
    return {"ok": True}


@router.post("/admin/accounts/{account_id}/recovery")
def recovery_create(account_id: str, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if not admin_account(db, owner):
        return error_response(403, "forbidden", "仅管理员可以发起账号恢复")
    account = db.get(Account, account_id)
    if account is None or not account.enabled:
        return error_response(404, "not_found", "账号不存在或已停用")
    token = secrets.token_urlsafe(32)
    db.execute(delete(PasswordRecovery).where(PasswordRecovery.account_id == account.id))
    db.add(PasswordRecovery(id=store.new_id(), account_id=account.id, token_hash=accounts.token_hash(token), expires_at=utcnow() + timedelta(minutes=30)))
    db.add(AuditLog(id=store.new_id(), owner_id=owner, action="recovery_created", result=account.id))
    db.commit()
    return JSONResponse({"token": token, "expires_in_seconds": 1800}, headers={"Cache-Control": "no-store"})


@router.post("/auth/recover")
def recover(body: Recovery, request: Request, db: Session = Depends(db_session)):
    if not allowed(request, db, "recovery:" + accounts.token_hash(body.token)):
        return error_response(429, "auth_rate_limited", "尝试次数过多，请在十五分钟后重试")
    row = db.scalar(select(PasswordRecovery).where(PasswordRecovery.token_hash == accounts.token_hash(body.token), PasswordRecovery.used.is_(False), PasswordRecovery.expires_at > utcnow()))
    if row is None:
        return error_response(400, "recovery_invalid", "恢复码无效或已过期")
    account = db.get(Account, row.account_id)
    if account is None or not account.enabled:
        return error_response(400, "recovery_invalid", "恢复码无效或已过期")
    encoded = accounts.password_hash(body.password)
    claimed = db.execute(update(PasswordRecovery).where(PasswordRecovery.id == row.id, PasswordRecovery.used.is_(False), PasswordRecovery.expires_at > utcnow()).values(used=True).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        db.rollback()
        return error_response(400, "recovery_invalid", "恢复码无效或已过期")
    account.password_hash = encoded
    db.execute(delete(SessionRow).where(SessionRow.owner_id == account.owner_id))
    db.add(AuditLog(id=store.new_id(), owner_id=account.owner_id, action="password_recovered", result="success"))
    db.commit()
    return {"ok": True}
