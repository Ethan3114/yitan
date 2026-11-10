import asyncio
import shutil
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings
from app.services.draft import MODEL_METRICS, compose
from app.services.writing_trace import WritingTrace


async def main():
    settings = get_settings()
    assert settings.writing_ready and settings.writing_model == "qwen3.7-plus"
    source = next(line for line in (ROOT / "data/real-interviews/li-01-tingwu.txt").read_text().splitlines() if line.startswith("[57:32 "))
    metrics = []
    token = MODEL_METRICS.set(metrics)
    trace = WritingTrace(settings, "plus-production-smoke-" + str(uuid4()), source)
    book = await compose(settings, source, trace)
    assert book.chapters and all(c.paragraphs for c in book.chapters)
    trace.finish("completed", metrics)
    MODEL_METRICS.reset(token)
    record = ROOT / "docs/evidence/阶段3-2/冒烟记录.md"
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if record.exists():
        archive = ROOT / "data/writing/smoke-history"
        archive.mkdir(exist_ok=True)
        shutil.copy2(record, archive / f"{now:%Y%m%d-%H%M%S}.md")
    record.write_text(f"# 冒烟记录（脚本自动生成）\n- 模型：阿里云北京 / {settings.writing_model} 思考模式\n\n| 时间 | 链路 | 请求 ID | 输入 token | 输出 token | 耗时 | 结构校验 | 重试次数 | 结果 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
    with record.open("a") as handle:
        for m in metrics:
            usage = m["usage"]
            handle.write(f"| {now:%Y-%m-%d %H:%M:%S} | Plus生产适配/真实访谈片段/抽取写作审查 | {m['model']} {m['request_id']} | {usage.get('prompt_tokens', 0)} | {usage.get('completion_tokens', 0)} | {m['elapsed']:.2f}s | Pydantic/引用/事件覆盖 | {m['retries']} | 通过 |\n")
    cost = sum(((m["usage"]["prompt_tokens"] - m["usage"].get("prompt_tokens_details", {}).get("cached_tokens", 0)) * 2 + m["usage"].get("prompt_tokens_details", {}).get("cached_tokens", 0) * .4 + m["usage"]["completion_tokens"] * 8) / 1000000 for m in metrics)
    print(f"生产写作适配真实验证通过；模型 {settings.writing_model}；{len(metrics)} 次调用；费用估算 {cost:.4f} 元")


if __name__ == "__main__":
    asyncio.run(main())
