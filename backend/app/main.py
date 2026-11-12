"""忆谈后端入口。"""

import hmac
import ipaddress
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.http import router as http_router
from app.api.live import router as live_router
from app.api.mobile import router as mobile_router
from app.api.family import router as family_router
from app.api.editions import router as editions_router
from app.api.accounts import router as accounts_router
from app.api.analytics import router as analytics_router
from app.core.config import Settings, get_settings
from app.core.crypto import build_fernet
from app.core.errors import error_body
from app.db import ensure_analytics_columns, ensure_dirs, make_engine, make_session_factory
from app.models import Base
from app.services.tasks import recover_interrupted
from app.services.cloudbase import CloudRepository
from app.services.cloud_files import CloudFiles


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.deployment_mode not in {"local", "cloud"}:
        raise RuntimeError("DEPLOYMENT_MODE must be local or cloud")
    if settings.deployment_mode == "cloud" and (not settings.cookie_secure or len(settings.auth_pepper) < 32 or not settings.web_origin.startswith("https://")):
        raise RuntimeError("Cloud mode requires HTTPS, secure cookies and AUTH_PEPPER")
    if settings.deployment_mode == "cloud" and (len(settings.proxy_shared_secret) < 32 or len(settings.worker_token) < 32 or not settings.worker_url.startswith("https://")):
        raise RuntimeError("Cloud mode requires proxy and worker credentials with an HTTPS worker URL")
    if not settings.audio_fernet_key:
        raise RuntimeError("AUDIO_FERNET_KEY is required")
    @asynccontextmanager
    async def lifespan(app):
        yield
        if settings.deployment_mode == "cloud":
            app.state.cloud_files.close()
            app.state.cloud_repository.close()

    app = FastAPI(title="忆谈", lifespan=lifespan, docs_url=None if settings.deployment_mode == "cloud" else "/docs", redoc_url=None)
    app.state.settings = settings
    app.state.draft_workers = set()
    app.state.fernet = build_fernet(settings.audio_fernet_key)
    if settings.deployment_mode == "cloud":
        from app.api.cloud_accounts import router as cloud_accounts
        from app.api.cloud_analytics import router as cloud_analytics
        from app.api.cloud_books import router as cloud_books
        from app.api.cloud_calls import router as cloud_calls
        from app.api.cloud_family import router as cloud_family
        app.state.cloud_repository = CloudRepository(settings.cloudbase_env_id, settings.cloudbase_api_key)
        app.state.cloud_files = CloudFiles(app.state.cloud_repository, app.state.fernet, settings.cloudbase_env_id, settings.cloudbase_api_key)
        for router in [cloud_accounts, cloud_analytics, cloud_books, cloud_calls, cloud_family]:
            app.include_router(router)
    else:
        ensure_dirs(settings)
        engine = make_engine(settings.sqlalchemy_url)
        Base.metadata.create_all(engine)
        ensure_analytics_columns(engine)
        app.state.db_factory = make_session_factory(engine)
        recover_interrupted(app.state.db_factory)
        for router in [http_router, live_router, mobile_router, family_router, editions_router, accounts_router, analytics_router]:
            app.include_router(router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin] if settings.deployment_mode == "cloud" else [settings.web_origin, "http://localhost:3011", "http://127.0.0.1:3011"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    @app.middleware("http")
    async def browser_origin(request: Request, call_next):
        if settings.deployment_mode == "cloud" and request.url.path != "/health":
            if request.url.path == "/api/internal/work":
                if not hmac.compare_digest(request.headers.get("x-worker-token", ""), settings.worker_token):
                    return JSONResponse(status_code=403, content=error_body("worker_denied", "请求未被授权"))
            elif not hmac.compare_digest(request.headers.get("x-proxy-secret", ""), settings.proxy_shared_secret):
                return JSONResponse(status_code=403, content=error_body("proxy_denied", "请通过忆谈页面访问"))
            if request.url.path != "/api/internal/work":
                address = request.headers.get("x-client-ip", "")
                if address:
                    try:
                        request.state.client_ip = str(ipaddress.ip_address(address))
                    except ValueError:
                        return JSONResponse(status_code=400, content=error_body("invalid_address", "请求来源格式无效"))
                else:
                    request.state.client_ip = request.client.host
        origin = request.headers.get("origin")
        allowed_origins = {settings.web_origin}
        if settings.deployment_mode == "local":
            allowed_origins.update({"http://127.0.0.1:3011", "http://localhost:3011"})
            if request.url.scheme == "https":
                allowed_origins.add(str(request.base_url).rstrip("/"))
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin and origin not in allowed_origins:
            return JSONResponse(status_code=403, content=error_body("origin_denied", "请求来源不被允许"))
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, _exc: RequestValidationError):
        return JSONResponse(status_code=400, content=error_body("invalid_input", "填写的内容不完整"))

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        if getattr(exc, "status_code", None):
            return JSONResponse(status_code=exc.status_code, content=error_body("http_error", "请求没有完成"))
        return JSONResponse(status_code=500, content=error_body("internal", "这次没有完成，请再试一次"))

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def app_factory() -> FastAPI:
    return create_app()
