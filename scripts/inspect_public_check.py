import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.services.cloudbase import CloudRepository


def main():
    state = json.loads((ROOT / ".intermediate/public-runtime/account.json").read_text())
    settings = Settings(_env_file=ROOT / "backend/.env.deploy")
    repository = CloudRepository(settings.cloudbase_env_id, settings.cloudbase_api_key)
    try:
        rows = list(repository.rows("writing_cache", filters={"call_id": "eq." + state["call_id"], "owner_id": "eq." + state["owner_id"]}, columns="status,usage", order="created_at.asc"))
        task = repository.one("draft_tasks", filters={"id": "eq." + state["task_id"], "owner_id": "eq." + state["owner_id"]}, columns="status,step,error_code") if state.get("task_id") else None
        print(json.dumps({"task": task, "completed_model_requests": sum(bool(item["usage"]) for item in rows), "pending_requests": sum(item["status"] == "pending" for item in rows), "input_tokens": sum(item["usage"].get("prompt_tokens", 0) for item in rows), "output_tokens": sum(item["usage"].get("completion_tokens", 0) for item in rows)}, ensure_ascii=False))
    finally:
        repository.close()


if __name__ == "__main__":
    main()
