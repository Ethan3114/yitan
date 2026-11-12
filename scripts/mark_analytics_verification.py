import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.services.cloudbase import CloudRepository


def main():
    state = json.loads((ROOT / ".intermediate/public-runtime/account.json").read_text())
    if not re.fullmatch(r"release-[a-f0-9]{16}", state["username"]):
        raise ValueError("仅可标记现存公网发布验证账号")
    settings = Settings(_env_file=ROOT / "backend/.env.deploy")
    repository = CloudRepository(settings.cloudbase_env_id, settings.cloudbase_api_key)
    account = repository.one("accounts", filters={"username": "eq." + state["username"]}, columns="id,owner_id,role")
    if account is None or account["role"] != "member":
        raise ValueError("验证账号不存在或角色不符")
    updated = repository.update("accounts", {"id": "eq." + account["id"], "owner_id": "eq." + account["owner_id"]}, {"analytics_excluded": True})
    if len(updated) != 1 or updated[0]["analytics_excluded"] is not True:
        raise RuntimeError("验证账号统计标记未保存")
    repository.close()
    print(json.dumps({"verification_accounts_marked": 1, "user_data_unchanged": True}))


if __name__ == "__main__":
    main()
