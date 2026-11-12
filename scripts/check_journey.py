import json
import secrets
import sys
import time
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.main import create_app
from app.models import Account, AnalyticsEvent, AnalyticsVisitor, Owner
from app.services.accounts import password_hash


def main():
    output = ROOT / ".intermediate/journey-verification" / uuid4().hex
    output.mkdir(parents=True)
    settings = Settings(_env_file=None, audio_fernet_key=Fernet.generate_key().decode(), data_dir=str(output), auth_pepper=secrets.token_urlsafe(40))
    app = create_app(settings)
    credentials = {"username": "journey-admin", "password": secrets.token_urlsafe(24)}
    owner_id = str(uuid4())
    with app.state.db_factory() as db:
        db.add(Owner(id=owner_id))
        db.flush()
        db.add(Account(id=str(uuid4()), owner_id=owner_id, username=credentials["username"], password_hash=password_hash(credentials["password"]), role="admin"))
        db.commit()
    checks = []

    def expect(response, status, label):
        if response.status_code != status:
            raise AssertionError(f"{label}: HTTP {response.status_code}")
        checks.append(label)
        return response.json()

    def payload(name, session_id, page, visit_id, duration_ms=0):
        return {"id": str(uuid4()), "name": name, "session_id": session_id, "page_id": page, "page_visit_id": visit_id, "duration_ms": duration_ms, "version": 2}

    session_id, visit_id = str(uuid4()), str(uuid4())
    with TestClient(app) as client, TestClient(app) as admin, TestClient(app) as reordered:
        expect(client.get("/api/v1/admin/metrics"), 403, "匿名用户读取统计被拒绝")
        expect(admin.post("/api/v1/auth/password-login", json=credentials), 200, "真实管理员密码登录")
        leave = payload("page_leave", session_id, "brand", visit_id)
        expect(reordered.post("/api/v1/events", json=leave), 200, "首次无Cookie离开事件先到达")
        view = payload("page_view", session_id, "brand", visit_id)
        expect(client.post("/api/v1/events", json=view), 200, "同会话无Cookie浏览事件后到达")
        reordered.cookies.clear()
        expect(reordered.post("/api/v1/events", json={"id": str(uuid4()), "name": "guide_viewed", "step": 0, "session_id": session_id}), 200, "引导事件无Cookie仍采用共享会话")
        expect(client.post("/api/v1/events", json=view), 200, "重复事件幂等接收")
        if client.cookies.get("yitan_visitor") != reordered.cookies.get("yitan_visitor"):
            raise AssertionError("乱序无Cookie事件被分配为不同访客")
        checks.append("乱序会话合并为同一真实访客")
        started = time.monotonic()
        time.sleep(1.1)
        expect(client.post("/api/v1/events", json=payload("page_heartbeat", session_id, "brand", visit_id, int((time.monotonic() - started) * 1000))), 200, "真实计时页面心跳入库")
        expect(client.post("/api/v1/events", json=payload("page_view", session_id, "sample_book", str(uuid4()))), 200, "第二个页面浏览入库")
        measured = expect(admin.get("/api/v1/admin/metrics"), 200, "管理员读取扩展统计")
        summary = measured["journey"]["summary"]
        if summary["visitors"] != 1 or summary["sessions"] != 1 or summary["page_views"] != 2 or summary["active_sessions"] != 1 or summary["exited_sessions"] != 0:
            raise AssertionError("真实匿名会话、页面去重或活动窗口统计错误")
        brand = next(page for page in measured["journey"]["pages"] if page["id"] == "brand")
        if brand["views"] != 1 or brand["avg_duration_seconds"] < 1:
            raise AssertionError("页面去重或真实停留计时错误")
        checks.append("真实访客1人会话1次浏览2页且仍在访问")
        checks.append("重试不增加浏览次数且停留来自真实计时")
        if not measured["journey"]["page_tracking_started_at"]:
            raise AssertionError("缺少真实页面采集开始时间")
        checks.append("页面采集起点来自首个真实事件")
        invalid = payload("page_view", session_id, "/profiles/private-name", str(uuid4()))
        expect(client.post("/api/v1/events", json=invalid), 400, "拒绝原始URL和个人标识页面字段")
        invalid = payload("page_view", session_id, "brand", str(uuid4()), -1)
        expect(client.post("/api/v1/events", json=invalid), 400, "拒绝负数停留时间")
        expect(client.post("/api/v1/events", json={"id": str(uuid4()), "name": "page_view"}), 400, "缺少页面字段时拒绝入库")
        admin_session, admin_visit = str(uuid4()), str(uuid4())
        expect(admin.post("/api/v1/events", json=payload("page_view", admin_session, "books", admin_visit)), 200, "管理员产品体验真实入库")
        filtered = expect(admin.get("/api/v1/admin/metrics"), 200, "默认排除管理员")
        included = expect(admin.get("/api/v1/admin/metrics?include_admin=true"), 200, "明确选项包含管理员诊断")
        if filtered["journey"]["summary"]["visitors"] != 1 or included["journey"]["summary"]["visitors"] != 2:
            raise AssertionError("管理员诊断开关错误")
        checks.append("管理员体验不会计入真实用户人数")
        invitation = expect(admin.post("/api/v1/admin/invitations"), 200, "创建实际注册邀请码")
        registered = expect(client.post("/api/v1/auth/register", json={"username": "journey-family-" + uuid4().hex[:8], "password": secrets.token_urlsafe(24), "invitation": invitation["invitation"]}), 200, "真实邀请码注册家庭")
        me = expect(client.get("/api/v1/me"), 200, "读取实际家庭身份")
        expect(client.post("/api/v1/profiles", json={"elder_name": "访客统计验证"}), 200, "真实家庭创建老人档案")
        linked = expect(admin.get("/api/v1/admin/metrics"), 200, "登录后匿名浏览与家庭合并")
        if linked["journey"]["summary"]["visitors"] != 1 or linked["journey"]["summary"]["new_registrations"] != 1 or next(stage for stage in linked["journey"]["funnel"] if stage["id"] == "profile")["visitors"] != 1:
            raise AssertionError("匿名注册合并或跳过完整引导后的实际到达统计错误")
        checks.append("跳过六页引导仍统计实际注册与建档")
        with app.state.db_factory() as db:
            account = db.scalar(select(Account).where(Account.owner_id == me["owner_id"]))
            account.analytics_excluded = True
            db.commit()
        excluded = expect(admin.get("/api/v1/admin/metrics"), 200, "明确测试账号标记后默认排除")
        diagnostic = expect(admin.get("/api/v1/admin/metrics?include_test=true"), 200, "诊断选项查看实际验证账号")
        if excluded["journey"]["summary"]["visitors"] != 0 or diagnostic["journey"]["summary"]["visitors"] != 1:
            raise AssertionError("测试账号筛选错误")
        checks.append("已标记验证账号与用户统计分离")
        old_visitor = client.cookies.get("yitan_visitor")
        expect(client.post("/api/v1/auth/logout"), 200, "首个家庭退出登录")
        invitation = expect(admin.post("/api/v1/admin/invitations"), 200, "为第二个真实家庭创建邀请")
        expect(client.post("/api/v1/auth/register", json={"username": "journey-other-" + uuid4().hex[:8], "password": secrets.token_urlsafe(24), "invitation": invitation["invitation"]}), 200, "同一浏览器注册第二个家庭")
        expect(client.post("/api/v1/events", json=payload("page_view", session_id, "books", str(uuid4()))), 200, "切换家庭后复用标签页会话发送浏览")
        if client.cookies.get("yitan_visitor") == old_visitor:
            raise AssertionError("跨家庭复用了前一家庭访客身份")
        diagnostic = expect(admin.get("/api/v1/admin/metrics?include_test=true"), 200, "两个家庭历史分别计数")
        if diagnostic["journey"]["summary"]["visitors"] != 2:
            raise AssertionError("两个家庭被错误合并")
        checks.append("跨家庭切换保留独立历史身份")
        expect(admin.get("/api/v1/admin/metrics?start=2020-01-01&end=2030-01-01"), 400, "拒绝非法日期区间")
        before = diagnostic["journey"]["summary"]
        for current in (admin, client):
            expect(current.post("/api/v1/auth/logout"), 200, "正常退出验证会话")
    reopened = create_app(settings)
    with TestClient(reopened) as client:
        expect(client.post("/api/v1/auth/password-login", json=credentials), 200, "服务重新创建后管理员仍可登录")
        restored = expect(client.get("/api/v1/admin/metrics?include_test=true"), 200, "重新创建服务后统计从实际数据库恢复")
        if restored["journey"]["summary"] != before:
            raise AssertionError("重启后的实际统计发生变化")
    result = {"passed": len(checks), "checks": checks, "model_calls": 0, "real_database": str(output / "yitan.db"), "real_http_application": True}
    (ROOT / ".intermediate/journey-verification/result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": len(checks), "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
