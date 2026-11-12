import json
import os
import subprocess
from datetime import datetime, timezone

from dotenv import dotenv_values

from cloudrun_tools import CLI, ENV_ID, ROOT

import sys
sys.path.insert(0, str(ROOT / "backend"))
from app.services.cloudbase import CloudRepository


def main():
    values = dotenv_values(ROOT / "backend/.env.deploy")
    repository = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    try:
        rows = repository.request("GET", "yitan_draft_tasks", params={"select": "id", "or": f"(status.eq.queued,and(status.eq.running,lease_until.lt.{datetime.now(timezone.utc).isoformat()}))", "limit": "1"})
        if rows:
            raise RuntimeError("存在待处理任务，禁止用空队列验证触发模型")
        result = subprocess.run([str(CLI), "fn", "invoke", "yitan-worker-timer", "--params", "{}", "-e", ENV_ID, "--json"], capture_output=True, text=True, env=dict(os.environ, TMPDIR=str(ROOT / ".intermediate/tmp")))
        if result.returncode:
            raise RuntimeError("定时函数真实调用失败，原始响应已隐藏")
        body = json.loads(result.stdout)["data"]
        nested = body.get("Result") or body
        returned = nested.get("RetMsg")
        if isinstance(returned, str):
            returned = json.loads(returned)
        if returned != {"status": "idle"}:
            raise ValueError("定时函数没有返回空队列结果")
        output = {"checked_at": datetime.now(timezone.utc).isoformat(), "invocation": "real_cloud_function", "function": "yitan-worker-timer", "queue_empty_before": True, "result": returned, "model_calls": 0}
        path = ROOT / ".intermediate/public-runtime/timer-idle.json"
        path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
        print(json.dumps(output, ensure_ascii=False))
    finally:
        repository.close()


if __name__ == "__main__":
    main()
