import json
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.accounts import password_hash, token_hash


def main():
    values = dotenv_values(ROOT / "backend" / ".env")
    base = f"https://{values['CLOUDBASE_ENV_ID']}.api.tcloudbasegateway.com/v1/rdb/rest/"
    headers = {"Authorization": "Bearer " + values["CLOUDBASE_API_KEY"], "Prefer": "return=representation"}
    ids = [str(uuid4()) for _ in range(3)]
    account_ids = [str(uuid4()), str(uuid4())]
    invite_id = str(uuid4())
    session_ids = [secrets.token_urlsafe(27), secrets.token_urlsafe(27)]
    invitation = secrets.token_urlsafe(32)
    encoded = password_hash(secrets.token_urlsafe(32))
    now = datetime.now(timezone.utc)
    checks = []
    with httpx.Client(base_url=base, headers=headers, timeout=30) as client:
        def request(method, endpoint, **kwargs):
            response = client.request(method, endpoint, **kwargs)
            if response.status_code not in {200, 201, 204}:
                raise RuntimeError(f"云端验证失败：{endpoint} HTTP {response.status_code}")
            return response.json() if response.content else None

        request("POST", "yitan_owners", json={"id": ids[0]})
        try:
            request("POST", "yitan_registration_invites", json={"id": invite_id, "token_hash": token_hash(invitation), "created_by": ids[0], "expires_at": (now + timedelta(minutes=10)).isoformat()})
            def register(index):
                return client.post("rpc/yitan_register", json={"p_invite_hash": token_hash(invitation), "p_username": "verification-" + account_ids[index], "p_password_hash": encoded, "p_owner_id": ids[index + 1], "p_account_id": account_ids[index], "p_session_id": session_ids[index], "p_session_days": 1})
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(register, [0, 1]))
            if sorted(response.status_code for response in responses) != [200, 400]:
                raise AssertionError("同一邀请码并发注册没有严格保证一次使用")
            checks.append("真实云端并发注册只允许一个账户成功")
            winner = next(index for index, response in enumerate(responses) if response.status_code == 200)
            loser = 1 - winner
            if request("GET", "yitan_owners", params={"id": "eq." + ids[loser + 1]}) != []:
                raise AssertionError("失败注册遗留了不完整家庭记录")
            checks.append("失败事务没有遗留家庭记录")
            accounts = request("GET", "yitan_accounts", params={"id": "eq." + account_ids[winner], "select": "id,owner_id,role"})
            if len(accounts) != 1 or accounts[0]["role"] != "member":
                raise AssertionError("注册账户角色不正确")
            checks.append("注册账户固定为普通家庭角色")
            events = request("GET", "yitan_analytics_events", params={"owner_id": "eq." + ids[winner + 1], "select": "name"})
            if events != [{"name": "account_registered"}]:
                raise AssertionError("注册成功事件没有随事务准确保存")
            checks.append("注册事件与账户同一事务保存")
            recovery = secrets.token_urlsafe(32)
            request("POST", "yitan_password_recoveries", json={"id": str(uuid4()), "account_id": account_ids[winner], "token_hash": token_hash(recovery), "expires_at": (now + timedelta(minutes=10)).isoformat()})
            recovered = request("POST", "rpc/yitan_recover_password", json={"p_token_hash": token_hash(recovery), "p_password_hash": password_hash(secrets.token_urlsafe(32))})
            if recovered is not True:
                raise AssertionError("云端密码恢复失败")
            if request("GET", "yitan_sessions", params={"owner_id": "eq." + ids[winner + 1]}) != []:
                raise AssertionError("重设密码没有撤销旧会话")
            checks.append("云端密码恢复撤销全部旧会话")
            repeated = request("POST", "rpc/yitan_recover_password", json={"p_token_hash": token_hash(recovery), "p_password_hash": encoded})
            if repeated is not False:
                raise AssertionError("云端恢复码被重复使用")
            checks.append("云端恢复码只允许一次使用")
        finally:
            for owner in ids[1:]:
                for table in ["yitan_analytics_events", "yitan_audit_logs", "yitan_sessions"]:
                    request("DELETE", table, params={"owner_id": "eq." + owner})
            for account_id in account_ids:
                request("DELETE", "yitan_password_recoveries", params={"account_id": "eq." + account_id})
                request("DELETE", "yitan_accounts", params={"id": "eq." + account_id})
            request("DELETE", "yitan_registration_invites", params={"id": "eq." + invite_id})
            for owner in ids:
                request("DELETE", "yitan_owners", params={"id": "eq." + owner})
    output = ROOT / ".intermediate" / "cloud-account-verification.json"
    output.write_text(json.dumps({"checks": checks, "passed": len(checks), "verification_records_removed": True, "private_memoirs_uploaded": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": len(checks), "verification_records_removed": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
