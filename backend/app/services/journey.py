from collections import defaultdict
from datetime import timedelta

from app.services.analytics import ZONE, aware

PAGE_NAMES = {
    "brand": "品牌首页", "guide_memory": "回忆引导", "guide_voice": "语音讲述介绍",
    "guide_book": "整理成书介绍", "guide_family": "家人阅读介绍", "guide_role": "选择记录对象",
    "sample_book": "李奶奶样书", "login": "登录", "register": "注册", "recover": "恢复密码",
    "books": "我的回忆录", "profile_create": "建立老人档案", "profile": "老人档案",
    "call": "访谈", "chapter": "章节阅读", "memoir": "整本阅读", "family": "家人展示",
    "account": "我的账户", "photos": "相册", "reviews": "事实核对", "editions": "历史版本", "other": "其他页面",
}
PAGE_EVENTS = {"page_view", "page_heartbeat", "page_leave"}
BUSINESS_STAGES = [
    ("visited", "进入体验", None), ("registered", "注册家庭", "account_registered"),
    ("profile", "建立老人档案", "profile_created"), ("interview", "开始访谈", "call_connected"),
    ("recorded", "完成有效访谈", "recording_saved"), ("memoir", "生成回忆录", "memoir_completed"),
    ("read", "阅读自己的回忆录", "memoir_read"),
]


def summarize_journey(accounts, visitors, previous, rows, since, until, now, *, include_admin=False, include_test=False):
    admin_owners = {account.owner_id for account in accounts if account.role == "admin"}
    test_owners = {account.owner_id for account in accounts if getattr(account, "analytics_excluded", False)}
    excluded = (set() if include_admin else admin_owners) | (set() if include_test else test_owners)

    def identity(row):
        owner = row.owner_id or visitors.get(row.visitor_id)
        if owner in excluded:
            return None
        return "account:" + owner if owner else "visitor:" + row.visitor_id if row.visitor_id else None

    history = [row for row in previous if identity(row)]
    all_rows = [row for row in rows if identity(row)]
    selected = [row for row in all_rows if since <= aware(row.created_at) < min(until, now)]
    actors = {identity(row) for row in selected}
    names = defaultdict(set)
    by_day = defaultdict(list)
    first_seen = {}
    for row in history + all_rows:
        actor = identity(row)
        occurred = aware(row.created_at)
        first_seen[actor] = min(first_seen.get(actor, occurred), occurred)
    for row in selected:
        actor = identity(row)
        names[row.name].add(actor)
        by_day[aware(row.created_at).astimezone(ZONE).date()].append(row)
    completed = {identity(row) for row in selected if row.name == "recording_saved" and (row.duration_seconds or 0) >= 60}
    page_rows = [row for row in selected if row.name in PAGE_EVENTS and getattr(row, "session_id", None) and getattr(row, "page_id", None) in PAGE_NAMES and getattr(row, "page_visit_id", None)]
    sessions = defaultdict(list)
    for row in page_rows:
        sessions[(identity(row), row.session_id)].append(row)
    all_session_activity = {}
    session_views = defaultdict(list)
    session_pages = defaultdict(list)
    for row in history + all_rows:
        if getattr(row, "session_id", None):
            key = (identity(row), row.session_id)
            all_session_activity[key] = max(all_session_activity.get(key, aware(row.created_at)), aware(row.created_at))
            if row.name == "page_view":
                session_views[key].append(row)
            if row.name in PAGE_EVENTS and getattr(row, "page_id", None) in PAGE_NAMES:
                session_pages[key].append(row)
    pages = {key: {"id": key, "label": label, "actors": set(), "visits": {}, "entries": 0, "exits": 0, "active_sessions": 0, "completed_sessions": 0} for key, label in PAGE_NAMES.items()}
    ended = active = bounced = 0
    returned_actors = {actor for actor in actors if first_seen[actor] < since}
    actor_session_starts = defaultdict(list)
    daily_sessions = defaultdict(set)
    for key, events in sessions.items():
        events.sort(key=lambda row: (aware(row.created_at), row.id))
        views = sorted(session_views[key], key=lambda row: (aware(row.created_at), row.id))
        full_pages = sorted(session_pages[key], key=lambda row: (aware(row.created_at), row.id))
        first = views[0] if views else full_pages[0]
        actor_session_starts[key[0]].append(aware(first.created_at))
        for row in events:
            daily_sessions[aware(row.created_at).astimezone(ZONE).date()].add(key)
        if views and since <= aware(first.created_at) < until:
            pages[first.page_id]["entries"] += 1
        for row in events:
            page = pages[row.page_id]
            page["actors"].add(key[0])
            visit_key = (key[0], row.page_visit_id)
            visit = page["visits"].setdefault(visit_key, {"viewed": False, "duration_ms": 0})
            visit["viewed"] |= row.name == "page_view"
            visit["duration_ms"] = max(visit["duration_ms"], row.duration_ms or 0)
        last = full_pages[-1]
        latest = all_session_activity[key]
        session_ended = latest + timedelta(minutes=30) <= now and latest < until
        if session_ended:
            ended += 1
            pages[last.page_id]["exits"] += 1
            bounced += int(len({row.page_visit_id for row in full_pages}) == 1)
            for page_id in {row.page_id for row in full_pages}:
                pages[page_id]["completed_sessions"] += 1
        elif latest + timedelta(minutes=30) > now:
            active += 1
            pages[last.page_id]["active_sessions"] += 1
    returned_actors.update(actor for actor, starts in actor_session_starts.items() if len(starts) > 1)
    page_output = []
    for page in pages.values():
        visits = [visit for visit in page["visits"].values() if visit["viewed"]]
        page_output.append({"id": page["id"], "label": page["label"], "visitors": len(page["actors"]), "views": len(visits), "entries": page["entries"], "exits": page["exits"], "active_sessions": page["active_sessions"], "exit_rate": page["exits"] / page["completed_sessions"] if page["completed_sessions"] else None, "avg_duration_seconds": sum(visit["duration_ms"] for visit in visits) / len(visits) / 1000 if visits else None})
    trend = []
    day = since.astimezone(ZONE).date()
    while day < until.astimezone(ZONE).date():
        events = by_day[day]
        day_actors = {identity(row) for row in events}
        trend.append({"date": day.isoformat(), "visitors": len(day_actors), "sessions": len(daily_sessions[day]), "started_interviews": len({identity(row) for row in events if row.name == "call_connected"}), "completed_interviews": len({identity(row) for row in events if row.name == "recording_saved" and (row.duration_seconds or 0) >= 60}), "completed_memoirs": len({identity(row) for row in events if row.name == "memoir_completed"}), "returned_visitors": len({actor for actor in day_actors if first_seen[actor].astimezone(ZONE).date() < day})})
        day += timedelta(days=1)
    count = len(actors)
    funnel = []
    for identifier, label, event in BUSINESS_STAGES:
        reached = actors if event is None else completed if event == "recording_saved" else names[event]
        funnel.append({"id": identifier, "label": label, "visitors": len(reached), "rate": len(reached) / count if count else None})
    all_visible = history + all_rows
    collection = min((aware(row.created_at) for row in all_visible), default=None)
    page_collection = min((aware(row.created_at) for row in all_visible if row.name in PAGE_EVENTS), default=None)
    return {
        "summary": {"visitors": count, "sessions": len(sessions), "page_views": sum(page["views"] for page in page_output), "new_registrations": len(names["account_registered"]), "started_interviews": len(names["call_connected"]), "completed_interviews": len(completed), "completed_memoirs": len(names["memoir_completed"]), "returned_visitors": len(returned_actors), "active_sessions": active, "exited_sessions": ended, "experience_rate": len(names["call_connected"]) / count if count else None, "bounce_rate": bounced / ended if ended else None},
        "trend": trend, "funnel": funnel, "pages": page_output,
        "collection_started_at": collection.isoformat() if collection else None,
        "page_tracking_started_at": page_collection.isoformat() if page_collection else None,
        "excluded": {"admin_accounts": len(admin_owners), "test_accounts": len(test_owners), "admin_events": sum((row.owner_id or visitors.get(row.visitor_id)) in admin_owners for row in rows if since <= aware(row.created_at) < until), "test_events": sum((row.owner_id or visitors.get(row.visitor_id)) in test_owners for row in rows if since <= aware(row.created_at) < until)},
        "filters": {"include_admin": include_admin, "include_test": include_test},
        "definitions": ["访客按匿名浏览器识别，登录后与家庭合并；同一家庭多人使用计为一个身份，人数为估算。", "会话按浏览器标签页标识统计；连续三十分钟无新活动后结束。", "退出页面是已结束会话的最后一页；离开单个页面不等于离开产品，仍在访问单独统计。", "阶段人数分别统计真实到达，允许跳过引导和已有家庭直接使用；完整顺序漏斗保留为独立观察。", "完成有效访谈要求成功保存至少六十秒录音；完成回忆录以服务端生成成功为准。", "页面停留来自可见页面的浏览器计时；历史未采集页面事件的数据不推测退出和停留。", "回访人数指区间内再次发起会话，或区间前已访问并在本区间返回；留存按注册家庭和北京时间自然日统计。", "历史资料导入不计为新成书，样书阅读单独统计，不计为本人回忆录阅读。"],
    }
