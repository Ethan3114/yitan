import hashlib
import hmac
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.accounts import Credentials, Recovery, Registration
from app.core.errors import error_response
from app.models import utcnow
from app.services import accounts, store
from app.services.analytics import VISITOR_COOKIE
from app.services.cloudbase import CloudDatabaseError, CloudRepository

router = APIRouter(prefix="/api/v1")


def repository(request: Request) -> CloudRepository:
    return request.app.state.cloud_repository


def cloud_owner(request: Request) -> str | None:
    token = request.cookies.get(store.COOKIE)
    if not token or len(token) > 128:
        return None
    return repository(request).owner_from_session(token, utcnow())


def cloud_admin(request: Request) -> dict | None:
    owner = cloud_owner(request)
    if owner is None:
        return None
    return repository(request).one("accounts", filters={"owner_id": "eq." + owner, "enabled": "eq.true", "role": "eq.admin"}, columns="id,owner_id,username,role")


def subject_hash(request: Request, category: str, value: str) -> str:
    return hmac.new(request.app.state.settings.auth_pepper.encode(), (category + ":" + value).encode(), hashlib.sha256).hexdigest()


def client_address(request: Request) -> str:
    return getattr(request.state, "client_ip", None) or (request.client.host if request.client else "unknown")


def allowed(request: Request, username: str) -> bool:
    return repository(request).rpc("auth_admit", {
        "p_ip_hash": subject_hash(request, "ip", client_address(request)),
        "p_account_hash": subject_hash(request, "account", username),
    }) is True


def session_response(request: Request, role: str, session_id: str):
    settings = request.app.state.settings
    response = JSONResponse({"ok": True, "role": role}, headers={"Cache-Control": "no-store"})
    response.set_cookie(store.COOKIE, session_id, httponly=True, secure=settings.cookie_secure, samesite="lax", max_age=settings.session_days * 86400)
    return response


@router.post("/auth/register")
def register(body: Registration, request: Request):
    if not allowed(request, body.username):
        return error_response(429, "auth_rate_limited", "尝试次数过多，请在十五分钟后重试")
    session_id = secrets.token_urlsafe(27)
    try:
        result = repository(request).rpc("register", {
            "p_invite_hash": accounts.token_hash(body.invitation), "p_username": body.username,
            "p_password_hash": accounts.password_hash(body.password), "p_owner_id": store.new_id(),
            "p_account_id": store.new_id(), "p_session_id": session_id,
            "p_session_days": request.app.state.settings.session_days,
            "p_visitor_id": request.cookies.get(VISITOR_COOKIE),
        })
    except CloudDatabaseError as exc:
        if exc.code == "22023":
            return error_response(400, "registration_unavailable", "邀请码不可用，请向邀请人获取新的邀请码")
        if exc.code == "23505":
            return error_response(409, "registration_conflict", "注册未完成，请更换账号名称后重试")
        raise
    return session_response(request, result["role"], session_id)


@router.post("/auth/password-login")
def login(body: Credentials, request: Request):
    if not allowed(request, body.username):
        return error_response(429, "auth_rate_limited", "尝试次数过多，请在十五分钟后重试")
    account = repository(request).one("accounts", filters={"username": "eq." + body.username})
    matches = accounts.password_matches(account["password_hash"] if account else accounts.UNKNOWN_HASH, body.password)
    if not account or not account["enabled"] or not matches:
        return error_response(401, "credentials_invalid", "账号或密码不正确")
    session_id = secrets.token_urlsafe(27)
    rehashed = accounts.password_hash(body.password) if accounts.HASHER.check_needs_rehash(account["password_hash"]) else None
    result = repository(request).rpc("password_login", {
        "p_account_id": account["id"], "p_expected_password_hash": account["password_hash"],
        "p_session_id": session_id, "p_session_days": request.app.state.settings.session_days,
        "p_new_password_hash": rehashed, "p_visitor_id": request.cookies.get(VISITOR_COOKIE),
    })
    if result is None:
        return error_response(401, "credentials_invalid", "账号或密码不正确")
    return session_response(request, result["role"], session_id)


@router.post("/auth/login")
def legacy_login():
    return error_response(403, "password_login_required", "请使用账号和密码登录")


@router.get("/auth/methods")
def methods():
    return {"password": True, "invitation_required": True, "local_invite_login": False}


@router.post("/auth/logout")
def logout(request: Request):
    token = request.cookies.get(store.COOKIE)
    if token and len(token) <= 128:
        repository(request).remove("sessions", {"id": "eq." + token})
    response = JSONResponse({"ok": True})
    response.delete_cookie(store.COOKIE)
    response.delete_cookie(VISITOR_COOKIE)
    return response


@router.get("/me")
def me(request: Request, owner: str | None = Depends(cloud_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    settings = request.app.state.settings
    return {"owner_id": owner, "voice_ready": settings.voice_ready, "writing_ready": settings.writing_ready}


@router.get("/account")
def account_view(request: Request, owner: str | None = Depends(cloud_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    account = repository(request).one("accounts", filters={"owner_id": "eq." + owner, "enabled": "eq.true"}, columns="username,role")
    if account is None:
        return error_response(401, "unauthorized", "请先登录")
    return {**account, "storage_mode": "cloud"}


@router.post("/admin/invitations")
def invitation_create(request: Request, admin: dict | None = Depends(cloud_admin)):
    if admin is None:
        return error_response(403, "forbidden", "仅管理员可以创建邀请码")
    token = secrets.token_urlsafe(32)
    row = repository(request).rpc("invitation_create", {"p_owner_id": admin["owner_id"], "p_id": store.new_id(), "p_token_hash": accounts.token_hash(token)})
    return JSONResponse({**row, "invitation": token}, headers={"Cache-Control": "no-store"})


@router.get("/admin/accounts")
def accounts_list(request: Request, admin: dict | None = Depends(cloud_admin)):
    if admin is None:
        return error_response(403, "forbidden", "仅管理员可以查看账户")
    return {"items": list(repository(request).rows("accounts", columns="id,username,role,enabled,created_at", order="created_at.desc,id.desc"))}


@router.get("/admin/invitations")
def invitations_list(request: Request, admin: dict | None = Depends(cloud_admin)):
    if admin is None:
        return error_response(403, "forbidden", "仅管理员可以查看邀请码")
    rows = repository(request).rows("registration_invites", columns="id,created_at,expires_at,used_at,revoked", order="created_at.desc,id.desc")
    return {"items": [{"id": row["id"], "created_at": row["created_at"], "expires_at": row["expires_at"], "used": row["used_at"] is not None, "revoked": row["revoked"]} for row in rows]}


@router.delete("/admin/invitations/{invite_id}")
def invitation_revoke(invite_id: str, request: Request, admin: dict | None = Depends(cloud_admin)):
    if admin is None:
        return error_response(403, "forbidden", "仅管理员可以撤销邀请码")
    changed = repository(request).rpc("invitation_revoke", {"p_owner_id": admin["owner_id"], "p_id": invite_id})
    return {"ok": True} if changed else error_response(404, "not_found", "邀请码不存在")


@router.post("/admin/accounts/{account_id}/recovery")
def recovery_create(account_id: str, request: Request, admin: dict | None = Depends(cloud_admin)):
    if admin is None:
        return error_response(403, "forbidden", "仅管理员可以发起账号恢复")
    token = secrets.token_urlsafe(32)
    changed = repository(request).rpc("recovery_create", {"p_owner_id": admin["owner_id"], "p_account_id": account_id, "p_id": store.new_id(), "p_token_hash": accounts.token_hash(token)})
    if not changed:
        return error_response(404, "not_found", "账号不存在或已停用")
    return JSONResponse({"token": token, "expires_in_seconds": 1800}, headers={"Cache-Control": "no-store"})


@router.post("/auth/recover")
def recover(body: Recovery, request: Request):
    if not allowed(request, "recovery:" + accounts.token_hash(body.token)):
        return error_response(429, "auth_rate_limited", "尝试次数过多，请在十五分钟后重试")
    changed = repository(request).rpc("recover_password", {"p_token_hash": accounts.token_hash(body.token), "p_password_hash": accounts.password_hash(body.password)})
    return {"ok": True} if changed else error_response(400, "recovery_invalid", "恢复码无效或已过期")
