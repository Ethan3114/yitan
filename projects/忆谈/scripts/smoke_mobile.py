import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import Transcript
from app.services.draft import MODEL_METRICS, compose
from app.services.grounding import elder_text, keep_grounded


async def main():
    settings = get_settings()
    selection = json.loads((ROOT / ".intermediate/validation/selection.json").read_text())
    with make_session_factory(make_engine(settings.sqlalchemy_url))() as db:
        rows = list(db.scalars(select(Transcript).where(Transcript.call_id == selection["call_id"])))
        source = elder_text({row.track: row.text for row in rows})
    assert source and settings.writing_ready
    metrics = []
    token = MODEL_METRICS.set(metrics)
    book = await compose(settings, source)
    assert any(keep_grounded(p.text, p.source_quote, source) for chapter in book.chapters for p in chapter.paragraphs)
    MODEL_METRICS.reset(token)
    record = ROOT / "docs/evidence/阶段3-1/冒烟记录.md"
    record.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if not record.exists():
        record.write_text(f"# 冒烟记录（脚本自动生成）\n- 产品：忆谈\n- 阶段：阶段3-1\n- 模型：DeepSeek / {settings.deepseek_writing_model}\n\n| 时间 | 链路 | 请求 ID | 输入 token | 输出 token | 总耗时 | 结构校验 | 重试次数 | 结果 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n", encoding="utf-8")
    with record.open("a", encoding="utf-8") as handle:
        for metric in metrics:
            usage = metric["usage"]
            handle.write(f"| {now:%Y-%m-%d %H:%M:%S} | 完整语料撰写与审查 | {metric['request_id']} | {usage.get('prompt_tokens', '-')} | {usage.get('completion_tokens', '-')} | {metric['elapsed']:.2f}s | JSON/Pydantic及出处 | {metric['retries']} | 通过 |\n")
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8012", timeout=30) as client:
        response = await client.post("/api/v1/auth/login", json={"code": settings.invite_code})
        response.raise_for_status()
        request_id = f"real-{time.time_ns()}"
        route = f"/api/v1/calls/{selection['call_id']}/draft-tasks"
        first = await client.post(route, json={"request_id": request_id, "replace": True})
        first.raise_for_status()
        task_id = first.json()["id"]
        second = await client.post(route, json={"request_id": request_id, "replace": True})
        second.raise_for_status()
        assert task_id == second.json()["id"]
        for _ in range(3600):
            result = await client.get(f"/api/v1/draft-tasks/{task_id}")
            result.raise_for_status()
            task = result.json()
            if task["status"] in {"succeeded", "failed"}:
                break
            await asyncio.sleep(2)
        assert task["status"] == "succeeded", task["error_code"]
        profile = (await client.get(f"/api/v1/profiles/{selection['profile_id']}")).json()
        call = next(item for item in profile["calls"] if item["id"] == selection["call_id"])
        assert call["chapters"] and call["task"]["id"] == task_id
        selection["chapter_id"] = call["chapters"][0]["id"]
        selection["task_id"] = task_id
        (ROOT / ".intermediate/validation/selection.json").write_text(json.dumps(selection), encoding="utf-8")
    print("真实模型结构与出处验证通过；HTTP 整理任务、重复提交和结果读取通过")


if __name__ == "__main__":
    asyncio.run(main())
