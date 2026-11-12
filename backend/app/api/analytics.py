import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.accounts import admin_account
from app.api.http import current_owner, db_session
from app.core.errors import error_response
from app.models import AnalyticsBrowserSession, AnalyticsEvent, AnalyticsVisitor, Chapter, DraftTask, utcnow
from app.services.analytics import VISITOR_COOKIE, ZONE, server_event, summarize
from app.services.journey import PAGE_EVENTS, PAGE_NAMES

router = APIRouter(prefix="/api/v1")


@router.post("/chapters/{chapter_id}/read")
def read_chapter(chapter_id: str, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    chapter = db.get(Chapter, chapter_id)
    if not owner or chapter is None or chapter.owner_id != owner:
        return error_response(404, "not_found", "找不到这一章")
    recent = db.scalar(select(AnalyticsEvent.id).where(AnalyticsEvent.name == "memoir_read", AnalyticsEvent.owner_id == owner, AnalyticsEvent.entity_id == chapter_id, AnalyticsEvent.created_at > utcnow() - timedelta(minutes=30)))
    if recent is None:
        server_event(db, "memoir_read", owner, chapter_id)
        db.commit()
    return {"ok": True}


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    name: Literal["guide_viewed", "registration_viewed", "registration_submitted", "recording_consent", "microphone_allowed", "microphone_denied", "page_view", "page_heartbeat", "page_leave"]
    step: int | None = Field(default=None, ge=0, le=5)
    version: Literal[1, 2] = 1
    session_id: UUID | None = None
    page_id: str | None = Field(default=None, max_length=32)
    page_visit_id: UUID | None = None
    duration_ms: int | None = Field(default=None, ge=0, le=86400000)

    @model_validator(mode="after")
    def event_fields(self):
        if (self.name == "guide_viewed") != (self.step is not None):
            raise ValueError("guide_step_required")
        fields = (self.session_id, self.page_id, self.page_visit_id, self.duration_ms)
        if self.name in PAGE_EVENTS:
            if any(value is None for value in fields) or self.page_id not in PAGE_NAMES:
                raise ValueError("page_event_fields_required")
        elif any(value is not None for value in fields[1:]):
            raise ValueError("unexpected_page_fields")
        return self


@router.post("/events")
def ingest(body: EventIn, request: Request, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if (body.name == "guide_viewed") != (body.step is not None):
        return error_response(400, "invalid_event", "事件字段不符合要求")
    token = request.cookies.get(VISITOR_COOKIE)
    visitor = db.get(AnalyticsVisitor, token) if token else None
    session = db.get(AnalyticsBrowserSession, str(body.session_id)) if body.session_id else None
    linked = db.get(AnalyticsVisitor, session.visitor_id) if session else None
    if linked and (linked.owner_id is None or linked.owner_id == owner):
        visitor = linked
    if visitor is None or (visitor.owner_id is not None and visitor.owner_id != owner):
        visitor = AnalyticsVisitor(id=secrets.token_urlsafe(32), owner_id=owner)
        db.add(visitor)
        db.flush()
    if owner and visitor.owner_id is None:
        visitor.owner_id = owner
    if body.session_id:
        db.execute(insert(AnalyticsBrowserSession).values(id=str(body.session_id), visitor_id=visitor.id, created_at=utcnow()).on_conflict_do_nothing(index_elements=["id"]))
        session = db.get(AnalyticsBrowserSession, str(body.session_id))
        linked = db.get(AnalyticsVisitor, session.visitor_id)
        if linked.owner_id is None or linked.owner_id == owner:
            visitor = linked
            if owner and visitor.owner_id is None:
                visitor.owner_id = owner
        else:
            session.visitor_id = visitor.id
    event_id = str(body.id)
    existing = db.get(AnalyticsEvent, event_id)
    if existing is not None:
        if existing.visitor_id != visitor.id or existing.name != body.name or existing.page_visit_id != (str(body.page_visit_id) if body.page_visit_id else None):
            return error_response(409, "event_conflict", "事件标识已被使用")
    else:
        recent = db.scalar(select(func.count()).select_from(AnalyticsEvent).where(AnalyticsEvent.visitor_id == visitor.id, AnalyticsEvent.created_at > utcnow() - timedelta(minutes=1)))
        if recent >= 60:
            return error_response(429, "events_rate_limited", "统计上报过于频繁")
        db.add(AnalyticsEvent(id=event_id, name=body.name, visitor_id=visitor.id, owner_id=owner, source="browser", stage=str(body.step) if body.step is not None else "", session_id=str(body.session_id) if body.session_id else None, page_id=body.page_id, page_visit_id=str(body.page_visit_id) if body.page_visit_id else None, duration_ms=body.duration_ms, version=body.version))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return error_response(409, "event_conflict", "事件标识已被使用")
    response = JSONResponse({"ok": True})
    response.set_cookie(VISITOR_COOKIE, visitor.id, httponly=True, secure=request.app.state.settings.cookie_secure or request.url.scheme == "https", samesite="lax", max_age=90 * 86400)
    return response


@router.get("/admin/metrics")
def metrics(start: date | None = None, end: date | None = None, include_admin: bool = False, include_test: bool = False, db: Session = Depends(db_session), owner: str | None = Depends(current_owner)):
    if not admin_account(db, owner):
        return error_response(403, "forbidden", "仅管理员可以查看运营数据")
    today = utcnow().astimezone(ZONE).date()
    start = start or today - timedelta(days=6)
    end = end or today
    if end < start or (end - start).days > 89 or end > today:
        return error_response(400, "date_range_invalid", "请选择截至今天、不超过九十天的日期范围")
    since = datetime.combine(start, time.min, ZONE).astimezone(timezone.utc)
    until = datetime.combine(end + timedelta(days=1), time.min, ZONE).astimezone(timezone.utc)
    result = summarize(db, since, until, utcnow(), include_admin=include_admin, include_test=include_test)
    result["tasks"] = [{"status": status, "count": count} for status, count in db.execute(select(DraftTask.status, func.count()).group_by(DraftTask.status))]
    return result
