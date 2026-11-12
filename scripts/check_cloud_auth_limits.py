import hashlib
import json
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.cloudbase import CloudRepository


def main():
    values = dotenv_values(ROOT / "backend/.env")
    cloud = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    ip_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
    account_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
    try:
        def attempt(_index):
            return cloud.rpc("auth_admit", {"p_ip_hash": ip_hash, "p_account_hash": account_hash})
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(attempt, range(12)))
        if results.count(True) != 10 or results.count(False) != 2:
            raise AssertionError("真实云端并发登录限流未严格执行十次限制")
        rows = list(cloud.rows("auth_attempts", filters={"subject_hash": "eq." + account_hash}, columns="id", order="id.asc"))
        if len(rows) != 10:
            raise AssertionError("真实云端登录尝试记录数量不符")
        result = {"parallel_requests": 12, "admitted": 10, "rejected": 2, "persisted_attempts": 10, "real_cloud_database": True, "model_calls": 0}
        (ROOT / ".intermediate/cloud-auth-limit-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        for subject in [ip_hash, account_hash]:
            cloud.remove("auth_attempts", {"subject_hash": "eq." + subject})
        cloud.close()


if __name__ == "__main__":
    main()
