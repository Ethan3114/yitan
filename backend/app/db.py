"""SQLite 连接。每次请求一个会话，用完关闭。"""

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


def make_engine(url: str):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def make_session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_analytics_columns(engine) -> None:
    additions = {
        "accounts": {"analytics_excluded": "BOOLEAN NOT NULL DEFAULT FALSE"},
        "analytics_events": {"session_id": "VARCHAR(36)", "page_id": "VARCHAR(32)", "page_visit_id": "VARCHAR(36)", "duration_ms": "INTEGER"},
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, kind in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {kind}"))


def session_scope(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    db = factory()
    try:
        yield db
    finally:
        db.close()


def ensure_dirs(settings: Settings) -> None:
    settings.data_path.mkdir(parents=True, exist_ok=True)
    (settings.data_path / "files").mkdir(parents=True, exist_ok=True)
