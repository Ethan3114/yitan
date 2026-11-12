import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.accounts import password_hash, password_matches, token_hash
from app.services.cloudbase import CloudRepository


def main():
    path = ROOT / ".intermediate/private/cloud-admin.json"
    record = json.loads(path.read_text())
    values = dotenv_values(ROOT / "backend/.env")
    repository = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    password, token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    pending = path.with_name("cloud-admin-next.json")
    pending.write_text(json.dumps({**record, "password": password, "status": "rotation_pending"}), encoding="utf-8")
    pending.chmod(0o600)
    repository.insert("password_recoveries", {"id": str(uuid4()), "account_id": record["account_id"], "token_hash": token_hash(token), "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()})
    recovered = repository.rpc("recover_password", {"p_token_hash": token_hash(token), "p_password_hash": password_hash(password)})
    if recovered is not True:
        raise RuntimeError("管理员密码未能重新签发")
    account = repository.one("accounts", filters={"id": "eq." + record["account_id"], "owner_id": "eq." + record["owner_id"]})
    if not password_matches(account["password_hash"], password):
        raise RuntimeError("管理员新凭据校验失败")
    pending.write_text(json.dumps({**record, "password": password, "status": "created"}, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)
    path.chmod(0o600)
    repository.close()
    print(json.dumps({"administrator_password_rotated": True, "old_sessions_revoked": True, "credentials_file_permissions": "0600"}))


if __name__ == "__main__":
    main()
