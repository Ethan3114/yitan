import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings


async def main():
    file = ROOT / ".intermediate/validation/selection.json"
    selected = json.loads(file.read_text())
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8012", timeout=30) as client:
        (await client.post("/api/v1/auth/login", json={"code": get_settings().invite_code})).raise_for_status()
        if sys.argv[1] == "prepare":
            response = await client.post(f"/api/v1/calls/{selected['call_id']}/draft-tasks", json={"request_id": f"restart-{time.time_ns()}", "replace": True})
            response.raise_for_status()
            selected["interrupted_task_id"] = response.json()["id"]
            file.write_text(json.dumps(selected))
            print("已创建真实整理任务，可终止隔离服务以检查恢复")
        else:
            interrupted = (await client.get(f"/api/v1/draft-tasks/{selected['interrupted_task_id']}")).json()
            assert interrupted["status"] == "failed" and interrupted["error_code"] == "service_restarted"
            completed = (await client.get(f"/api/v1/draft-tasks/{selected['task_id']}")).json()
            assert completed["status"] == "succeeded"
            chapter = await client.get(f"/api/v1/chapters/{selected['chapter_id']}")
            chapter.raise_for_status()
            audio = await client.get(f"/api/v1/calls/{selected['voice_call_id']}/audio/user")
            audio.raise_for_status()
            assert len(audio.content) > 44
            print("重启恢复通过：中断任务明确失败可重试、成功任务与原章节保留、真实录音可读取")


if __name__ == "__main__":
    asyncio.run(main())
