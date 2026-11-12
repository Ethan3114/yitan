import secrets
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.analytics import EventIn
from app.api.cloud_accounts import client_address, cloud_admin, cloud_owner, repository, subject_hash
from app.core.errors import error_response
from app.models import utcnow
from app.services.analytics import VISITOR_COOKIE, ZONE, summarize_records
from app.services.cloudbase import CloudDatabaseError

router = APIRouter(prefix="/api/v1")


@router.post("/events")
def ingest(body: EventIn, request: Request, owner: str | None = Depends(cloud_owner)):
    if (body.name == "guide_viewed") != (body.step is not None):
        return error_response(400, "invalid_event", "事件字段不符合要求")
    token = request.cookies.get(VISITOR_COOKIE)
    if token and len(token) > 128:
        token = None
    try:
        visitor_id = repository(request).rpc("event_ingest", {
            "p_event_id": str(body.id), "p_name": body.name,
            "p_stage": str(body.step) if body.step is not None else "",
            "p_visitor_id": token, "p_new_visitor_id": secrets.token_urlsafe(32), "p_owner_id": owner,
            "p_ip_hash": subject_hash(request, "events-ip", client_address(request)),
        })
    except CloudDatabaseError as exc:
        if exc.code == "23505":
            return error_response(409, "event_conflict", "事件标识已被使用")
        if exc.code == "P0001":
            return error_response(429, "events_rate_limited", "统计上报过于频繁")
        if exc.code == "22023":
            return error_response(400, "invalid_event", "事件字段不符合要求")
        raise
    response = JSONResponse({"ok": True})
    response.set_cookie(VISITOR_COOKIE, visitor_id, httponly=True, secure=request.app.state.settings.cookie_secure, samesite="lax", max_age=90 * 86400)
    return response


@router.post("/chapters/{chapter_id}/read")
def read_chapter(chapter_id: str, request: Request, owner: str | None = Depends(cloud_owner)):
    if owner is None:
        return error_response(404, "not_found", "找不到这一章")
    found = repository(request).rpc("chapter_read", {"p_owner_id": owner, "p_chapter_id": chapter_id})
    return {"ok": True} if found else error_response(404, "not_found", "找不到这一章")


def record(row: dict):
    values = dict(row)
    if values.get("created_at") is not None:
        values["created_at"] = datetime.fromisoformat(values["created_at"])
    return SimpleNamespace(**values)


@router.get("/admin/metrics")
def metrics(request: Request, start: date | None = None, end: date | None = None, admin: dict | None = Depends(cloud_admin)):
    if admin is None:
        return error_response(403, "forbidden", "仅管理员可以查看运营数据")
    now = utcnow()
    today = now.astimezone(ZONE).date()
    start = start or today - timedelta(days=6)
    end = end or today
    if end < start or (end - start).days > 89 or end > today:
        return error_response(400, "date_range_invalid", "请选择截至今天、不超过九十天的日期范围")
    since = datetime.combine(start, time.min, ZONE).astimezone(timezone.utc)
    until = datetime.combine(end + timedelta(days=1), time.min, ZONE).astimezone(timezone.utc)
    cloud = repository(request)
    accounts = [record(row) for row in cloud.rows("accounts", columns="id,owner_id,role,created_at", order="created_at.asc,id.asc")]
    visitors = {row["id"]: row["owner_id"] for row in cloud.rows("analytics_visitors", columns="id,owner_id", order="id.asc")}
    previous = [record(row) for row in cloud.rows("analytics_events", filters={"name": "eq.guide_viewed", "stage": "eq.0", "created_at": "lt." + since.isoformat()}, columns="owner_id,visitor_id", order="id.asc")]
    rows = [record(row) for row in cloud.rows("analytics_events", filters={"and": "(created_at.gte." + since.isoformat() + ",created_at.lt." + min(until + timedelta(days=31), now).isoformat() + ")"}, order="created_at.asc,id.asc")]
    result = summarize_records(accounts, visitors, previous, rows, since, until, now)
    states = Counter(row["status"] for row in cloud.rows("draft_tasks", columns="status", order="id.asc"))
    result["tasks"] = [{"status": status, "count": count} for status, count in sorted(states.items())]
    return result
