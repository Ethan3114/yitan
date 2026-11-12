import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.models import Account, Base, Owner
from app.services.accounts import password_hash
from app.services.store import new_id


def main():
    run_dir = ROOT / ".intermediate" / "account-checks" / secrets.token_hex(6)
    run_dir.mkdir(parents=True, mode=0o700)
    database = run_dir / "verification.db"
    engine = create_engine("sqlite:///" + str(database))
    Base.metadata.create_all(engine)
    admin_password = secrets.token_urlsafe(24)
    with Session(engine) as db:
        owner = Owner(id=new_id())
        db.add(owner)
        db.flush()
        db.add(Account(id=new_id(), owner_id=owner.id, username="verification-admin", password_hash=password_hash(admin_password), role="admin"))
        db.commit()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        frontend_port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    frontend_origin = f"http://127.0.0.1:{frontend_port}"
    environment = dict(os.environ, DATA_DIR=str(run_dir), DATABASE_URL="sqlite:///" + str(database), AUDIO_FERNET_KEY=Fernet.generate_key().decode(), AUTH_PEPPER=secrets.token_urlsafe(32), DEPLOYMENT_MODE="local", WEB_ORIGIN=frontend_origin, TMPDIR=str(ROOT / ".intermediate" / "tmp"))
    checks = []
    log = (run_dir / "server.log").open("w")

    def start():
        process = subprocess.Popen([str(ROOT / "backend/.venv/bin/python"), "-m", "uvicorn", "app.main:app_factory", "--factory", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT / "backend", env=environment, stdout=log, stderr=log)
        for _ in range(80):
            if process.poll() is not None:
                raise RuntimeError("验证服务启动失败，请检查受保护的本机日志")
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    return process
            time.sleep(0.1)
        process.terminate()
        process.wait(timeout=10)
        raise RuntimeError("验证服务启动超时")

    def expect(response, status, label):
        if response.status_code != status:
            raise AssertionError(f"{label}: expected {status}, got {response.status_code}")
        checks.append(label)
        return response.json()

    process = start()
    try:
        with httpx.Client(base_url=origin, trust_env=False, timeout=20) as admin, httpx.Client(base_url=origin, trust_env=False, timeout=20) as first, httpx.Client(base_url=origin, trust_env=False, timeout=20) as second:
            expect(first.get("/api/v1/admin/metrics"), 403, "匿名访问后台被拒绝")
            expect(admin.post("/api/v1/auth/password-login", json={"username": "verification-admin", "password": admin_password}), 200, "管理员真实密码登录")
            invite = expect(admin.post("/api/v1/admin/invitations"), 200, "创建一次性邀请码")["invitation"]
            for step in range(6):
                event = {"id": new_id(), "name": "guide_viewed", "step": step}
                expect(first.post("/api/v1/events", json=event), 200, f"真实提交引导第{step + 1}页事件")
                if step == 0:
                    expect(first.post("/api/v1/events", json=event), 200, "同一事件重复上报去重")
            password = secrets.token_urlsafe(24)
            expect(first.post("/api/v1/auth/register", json={"username": "verification-family-a", "password": password, "invitation": invite}), 200, "家庭A真实注册")
            expect(second.post("/api/v1/auth/register", json={"username": "verification-family-b", "password": password, "invitation": invite}), 400, "已使用邀请码不能再次注册")
            invite2 = expect(admin.post("/api/v1/admin/invitations"), 200, "创建家庭B邀请码")["invitation"]
            expect(second.post("/api/v1/auth/register", json={"username": "verification-family-b", "password": password, "invitation": invite2}), 200, "家庭B真实注册")
            profile = expect(first.post("/api/v1/profiles", json={"elder_name": "接口验证档案"}), 200, "家庭A真实建档")
            expect(second.get("/api/v1/profiles/" + profile["id"]), 404, "家庭B无法读取家庭A档案")
            expect(first.get("/api/v1/admin/metrics"), 403, "普通账户无法读取运营数据")
            expect(first.post("/api/v1/events", json={"id": new_id(), "name": "memoir_completed"}), 400, "浏览器不能伪造服务端成功事件")
            expect(first.post("/api/v1/events", json={"id": new_id(), "name": "registration_viewed", "password": "must-not-be-stored"}), 400, "统计接口拒绝额外隐私字段")
            expect(first.post("/api/v1/profiles", headers={"Origin": "https://untrusted.invalid"}, json={"elder_name": "拒绝跨站请求"}), 403, "跨站写入请求被拒绝")
            measured = expect(admin.get("/api/v1/admin/metrics"), 200, "管理员读取真实统计")
            if [row["count"] for row in measured["funnel"][:8]] != [1] * 8:
                raise AssertionError("漏斗人数、注册身份关联或事件去重不正确")
            checks.append("漏斗前八步各一个身份且重复事件未增加人数")
            if any(item["eligible"] != 0 or item["rate"] is not None for item in measured["retention"]):
                raise AssertionError("新注册账户不应计入成熟留存样本")
            checks.append("未成熟留存显示待观察")
            listed = expect(admin.get("/api/v1/admin/accounts"), 200, "管理员读取账户清单")
            target = next(item["id"] for item in listed["items"] if item["username"] == "verification-family-a")
            recovery = expect(admin.post(f"/api/v1/admin/accounts/{target}/recovery"), 200, "管理员生成一次性恢复码")["token"]
            new_password = secrets.token_urlsafe(24)
            expect(first.post("/api/v1/auth/recover", json={"token": recovery, "password": new_password}), 200, "真实重设密码")
            expect(first.get("/api/v1/profiles"), 401, "旧会话立即失效")
            expect(first.post("/api/v1/auth/recover", json={"token": recovery, "password": new_password}), 400, "恢复码不能重复使用")
            expect(first.post("/api/v1/auth/password-login", json={"username": "verification-family-a", "password": password}), 401, "旧密码失效")
            expect(first.post("/api/v1/auth/password-login", json={"username": "verification-family-a", "password": new_password}), 200, "新密码可以登录")
            process.terminate()
            process.wait(timeout=10)
            process = start()
            saved = expect(first.get("/api/v1/profiles"), 200, "服务重启后会话与档案保留")
            if saved["items"][0]["id"] != profile["id"]:
                raise AssertionError("服务重启后档案发生变化")
            repeat = expect(admin.get("/api/v1/admin/metrics"), 200, "服务重启后统计保留")
            if repeat["funnel"] != measured["funnel"]:
                raise AssertionError("服务重启后漏斗数据发生变化")
            expect(first.post("/api/v1/auth/logout"), 200, "真实退出登录")
            expect(first.get("/api/v1/profiles"), 401, "退出后不能访问档案")
            browser_invite = expect(admin.post("/api/v1/admin/invitations"), 200, "创建浏览器验证邀请码")["invitation"]
            frontend = subprocess.Popen(["node", "server.mjs"], cwd=ROOT / "frontend", env=dict(environment, PORT=str(frontend_port), NODE_ENV="production", BACKEND_URL=origin), stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if frontend.poll() is not None:
                        raise RuntimeError("前端验证服务启动失败")
                    with socket.socket() as probe:
                        if probe.connect_ex(("127.0.0.1", frontend_port)) == 0:
                            break
                    time.sleep(0.1)
                browser_run = subprocess.run(["node", str(ROOT / "scripts/check_accounts_browser.mjs")], cwd=ROOT / "frontend", env=dict(environment, CHECK_ORIGIN=frontend_origin, CHECK_ADMIN_PASSWORD=admin_password, CHECK_INVITATION=browser_invite, CHECK_PASSWORD=secrets.token_urlsafe(24)), stdout=log, stderr=log)
                if browser_run.returncode:
                    raise RuntimeError("浏览器验证失败，请检查受保护的本机验证日志")
                checks.append("手机宽度下真实注册登录与管理员图表交互通过")
            finally:
                frontend.terminate()
                frontend.wait(timeout=10)
        result = {"passed": len(checks), "checks": checks, "database": str(database), "providers_called": False, "server_restarted": True}
        (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": len(checks), "result": str(run_dir / "result.json")}, ensure_ascii=False))
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()
        engine.dispose()


if __name__ == "__main__":
    main()
