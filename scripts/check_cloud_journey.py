import hashlib
import json
import secrets
import sys
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.services.cloudbase import CloudDatabaseError, CloudRepository


def main():
    settings = Settings(_env_file=ROOT / "backend/.env.deploy")
    repository = CloudRepository(settings.cloudbase_env_id, settings.cloudbase_api_key)
    state = json.loads((ROOT / ".intermediate/public-runtime/account.json").read_text())
    account = repository.one("accounts", filters={"username": "eq." + state["username"]}, columns="owner_id,analytics_excluded")
    if account is None or account["analytics_excluded"] is not True:
        raise ValueError("只能使用已经排除统计的真实发布验证家庭")
    session_id, visit_id = str(uuid4()), str(uuid4())
    identifiers = []
    visitors = set()
    checks = []
    ip_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()

    def event(name, page="brand", duration=0):
        return {"p_event_id": str(uuid4()), "p_name": name, "p_stage": "", "p_visitor_id": None, "p_new_visitor_id": secrets.token_urlsafe(32), "p_owner_id": account["owner_id"], "p_ip_hash": ip_hash, "p_session_id": session_id, "p_page_id": page, "p_page_visit_id": visit_id, "p_duration_ms": duration}

    def ingest(payload):
        identifier = repository.rpc("event_ingest", payload)
        identifiers.append(payload["p_event_id"])
        visitors.add(identifier)
        return identifier

    try:
        first = ingest(event("page_leave"))
        checks.append("真实云事务先接收无Cookie离开事件")
        view = event("page_view")
        if ingest(view) != first or ingest(view) != first:
            raise AssertionError("真实云事务未按会话关联或幂等")
        checks.extend(["无Cookie浏览事件与离开事件归同一访客", "真实云事件重复提交不增加记录"])
        guide = event("guide_viewed", page=None, duration=None)
        guide.update(p_stage="0", p_page_visit_id=None)
        if ingest(guide) != first:
            raise AssertionError("引导与页面访客身份分离")
        checks.append("真实引导事件共享会话身份")
        conflict = {**view, "p_page_id": "sample_book"}
        try:
            repository.rpc("event_ingest", conflict)
        except CloudDatabaseError as exc:
            if exc.code != "23505":
                raise
            checks.append("同一事件编号更换页面被真实事务拒绝")
        else:
            raise AssertionError("事件冲突未拒绝")
        invalid = event("page_view", page="private-name-or-url")
        try:
            repository.rpc("event_ingest", invalid)
        except CloudDatabaseError as exc:
            if exc.code != "22023":
                raise
            checks.append("云事务拒绝非白名单页面")
        else:
            raise AssertionError("非白名单页面未拒绝")
        rows = list(repository.rows("analytics_events", filters={"session_id": "eq." + session_id}, columns="id,visitor_id,owner_id,name", order="created_at.asc,id.asc"))
        if len(rows) != 3 or len({row["visitor_id"] for row in rows}) != 1 or any(row["owner_id"] != account["owner_id"] for row in rows):
            raise AssertionError("真实云持久记录与会话归属不一致")
        checks.append("实际持久化三条去重事件且归属同一验证家庭")
        with httpx.Client(timeout=30) as client:
            response = client.post(f"https://{settings.cloudbase_env_id}.api.tcloudbasegateway.com/v1/rdb/rest/rpc/yitan_event_ingest", json=event("page_view"))
            if response.status_code not in {401, 403}:
                raise AssertionError(f"匿名调用云事务未拒绝：HTTP {response.status_code}")
        checks.append("匿名公网调用数据库事务被拒绝")
    finally:
        # 仅清理本次随机会话与事件，验证家庭原有资料保持完整。
        repository.remove("analytics_events", {"session_id": "eq." + session_id})
        repository.remove("analytics_browser_sessions", {"id": "eq." + session_id})
        for visitor in visitors:
            repository.remove("analytics_visitors", {"id": "eq." + visitor})
        repository.remove("auth_attempts", {"subject_hash": "eq." + ip_hash})
        repository.close()
    result = {"passed": len(checks), "checks": checks, "model_calls": 0, "verification_events_removed": True, "existing_family_data_unchanged": True}
    (ROOT / ".intermediate/cloud-journey-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": len(checks), "model_calls": 0, "verification_events_removed": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
