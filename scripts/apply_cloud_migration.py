import argparse
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="执行忆谈专用云数据库迁移")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--env-id", required=True)
    parser.add_argument("--cli", type=Path, required=True)
    args = parser.parse_args()
    migration = args.file.resolve(strict=True)
    if not migration.is_relative_to(ROOT / "backend" / "migrations"):
        raise SystemExit("只能执行忆谈 migrations 目录中的文件")
    result = subprocess.run([str(args.cli.resolve(strict=True)), "db", "execute", "-e", args.env_id, "--sql", migration.read_text(encoding="utf-8"), "--json"], capture_output=True, text=True, env=dict(os.environ, TMPDIR=str(ROOT / ".intermediate" / "tmp")))
    if result.returncode:
        body = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
        error = body.get("error") or body.get("Error") or {}
        print(json.dumps({"returncode": result.returncode, "code": error.get("code"), "message": error.get("message"), "request_id": error.get("requestId")}, ensure_ascii=False))
        raise SystemExit("迁移命令失败")
    body = json.loads(result.stdout)
    if "error" in body or "Error" in body:
        raise SystemExit("云端返回迁移错误")
    print(json.dumps({"file": migration.name, "executed": True, "request_id": body.get("data", {}).get("RequestId")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
