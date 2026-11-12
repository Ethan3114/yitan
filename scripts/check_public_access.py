import base64
import hashlib
import hmac
import json
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.services.cloudbase import CloudRepository


def denied_websocket(url, origin, cookie=""):
    headers = {"Origin": origin, "Connection": "Upgrade", "Upgrade": "websocket", "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": base64.b64encode(secrets.token_bytes(16)).decode()}
    if cookie:
        headers["Cookie"] = cookie
    endpoint = urlparse(url)._replace(scheme="https").geturl()
    with httpx.Client(timeout=30) as client:
        response = client.get(endpoint, headers=headers)
    if response.status_code != 403:
        raise AssertionError("实时连接未返回HTTP403拒绝")


def main():
    settings = Settings(_env_file=ROOT / "backend/.env.deploy")
    frontend = dotenv_values(ROOT / "frontend/.env.deploy")
    web, api = frontend["PUBLIC_ORIGIN"].rstrip("/"), frontend["BACKEND_URL"].rstrip("/")
    if not web.startswith("https://") or not api.startswith("https://"):
        raise ValueError("只能验证已经发布的公网 HTTPS 服务")
    credentials = json.loads((ROOT / ".intermediate/private/cloud-admin.json").read_text())
    manifest = json.loads((ROOT / "data/migration/li/manifest.json").read_text())
    source = json.loads((ROOT / "data/real-interviews/qwen-plus-evaluation-c19c367a-8d2c-49df-83be-7114b8061f91/book.json").read_text(encoding="utf-8"))
    repository = CloudRepository(settings.cloudbase_env_id, settings.cloudbase_api_key)
    clients = [httpx.Client(base_url=web, timeout=90, headers={"Origin": web}) for _ in range(4)]
    admin, first, second, anonymous = clients
    invitation_ids, owners = [], []
    checks = []
    def expect(response, status, label):
        if response.status_code != status:
            raise AssertionError(f"{label}: HTTP {response.status_code}")
        checks.append(label)
        print(json.dumps({"verified": label}, ensure_ascii=False), flush=True)
        return response.json()
    try:
        logged_in = admin.post("/api/v1/auth/password-login", json={"username": credentials["username"], "password": credentials["password"]})
        expect(logged_in, 200, "公网管理员账号密码登录")
        session_header = logged_in.headers.get("set-cookie", "").lower()
        if "secure" not in session_header or "httponly" not in session_header:
            raise AssertionError("公网会话 Cookie 缺少安全属性")
        checks.append("公网会话使用Secure和HttpOnly")
        for client in [first, second]:
            invitation = expect(admin.post("/api/v1/admin/invitations"), 200, "公网创建一次性注册邀请")
            invitation_ids.append(invitation["id"])
            username = "access-check-" + secrets.token_hex(8)
            expect(client.post("/api/v1/auth/register", json={"username": username, "password": secrets.token_urlsafe(32), "invitation": invitation["invitation"]}), 200, "公网邀请码真实注册独立家庭")
            me = expect(client.get("/api/v1/me"), 200, "公网注册会话可以读取本人身份")
            owners.append(me["owner_id"])
        li_url = "/api/v1/profiles/" + manifest["cloud_profile_id"]
        detail = expect(admin.get(li_url), 200, "管理员读取私有李奶奶完整成稿")
        chapters = [chapter for call in detail["calls"] for chapter in call["chapters"]]
        actual = [{"title": c["title"], "paragraphs": [p["text"] for p in c["paragraphs"]]} for c in chapters]
        expected = [{"title": c["title"], "paragraphs": [p["text"] for p in c["paragraphs"]]} for c in source["chapters"]]
        if actual != expected or sum(len(p) for c in actual for p in c["paragraphs"]) != 5366:
            raise AssertionError("公网私有回忆录与7章5366字完整来源不符")
        checks.append("公网7章5366字与选定完整来源逐段一致")
        expect(anonymous.get(li_url), 404, "匿名访问私有回忆录被拒绝")
        expect(first.get(li_url), 404, "新家庭访问李奶奶私有回忆录被拒绝")
        a = expect(first.post("/api/v1/profiles", json={"elder_name": "公网权限验证A"}), 200, "公网家庭A真实建档")
        b = expect(second.post("/api/v1/profiles", json={"elder_name": "公网权限验证B"}), 200, "公网家庭B真实建档")
        expect(second.get("/api/v1/profiles/" + a["id"]), 404, "公网家庭B读取家庭A被拒绝")
        expect(first.get("/api/v1/profiles/" + b["id"]), 404, "公网家庭A读取家庭B被拒绝")
        expect(first.get("/api/v1/admin/metrics"), 403, "公网普通用户读取管理面板被拒绝")
        expect(first.post("/api/v1/profiles", headers={"Origin": "https://untrusted.invalid"}, json={"elder_name": "跨站写入应被拒绝"}), 403, "公网跨站业务写入被拒绝")
        with httpx.Client(base_url=api, timeout=30) as direct:
            expect(direct.get("/api/v1/me"), 403, "直接访问后端绕过可信代理被拒绝")
        parsed = urlparse(web)
        socket_url = "wss://" + parsed.netloc + "/api/v1/calls/" + secrets.token_hex(16) + "/live"
        denied_websocket(socket_url, web)
        checks.append("公网匿名实时连接在访问语音服务前被拒绝")
        cookie = "; ".join(name + "=" + value for name, value in first.cookies.items())
        denied_websocket(socket_url, "https://untrusted.invalid", cookie)
        checks.append("公网跨站实时连接在访问语音服务前被拒绝")
        username = "ip-check-" + secrets.token_hex(8)
        for address in ["198.51.100.21", "203.0.113.22"]:
            response = anonymous.post("/api/v1/auth/password-login", json={"username": username, "password": secrets.token_urlsafe(32)}, headers={"X-Client-IP": address, "X-Envoy-External-Address": address, "X-Forwarded-For": address, "X-Real-IP": address})
            expect(response, 401, "伪造代理地址的错误登录仍由真实认证处理")
        account_hash = hmac.new(settings.auth_pepper.encode(), ("account:" + username).encode(), hashlib.sha256).hexdigest()
        attempts = list(repository.rows("auth_attempts", filters={"subject_hash": "eq." + account_hash}, order="created_at.asc"))
        if len(attempts) != 2:
            raise AssertionError("无法唯一核对两次地址伪造验证")
        ip_hashes = []
        for attempt in attempts:
            paired = [row for row in repository.rows("auth_attempts", filters={"created_at": "eq." + attempt["created_at"]}, order="id.asc") if row["subject_hash"] != account_hash]
            if len(paired) != 1:
                raise AssertionError("无法唯一关联真实来源地址的限流记录")
            ip_hashes.append(paired[0]["subject_hash"])
        if ip_hashes[0] != ip_hashes[1]:
            raise AssertionError("伪造转发地址改变了后台用于限流的来源身份")
        checks.append("伪造转发头无法改变后台真实来源限流身份")
    finally:
        # 邀请归属固定管理员，只按本次创建的编号清理。
        for identifier in invitation_ids:
            repository.remove("registration_invites", {"id": "eq." + identifier})
        for owner in owners:
            for table in ["analytics_events", "audit_logs", "profiles", "sessions", "accounts"]:
                repository.remove(table, {"owner_id": "eq." + owner})
            repository.remove("owners", {"id": "eq." + owner})
        if admin.cookies:
            logout = admin.post("/api/v1/auth/logout")
            if logout.status_code != 200:
                raise RuntimeError("验证管理员会话退出失败")
        for client in clients:
            client.close()
        repository.close()
    result = {"web_url": web, "api_url": api, "passed": len(checks), "checks": checks, "private_source_chapters": 7, "private_source_characters": 5366, "verification_accounts_removed": True, "existing_admin_and_source_unchanged": True, "model_calls": 0}
    (ROOT / ".intermediate/public-access-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": len(checks), "verification_accounts_removed": True, "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
