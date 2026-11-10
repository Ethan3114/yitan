import json
import sys
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings
from app.core.crypto import build_fernet
from app.db import make_engine, make_session_factory
from app.models import WritingRun
from app.services.draft import chapter_views
from app.services.writing import Catalogue, Plan, source_lines, validate_catalogue, validate_plan


def main():
    settings = get_settings()
    factory = make_session_factory(make_engine(settings.sqlalchemy_url))
    fernet = build_fernet(settings.audio_fernet_key)
    results = json.loads((ROOT / ".intermediate/real-memoir-results.json").read_text())
    evidence = ROOT / "docs/evidence/阶段3-2"
    evidence.mkdir(parents=True, exist_ok=True)
    metrics = []
    report = ["# 真实语料整改验证", "", "| 语料 | 原文行数 | 事件数 | 待核对事项 | 章节 | 段落 | 正文字符 | 原文引用 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    with factory() as db:
        for item in results:
            run = db.scalars(select(WritingRun).where(WritingRun.call_id == item["call_id"], WritingRun.status == "completed").order_by(WritingRun.created_at.desc())).first()
            assert run is not None
            trace = json.loads(fernet.decrypt(Path(run.trace_path).read_bytes()))
            assert trace["status"] == "completed"
            steps = trace["steps"]
            catalogue = Catalogue.model_validate(next(step["data"] for step in steps if step["stage"] == "catalogue"))
            plan = Plan.model_validate([step["data"] for step in steps if step["stage"] == "plan"][-1])
            validate_plan(plan, catalogue.events)
            audit = [step["data"] for step in steps if step["stage"].startswith("book-audit-")][-1]
            assert not audit["issues"] and all(c["accepted"] and not c["issues"] for c in audit["chapters"])
            source = source_lines((ROOT / "data/real-interviews" / item["source"]).read_text())
            assert trace["source_sha256"] == item["source_sha256"]
            validate_catalogue(catalogue, source, source)
            indexed = {line["line"]: line["text"] for line in source}
            chapters = chapter_views(db, run.owner_id, run.call_id)
            assert len(chapters) == len(plan.chapters) == item["chapter_count"]
            key = item["source"].removesuffix("-tingwu.txt")
            snapshots = [json.loads(path.read_text()) for path in (ROOT / "data/real-interviews").glob(f"rewrite-*/{key}-memoir.json")]
            snapshot = next(value for value in snapshots if value["call_id"] == item["call_id"])
            assert chapters == snapshot["chapters"]
            assert sorted(row["detail"] for row in chapters[0]["memories"]) == sorted(paragraph["text"] for chapter in chapters for paragraph in chapter["paragraphs"])
            references = 0
            for chapter in chapters:
                for paragraph in chapter["paragraphs"]:
                    assert paragraph["source_quotes"]
                    for ref in paragraph["source_quotes"]:
                        assert ref["quote"] in indexed[ref["line"]]
                        references += 1
            report.append(f"| {item['name']} | {len(source)} | {len(catalogue.events)} | {sum(len(chapter['facts']) for chapter in chapters)} | {len(chapters)} | {item['paragraph_count']} | {item['body_characters']} | {references} |")
            metrics.extend(trace["metrics"])
            print(f"{item['name']}：完整事件分配、全文审查、全部正文与引用和生成后快照一致")
    archives = [path.parent for path in (ROOT / "data/real-interviews").glob("rewrite-*/li-01-memoir.json") if json.loads(path.read_text())["call_id"] == results[0]["call_id"]]
    assert len(archives) == 1
    preserved = 0
    with sqlite3.connect(archives[0] / "before-import.db") as before, sqlite3.connect(make_url(settings.sqlalchemy_url).database) as current:
        for table, columns in [("chapters", "id,title"), ("paragraphs", "id,text,source_quote")]:
            old_rows = before.execute(f"SELECT {columns} FROM {table}").fetchall()
            current_rows = {row[0]: row for row in current.execute(f"SELECT {columns} FROM {table}").fetchall()}
            assert all(current_rows.get(row[0]) == row for row in old_rows)
            preserved += len(old_rows)
    report.extend(["", f"已将生成前数据库备份与当前数据库比较：原有 {preserved} 条章节及段落记录完整保留。"])
    (evidence / "真实语料整改验证.md").write_text("\n".join(report) + "\n")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    smoke = evidence / "冒烟记录.md"
    if not smoke.exists():
        smoke.write_text(f"# 冒烟记录（脚本自动生成）\n- 模型：DeepSeek / {settings.deepseek_writing_model}\n\n| 时间 | 链路 | 请求 ID | 输入 token | 输出 token | 耗时 | 结构校验 | 重试次数 | 结果 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
    with smoke.open("a") as handle:
        for metric in metrics:
            usage = metric["usage"]
            handle.write(f"| {now:%Y-%m-%d %H:%M:%S} | 完整语料撰写与审查 | {metric['request_id']} | {usage.get('prompt_tokens', '-')} | {usage.get('completion_tokens', '-')} | {metric['elapsed']:.2f}s | Pydantic、原文位置、事件覆盖及语义审查 | {metric['retries']} | 最终稿通过 |\n")


if __name__ == "__main__":
    main()
