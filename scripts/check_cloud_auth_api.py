import json
import hashlib
import hmac
import os
import secrets
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.api.cloud_accounts import router as accounts_router
from app.api.cloud_analytics import router as analytics_router
from app.core.config import Settings
from app.core.errors import error_body
from app.services.accounts import password_hash
from app.services.cloudbase import CloudRepository


def app_factory():
    values = dotenv_values(ROOT / "backend/.env")
    app = FastAPI()
    app.state.settings = Settings(deployment_mode="cloud", cookie_secure=True, auth_pepper=os.environ["AUTH_PEPPER"])
    app.state.cloud_repository = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    app.include_router(accounts_router)
    app.include_router(analytics_router)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, _exc: RequestValidationError):
        return JSONResponse(status_code=400, content=error_body("invalid_input", "填写的内容不完整"))

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def main():
    values = dotenv_values(ROOT / "backend/.env")
    cloud = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    run_id = uuid4().hex
    run_dir = ROOT / ".intermediate/cloud-auth-checks" / run_id
    run_dir.mkdir(parents=True, mode=0o700)
    admin_owner, admin_id = str(uuid4()), str(uuid4())
    admin_username = "verify-admin-" + run_id[:16]
    username_a, username_b = "verify-a-" + run_id[:16], "verify-b-" + run_id[:16]
    password, admin_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    owned = [admin_owner]
    subjects = []
    event_ids = []
    checks = []
    cloud.insert("owners", {"id": admin_owner})
    cloud.insert("accounts", {"id": admin_id, "owner_id": admin_owner, "username": admin_username, "password_hash": password_hash(admin_password), "role": "admin"})
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"https://127.0.0.1:{port}"
    environment = dict(os.environ, AUTH_PEPPER=secrets.token_urlsafe(32), TMPDIR=str(ROOT / ".intermediate/tmp"))
    def subject(category, value):
        return hmac.new(environment["AUTH_PEPPER"].encode(), (category + ":" + value).encode(), hashlib.sha256).hexdigest()
    subjects.extend([subject("ip", "127.0.0.1"), subject("events-ip", "127.0.0.1")])
    subjects.extend(subject("account", value) for value in [admin_username, username_a, username_b, "verify-revoked-" + run_id[:12]])
    context = ssl.create_default_context(cafile=str(ROOT / ".intermediate/tls/ca/rootCA.pem"))
    log = (run_dir / "server.log").open("w")
    process = None

    def start():
        child = subprocess.Popen([str(ROOT / "backend/.venv/bin/python"), "-m", "uvicorn", "scripts.check_cloud_auth_api:app_factory", "--factory", "--host", "127.0.0.1", "--port", str(port), "--ssl-keyfile", str(ROOT / ".intermediate/tls/server-key.pem"), "--ssl-certfile", str(ROOT / ".intermediate/tls/server.pem")], cwd=ROOT, env=environment, stdout=log, stderr=log)
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError("云端账户验证服务启动失败")
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    return child
            time.sleep(0.1)
        child.terminate()
        child.wait(timeout=10)
        raise RuntimeError("云端账户验证服务启动超时")

    def expect(response, status, label):
        if response.status_code != status:
            raise AssertionError(f"{label}: expected {status}, got {response.status_code}")
        checks.append(label)
        return response.json()

    try:
        process = start()
        with httpx.Client(base_url=origin, verify=context, timeout=60) as admin, httpx.Client(base_url=origin, verify=context, timeout=60) as first, httpx.Client(base_url=origin, verify=context, timeout=60) as second:
            expect(first.get("/api/v1/admin/accounts"), 403, "匿名账户管理被拒绝")
            expect(first.post("/api/v1/auth/login", json={"code": "yitan-local"}), 403, "线上停用共享邀请码登录")
            response = admin.post("/api/v1/auth/password-login", json={"username": admin_username, "password": admin_password})
            expect(response, 200, "真实云端管理员密码登录")
            if "Secure" not in response.headers["set-cookie"] or "HttpOnly" not in response.headers["set-cookie"]:
                raise AssertionError("会话 Cookie 缺少生产安全属性")
            checks.append("HTTPS 安全 Cookie 被实际客户端接收")
            baseline = expect(admin.get("/api/v1/admin/metrics"), 200, "读取操作前真实统计")
            invite = expect(admin.post("/api/v1/admin/invitations"), 200, "管理员创建真实云端邀请码")["invitation"]
            event_id = str(uuid4())
            for step in range(6):
                payload = {"id": event_id if step == 0 else str(uuid4()), "name": "guide_viewed", "step": step}
                event_ids.append(payload["id"])
                expect(first.post("/api/v1/events", json=payload), 200, f"云端记录真实引导第{step+1}页")
                if step == 0:
                    expect(first.post("/api/v1/events", json=payload), 200, "云端重复事件幂等去重")
                    expect(second.post("/api/v1/events", json=payload), 409, "其他访客无法复用事件标识")
            expect(first.post("/api/v1/auth/register", json={"username": username_a.upper(), "password": password, "invitation": invite}), 200, "邀请码注册自动创建家庭及安全会话")
            owner_a = expect(first.get("/api/v1/me"), 200, "读取家庭 A 真实云端身份")["owner_id"]
            owned.append(owner_a)
            expect(second.post("/api/v1/auth/register", json={"username": username_b, "password": password, "invitation": invite}), 400, "已使用云端邀请码再次注册被拒绝")
            invite2 = expect(admin.post("/api/v1/admin/invitations"), 200, "管理员创建第二个云端邀请码")["invitation"]
            expect(second.post("/api/v1/auth/register", json={"username": username_b, "password": password, "invitation": invite2}), 200, "第二个家庭真实云端注册")
            owner_b = expect(second.get("/api/v1/me"), 200, "读取家庭 B 真实云端身份")["owner_id"]
            owned.append(owner_b)
            if owner_a == owner_b:
                raise AssertionError("两个注册家庭身份相同")
            expect(first.get("/api/v1/admin/accounts"), 403, "普通家庭无法读取云端账户管理")
            expect(first.post("/api/v1/admin/invitations"), 403, "普通家庭无法创建云端邀请码")
            expect(first.post("/api/v1/events", json={"id": str(uuid4()), "name": "memoir_completed"}), 400, "浏览器无法伪造云端成稿成功")
            expect(first.post("/api/v1/events", json={"id": str(uuid4()), "name": "registration_viewed", "password": password}), 400, "云端统计接口拒绝隐私字段")
            measured = expect(admin.get("/api/v1/admin/metrics"), 200, "读取云端真实转化漏斗")
            if [row["count"] for row in measured["funnel"][:7]] != [row["count"] + 1 for row in baseline["funnel"][:7]]:
                raise AssertionError("真实引导与注册事件身份连接或漏斗人数错误")
            checks.append("云端漏斗前七步人数与实际操作逐项一致")
            listed = expect(admin.get("/api/v1/admin/accounts"), 200, "管理员列出云端家庭账户")
            account_a = next(row["id"] for row in listed["items"] if row["username"] == username_a)
            recovery = expect(admin.post(f"/api/v1/admin/accounts/{account_a}/recovery"), 200, "管理员创建云端一次性恢复码")["token"]
            subjects.append(subject("account", "recovery:" + hashlib.sha256(recovery.encode()).hexdigest()))
            new_password = secrets.token_urlsafe(32)
            expect(first.post("/api/v1/auth/recover", json={"token": recovery, "password": new_password}), 200, "真实云端恢复密码")
            expect(first.get("/api/v1/me"), 401, "云端密码恢复立即撤销旧会话")
            expect(first.post("/api/v1/auth/recover", json={"token": recovery, "password": new_password}), 400, "云端恢复码重复使用被拒绝")
            expect(first.post("/api/v1/auth/password-login", json={"username": username_a, "password": password}), 401, "云端旧密码失效")
            expect(first.post("/api/v1/auth/password-login", json={"username": username_a, "password": new_password}), 200, "云端新密码真实登录")
            extra = expect(admin.post("/api/v1/admin/invitations"), 200, "创建待撤销邀请码")
            expect(admin.delete("/api/v1/admin/invitations/" + extra["id"]), 200, "管理员撤销云端邀请码")
            expect(second.post("/api/v1/auth/register", json={"username": "verify-revoked-" + run_id[:12], "password": password, "invitation": extra["invitation"]}), 400, "撤销后的邀请码不能注册")
            process.terminate()
            process.wait(timeout=45)
            process = start()
            if expect(first.get("/api/v1/me"), 200, "服务重启后云端会话保留")["owner_id"] != owner_a:
                raise AssertionError("重启后的家庭归属变化")
            repeat = expect(admin.get("/api/v1/admin/metrics"), 200, "服务重启后云端统计保留")
            if repeat["funnel"] != measured["funnel"]:
                raise AssertionError("重启后的漏斗人数变化")
            expect(first.post("/api/v1/auth/logout"), 200, "真实云端退出登录")
            expect(first.get("/api/v1/me"), 401, "云端退出后会话不可用")
        result = {"passed": len(checks), "checks": checks, "real_cloud_database": True, "real_tls_http": True, "model_calls": 0}
        (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": len(checks), "result": str(run_dir / "result.json")}, ensure_ascii=False))
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=45)
        # 只清理本次随机账户及其自身事件，不接触既有管理员或家庭资料。
        visitor_ids = set()
        for event_id in event_ids:
            event = cloud.one("analytics_events", filters={"id": "eq." + event_id}, columns="visitor_id")
            cloud.remove("analytics_events", {"id": "eq." + event_id})
            if event and event["visitor_id"]:
                visitor_ids.add(event["visitor_id"])
        for visitor_id in visitor_ids:
            cloud.remove("analytics_browser_sessions", {"visitor_id": "eq." + visitor_id})
            cloud.remove("analytics_visitors", {"id": "eq." + visitor_id})
        for username in [username_a, username_b]:
            account = cloud.one("accounts", filters={"username": "eq." + username}, columns="owner_id")
            if account and account["owner_id"] not in owned:
                owned.append(account["owner_id"])
        for owner_id in owned:
            for visitor in cloud.rows("analytics_visitors", filters={"owner_id": "eq." + owner_id}, columns="id", order="id.asc"):
                cloud.remove("analytics_browser_sessions", {"visitor_id": "eq." + visitor["id"]})
                cloud.remove("analytics_events", {"visitor_id": "eq." + visitor["id"]})
            for table in ["analytics_events", "audit_logs", "sessions"]:
                cloud.remove(table, {"owner_id": "eq." + owner_id})
            cloud.remove("analytics_visitors", {"owner_id": "eq." + owner_id})
            account = cloud.one("accounts", filters={"owner_id": "eq." + owner_id}, columns="id")
            if account:
                cloud.remove("password_recoveries", {"account_id": "eq." + account["id"]})
        cloud.remove("registration_invites", {"created_by": "eq." + admin_owner})
        for owner_id in owned:
            cloud.remove("accounts", {"owner_id": "eq." + owner_id})
            cloud.remove("owners", {"id": "eq." + owner_id})
        for key in subjects:
            cloud.remove("auth_attempts", {"subject_hash": "eq." + key})
        cloud.close()
        log.close()


if __name__ == "__main__":
    main()
