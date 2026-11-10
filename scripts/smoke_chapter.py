"""有 DeepSeek 钥匙时整理一段固定原话，并写入阶段 2-2 冒烟记录。没有钥匙不造记录。"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings
from app.services.draft import _ask

STAGE = sys.argv[1] if len(sys.argv) > 1 else "阶段2-2"
RECORD = ROOT / "docs" / "evidence" / STAGE / "冒烟记录.md"
SAMPLE = "天不亮就跟着我爹下田。红薯藤子扯下来还能喂猪。"


async def main() -> int:
    settings = get_settings()
    if not settings.writing_ready:
        print("待验：没有 DEEPSEEK_API_KEY，不写冒烟记录")
        return 0
    started = datetime.now()
    result = "通过"
    try:
        draft = await _ask(settings, SAMPLE)
        if not draft.paragraphs:
            result = "失败"
    except Exception:
        result = "失败"
    elapsed = f"{(datetime.now() - started).total_seconds():.1f}s"
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    if not RECORD.exists():
        RECORD.write_text(
            "# 冒烟记录（冒烟脚本自动写入，不得手填）\n"
            "- 产品：忆谈\n"
            f"- 阶段：{STAGE}\n"
            f"- 模型：DeepSeek / {settings.deepseek_model}\n"
            f"- 写入时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
            "| 时间 | 链路 | 请求 ID | 输入 token | 输出 token | 首字耗时 | 总耗时 | 结构校验 | 重试次数 | 结果 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n",
            encoding="utf-8",
        )
    with RECORD.open("a", encoding="utf-8") as handle:
        handle.write(
            f"| {datetime.now():%Y-%m-%d %H:%M:%S} | 写成回忆 | - | - | - | - | {elapsed} | JSON | 0 | {result} |\n"
        )
    print(result)
    return 0 if result == "通过" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
