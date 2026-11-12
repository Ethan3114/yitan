import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, AnalyticsEvent, AnalyticsVisitor

VISITOR_COOKIE = "yitan_visitor"
ZONE = ZoneInfo("Asia/Shanghai")
FUNNEL = [
    ("guide_viewed", "0", "看到品牌首页"),
    ("guide_viewed", "1", "看到回忆引导"),
    ("guide_viewed", "2", "了解语音讲述"),
    ("guide_viewed", "3", "了解整理成书"),
    ("guide_viewed", "4", "了解家人阅读"),
    ("guide_viewed", "5", "选择记录对象"),
    ("account_registered", "", "注册成功"),
    ("profile_created", "", "建立老人档案"),
    ("call_connected", "", "访谈接通"),
    ("recording_saved", "", "有效录音保存"),
    ("transcription_completed", "", "完整转写完成"),
    ("memoir_completed", "", "回忆录生成完成"),
    ("memoir_read", "", "阅读自己的回忆录"),
]


def server_event(db: Session, name: str, owner: str, entity: str = "", duration: int | None = None, error: str = "", stage: str = ""):
    db.add(AnalyticsEvent(id=secrets.token_hex(16), name=name, owner_id=owner, source="server", entity_id=entity, duration_seconds=duration, error_code=error[:48], stage=stage))


def link_visitor(db: Session, visitor_id: str | None, owner: str):
    visitor = db.get(AnalyticsVisitor, visitor_id) if visitor_id else None
    if visitor is not None and visitor.owner_id is None:
        visitor.owner_id = owner


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def summarize(db: Session, since: datetime, until: datetime, now: datetime, *, include_admin=False, include_test=False) -> dict:
    accounts = list(db.scalars(select(Account)))
    visitors = {row.id: row.owner_id for row in db.scalars(select(AnalyticsVisitor))}
    previous = list(db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.created_at < since).order_by(AnalyticsEvent.created_at, AnalyticsEvent.id)))
    rows = list(db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.created_at >= since, AnalyticsEvent.created_at < min(until + timedelta(days=31), now)).order_by(AnalyticsEvent.created_at, AnalyticsEvent.id)))
    return summarize_records(accounts, visitors, previous, rows, since, until, now, include_admin=include_admin, include_test=include_test)


def summarize_records(accounts, visitors: dict[str, str | None], previous, rows, since: datetime, until: datetime, now: datetime, *, include_admin=False, include_test=False) -> dict:
    from app.services.journey import summarize_journey
    excluded = {account.owner_id for account in accounts if (account.role == "admin" and not include_admin) or (getattr(account, "analytics_excluded", False) and not include_test)}
    previous_entries = set()
    for row in previous:
        if row.name != "guide_viewed" or row.stage != "0":
            continue
        owner = row.owner_id or visitors.get(row.visitor_id)
        previous_entries.add("account:" + owner if owner else "visitor:" + str(row.visitor_id))
    actors: dict[str, list[AnalyticsEvent]] = {}
    failures: dict[tuple[str, str], int] = {}
    names: dict[str, set[str]] = {}
    latest = None
    for row in rows:
        owner = row.owner_id or visitors.get(row.visitor_id)
        if owner in excluded:
            continue
        actor = "account:" + owner if owner else "visitor:" + str(row.visitor_id)
        actors.setdefault(actor, []).append(row)
        occurred = aware(row.created_at)
        latest = occurred
        if occurred < until:
            names.setdefault(row.name, set()).add(actor)
            if row.name == "operation_failed":
                key = (row.stage, row.error_code)
                failures[key] = failures.get(key, 0) + 1
    counts = [0] * len(FUNNEL)
    pending = 0
    for actor, events in actors.items():
        if actor in previous_entries:
            continue
        entry = next((row for row in events if row.name == "guide_viewed" and row.stage == "0" and aware(row.created_at) < until), None)
        if entry is None:
            continue
        deadline = aware(entry.created_at) + timedelta(days=7)
        if deadline > now:
            pending += 1
        index = 0
        for row in events:
            if aware(row.created_at) < aware(entry.created_at) or aware(row.created_at) >= deadline:
                continue
            event_name, stage, _ = FUNNEL[index]
            valid_recording = row.name != "recording_saved" or (row.duration_seconds or 0) >= 60
            if row.name == event_name and (not stage or row.stage == stage) and valid_recording:
                counts[index] += 1
                index += 1
                if index == len(FUNNEL):
                    break
    funnel = []
    for index, (name, stage, label) in enumerate(FUNNEL):
        denominator = counts[index - 1] if index else counts[0]
        funnel.append({"event": name, "stage": stage, "label": label, "count": counts[index], "previous": denominator, "rate": counts[index] / denominator if denominator else None, "not_completed": denominator - counts[index]})
    retention = []
    for day in (1, 7, 30):
        eligible = retained = 0
        for account in accounts:
            registered = aware(account.created_at)
            if account.owner_id in excluded or not since <= registered < until:
                continue
            target = registered.astimezone(ZONE).date() + timedelta(days=day)
            if target >= now.astimezone(ZONE).date():
                continue
            eligible += 1
            active = any(row.name in {"call_connected", "memoir_read", "family_read"} and aware(row.created_at).astimezone(ZONE).date() == target for row in actors.get("account:" + account.owner_id, []))
            retained += int(active)
        retention.append({"day": day, "eligible": eligible, "retained": retained, "rate": retained / eligible if eligible else None})
    journey = summarize_journey(accounts, visitors, previous, rows, since, until, now, include_admin=include_admin, include_test=include_test)
    journey["retention"] = retention
    return {"timezone": "Asia/Shanghai", "since": since.isoformat(), "until": until.isoformat(), "as_of": now.isoformat(), "latest_event_at": latest.isoformat() if latest else None, "funnel": funnel, "observing_cohorts": pending, "retention": retention, "activity": {name: len(identities) for name, identities in names.items()}, "failures": [{"stage": key[0], "code": key[1], "count": count} for key, count in sorted(failures.items())], "excluded_admin_accounts": sum(account.role == "admin" for account in accounts), "measurement_version": 2, "journey": journey}
