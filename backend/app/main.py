"""忆谈后端入口。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.http import router as http_router
from app.api.live import router as live_router
from app.api.mobile import router as mobile_router
from app.api.family import router as family_router
from app.api.editions import router as editions_router
from app.core.config import Settings, get_settings
from app.core.crypto import build_fernet
from app.core.errors import error_body
from app.db import ensure_dirs, make_engine, make_session_factory
from app.models import Base
from app.services.tasks import recover_interrupted


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    ensure_dirs(settings)
    if not settings.audio_fernet_key:
        raise RuntimeError("AUDIO_FERNET_KEY is required")
    engine = make_engine(settings.sqlalchemy_url)
    Base.metadata.create_all(engine)
    app = FastAPI(title="忆谈")
    app.state.settings = settings
    app.state.db_factory = make_session_factory(engine)
    app.state.draft_workers = set()
    recover_interrupted(app.state.db_factory)
    app.state.fernet = build_fernet(settings.audio_fernet_key)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin, "http://localhost:3011", "http://127.0.0.1:3011"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(http_router)
    app.include_router(live_router)
    app.include_router(mobile_router)
    app.include_router(family_router)
    app.include_router(editions_router)

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
