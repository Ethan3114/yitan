import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.accounts import password_hash
from app.services.cloudbase import CloudRepository


def main():
    parser = argparse.ArgumentParser(description="创建忆谈唯一初始管理员，凭据仅保存至受保护的本机文件")
    parser.add_argument("--username", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".intermediate"):
        raise SystemExit("管理员凭据必须保存在已忽略的 .intermediate 目录")
    subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(output)], check=True)
    if output.exists():
        raise SystemExit("凭据文件已存在，禁止覆盖")
    values = dotenv_values(ROOT / "backend" / ".env")
    repository = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    password = secrets.token_urlsafe(24)
    owner_id, account_id = str(uuid4()), str(uuid4())
    # 网络中断时凭据仍可保留，按账户标识查询后确认创建结果。
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        json.dump({"username": args.username, "password": password, "owner_id": owner_id, "account_id": account_id, "status": "pending"}, file, ensure_ascii=False)
    result = repository.rpc("bootstrap_admin", {"p_owner_id": owner_id, "p_account_id": account_id, "p_username": args.username, "p_password_hash": password_hash(password)})
    if result["owner_id"] != owner_id or result["account_id"] != account_id:
        raise RuntimeError("管理员创建结果与请求不符")
    with output.open("w") as file:
        json.dump({"username": args.username, "password": password, "owner_id": owner_id, "account_id": account_id, "status": "created"}, file, ensure_ascii=False)
    repository.close()
    print("管理员已创建；凭据仅保存至指定文件，权限 0600")


if __name__ == "__main__":
    main()
