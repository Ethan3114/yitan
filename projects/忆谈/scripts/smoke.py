"""有豆包钥匙时做一次极短接通，并自己写冒烟记录。没有钥匙就退出，不造记录。"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings
from app.services.doubao import RealtimeSession, VoiceError

RECORD = ROOT / "docs" / "evidence" / "阶段2-1" / "冒烟记录.md"


async def main() -> int:
    settings = get_settings()
    if not settings.voice_ready:
        print("待验：没有 DOUBAO_API_KEY，不写冒烟记录")
        return 0
    started = datetime.now()
    session = RealtimeSession(settings)
    result = "通过"
    try:
        await session.connect()
        await session.finish()
    except VoiceError:
        result = "失败"
    except Exception:
        result = "失败"
    elapsed = f"{(datetime.now() - started).total_seconds():.1f}s"
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    if not RECORD.exists():
        RECORD.write_text(
            "# 冒烟记录（冒烟脚本自动写入，不得手填）\n"
            "- 产品：忆谈\n"
            "- 阶段：阶段2-1\n"
            f"- 模型：豆包实时语音 / {settings.doubao_model}\n"
            f"- 写入时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
            "| 时间 | 链路 | 请求 ID | 输入 token | 输出 token | 首字耗时 | 总耗时 | 结构校验 | 重试次数 | 结果 |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n",
            encoding="utf-8",
        )
    with RECORD.open("a", encoding="utf-8") as handle:
        handle.write(
            f"| {datetime.now():%Y-%m-%d %H:%M:%S} | 接通并挂断 | - | - | - | - | {elapsed} | 接通返回 | 0 | {result} |\n"
        )
    print(result)
    return 0 if result == "通过" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
