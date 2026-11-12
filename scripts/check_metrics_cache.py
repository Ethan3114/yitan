import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as day_time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".intermediate/metrics-cache"
sys.path.insert(0, str(ROOT / "backend"))
from app.api.cloud_analytics import build_metrics, metrics, record
from app.core.config import Settings
from app.main import create_app
from app.models import utcnow
from app.services.analytics import ZONE, summarize_records


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    path.chmod(0o600)


def legacy_metrics(cloud, since, until, now, include_admin, include_test):
    accounts = [record(row) for row in cloud.rows("accounts", columns="id,owner_id,role,created_at,analytics_excluded", order="created_at.asc,id.asc")]
    visitors = {row["id"]: row["owner_id"] for row in cloud.rows("analytics_visitors", columns="id,owner_id", order="id.asc")}
    previous = [record(row) for row in cloud.rows("analytics_events", filters={"created_at": "lt." + since.isoformat()}, order="created_at.asc,id.asc")]
    rows = [record(row) for row in cloud.rows("analytics_events", filters={"and": "(created_at.gte." + since.isoformat() + ",created_at.lt." + min(until + timedelta(days=31), now).isoformat() + ")"}, order="created_at.asc,id.asc")]
    result = summarize_records(accounts, visitors, previous, rows, since, until, now, include_admin=include_admin, include_test=include_test)
    states = Counter(row["status"] for row in cloud.rows("draft_tasks", columns="status", order="id.asc"))
    result["tasks"] = [{"status": status, "count": count} for status, count in sorted(states.items())]
    return result


def response_body(value):
    return JSONResponse(jsonable_encoder(value)).body


def actual_http_checks(origin):
    expected = Settings(_env_file=ROOT / "backend/.env.deploy").web_origin.rstrip("/")
    if not origin.startswith("https://") or origin != expected:
        raise ValueError("HTTP鉴权验证必须使用配置中既有的真实公网HTTPS入口")
    admin = json.loads((ROOT / ".intermediate/private/cloud-admin.json").read_text())
    member = json.loads((ROOT / ".intermediate/public-runtime/account.json").read_text())
    checks = []
    with httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=90) as anonymous, httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=90) as ordinary, httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=90) as manager:
        anonymous_response = anonymous.get("/api/v1/admin/metrics?refresh=true")
        if anonymous_response.status_code != 403:
            raise AssertionError(f"真实公网匿名请求未被拒绝：HTTP {anonymous_response.status_code}")
        checks.append("anonymous_denied")
        for client, credentials in [(ordinary, member), (manager, admin)]:
            response = client.post("/api/v1/auth/password-login", json={"username": credentials["username"], "password": credentials["password"]})
            if response.status_code != 200:
                raise AssertionError(f"真实公网验证账号登录失败：HTTP {response.status_code}")
        ordinary_response = ordinary.get("/api/v1/admin/metrics?refresh=true")
        if ordinary_response.status_code != 403:
            raise AssertionError(f"真实公网普通家庭请求未被拒绝：HTTP {ordinary_response.status_code}")
        checks.append("ordinary_family_denied")
        refresh = manager.get("/api/v1/admin/metrics?refresh=true")
        hit = manager.get("/api/v1/admin/metrics")
        if refresh.status_code != 200 or hit.status_code != 200 or refresh.headers.get("X-Metrics-Cache") != "refresh" or hit.headers.get("X-Metrics-Cache") != "hit" or refresh.content != hit.content:
            raise AssertionError(f"真实公网缓存强刷或命中行为无效：refresh HTTP {refresh.status_code}、hit HTTP {hit.status_code}")
        if "no-store" not in hit.headers.get("Cache-Control", ""):
            raise AssertionError("真实公网聚合数据缺少禁止共享缓存设置")
        checks.extend(["admin_refresh_then_hit_exact", "private_no_store"])
        for client in [ordinary, manager]:
            logged_out = client.post("/api/v1/auth/logout")
            if logged_out.status_code != 200:
                raise AssertionError(f"真实公网验证会话退出失败：HTTP {logged_out.status_code}")
    return checks


def main():
    parser = argparse.ArgumentParser(description="用真实CloudRepository验证统计投影等价、缓存并发/强刷/筛选隔离和实际300秒过期；不调用模型")
    parser.add_argument("--origin", help="新版后端发布后，用该真实公网入口追加HTTP鉴权与缓存验证")
    parser.add_argument("--http-only", action="store_true", help="仅验证已发布公网HTTP，不重复300秒等待")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    WORK.chmod(0o700)
    path = WORK / (datetime.now(ZONE).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6] + ".json")
    result = {"started_at": utcnow().isoformat(), "passed": False, "model_calls": 0, "checks": []}
    if args.http_only:
        if not args.origin:
            raise ValueError("--http-only必须提供--origin")
        result["checks"] = actual_http_checks(args.origin.rstrip("/"))
        result["passed"] = True
        private_json(path, result)
        print(json.dumps({"passed": True, "http_checks": len(result["checks"]), "model_calls": 0}), flush=True)
        return
    settings = Settings(_env_file=ROOT / "backend/.env.deploy")
    if settings.deployment_mode != "cloud":
        raise ValueError("必须使用真实云数据库配置")
    app = create_app(settings)
    cloud = app.state.cloud_repository
    requests = []
    cloud.client.event_hooks["request"].append(lambda request: requests.append(request.method))
    admin = next(cloud.rows("accounts", filters={"role": "eq.admin", "enabled": "eq.true"}, columns="owner_id", page_size=1), None)
    if admin is None:
        raise ValueError("真实数据库不存在可用管理员")
    today = utcnow().astimezone(ZONE).date()

    def endpoint(start=None, include_admin=False, include_test=False, refresh=False):
        request = Request({"type": "http", "app": app, "method": "GET", "path": "/api/v1/admin/metrics", "headers": [], "query_string": b""})
        return metrics(request, start=start, end=today, include_admin=include_admin, include_test=include_test, refresh=refresh, admin=admin)

    try:
        expiry_start = today - timedelta(days=29)
        began = time.monotonic()
        expiry_first = endpoint(expiry_start)
        if expiry_first.status_code != 200 or expiry_first.headers.get("X-Metrics-Cache") != "miss":
            raise AssertionError("实际过期验证未从空缓存开始")
        print(json.dumps({"stage": "expiry_clock_started", "max_age_seconds": 300}), flush=True)
        first_reads = len(requests)
        expiry_hit = endpoint(expiry_start)
        if expiry_hit.body != expiry_first.body or expiry_hit.headers.get("X-Metrics-Cache") != "hit" or len(requests) != first_reads:
            raise AssertionError("命中缓存仍查询数据库或改变完整响应")
        result["checks"].append("real_cache_hit_has_zero_metrics_database_requests")
        fixed = utcnow()
        since = datetime.combine(today - timedelta(days=6), day_time.min, ZONE).astimezone(timezone.utc)
        until = datetime.combine(today + timedelta(days=1), day_time.min, ZONE).astimezone(timezone.utc)
        digests = []
        for include_admin in [False, True]:
            for include_test in [False, True]:
                old = response_body(legacy_metrics(cloud, since, until, fixed, include_admin, include_test))
                projected = response_body(build_metrics(cloud, since, until, fixed, include_admin, include_test))
                if old != projected:
                    raise AssertionError("真实旧全字段查询与新投影查询结果存在差异")
                digests.append(hashlib.sha256(projected).hexdigest())
                first = endpoint(today - timedelta(days=6), include_admin, include_test)
                read_count = len(requests)
                second = endpoint(today - timedelta(days=6), include_admin, include_test)
                if first.headers.get("X-Metrics-Cache") != "miss" or second.headers.get("X-Metrics-Cache") != "hit" or first.body != second.body or len(requests) != read_count:
                    raise AssertionError("真实筛选缓存相互串用或命中后继续查询")
        result["checks"].extend(["four_filter_combinations_match_legacy_byte_for_byte", "four_filter_cache_keys_are_independent"])
        result["equivalence"] = {"as_of": fixed.isoformat(), "combinations": 4, "response_sha256": digests}
        count_before = len(requests)
        fresh = endpoint(today - timedelta(days=6), refresh=True)
        fresh_reads = len(requests) - count_before
        if fresh.headers.get("X-Metrics-Cache") != "refresh" or fresh_reads <= 0 or endpoint(today - timedelta(days=6)).body != fresh.body:
            raise AssertionError("强制刷新没有真实重算并替换同一缓存项")
        result["checks"].append("force_refresh_rebuilds_and_replaces_same_key")
        with ThreadPoolExecutor(max_workers=5) as pool:
            responses = list(pool.map(lambda _: endpoint(today - timedelta(days=13)), range(5)))
        states = Counter(response.headers.get("X-Metrics-Cache") for response in responses)
        if states != {"miss": 1, "hit": 4} or len({response.body for response in responses}) != 1:
            raise AssertionError("真实同筛选并发未合并为一次计算")
        result["checks"].append("five_concurrent_real_database_requests_share_one_build")
        result["database_requests"] = {"initial_miss_including_admin_lookup": first_reads, "force_refresh": fresh_reads}
        while time.monotonic() - began < 301:
            remaining = 301 - (time.monotonic() - began)
            print(json.dumps({"stage": "waiting_for_real_expiry", "remaining_seconds": round(remaining, 1)}), flush=True)
            time.sleep(min(30, remaining))
        count_before = len(requests)
        expired = endpoint(expiry_start)
        if expired.headers.get("X-Metrics-Cache") != "miss" or len(requests) <= count_before or json.loads(expired.body)["as_of"] == json.loads(expiry_first.body)["as_of"]:
            raise AssertionError("实际300秒后未重新查询真实数据库")
        result["checks"].append("actual_300_second_expiry_rebuilds_from_real_database")
        result["expiry_elapsed_seconds"] = round(time.monotonic() - began, 3)
        if args.origin:
            result["checks"].extend(actual_http_checks(args.origin.rstrip("/")))
        result["passed"] = True
    finally:
        private_json(path, result)
        app.state.cloud_files.close()
        cloud.close()
    print(json.dumps({"passed": result["passed"], "checks": len(result["checks"]), "expiry_elapsed_seconds": result["expiry_elapsed_seconds"], "model_calls": 0}), flush=True)


if __name__ == "__main__":
    main()
