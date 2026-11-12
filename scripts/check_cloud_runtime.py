import asyncio
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.services.cloud_writing import CloudTrace
from app.services.cloudbase import CloudDatabaseError, CloudRepository
from app.services.writing import WritingError


def main():
    values = dotenv_values(ROOT / "backend/.env")
    repository = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    manifest = json.loads((ROOT / "data/migration/li/manifest.json").read_text())
    files = {item["role"]: ROOT / item["path"] for item in manifest["files"]}
    book = json.loads(files["memoir"].read_text())
    transcript = files["transcript"].read_text()
    owner, other_owner, profile, call = [str(uuid4()) for _ in range(4)]
    import_id = "runtime-" + uuid4().hex
    checks = []
    app = FastAPI()
    app.state.settings = Settings(deployment_mode="cloud")
    app.state.cloud_repository = repository
    app.state.fernet = Fernet(values["AUDIO_FERNET_KEY"].encode())

    def require(value, label):
        if not value:
            raise AssertionError(label)
        checks.append(label)

    def rejected(callback, code, label):
        try:
            callback()
        except CloudDatabaseError as exc:
            require(exc.code == code, label)
        else:
            raise AssertionError(label)

    repository.insert("owners", {"id": owner})
    repository.insert("owners", {"id": other_owner})
    try:
        receipt = repository.rpc("import_book", {"p_import_id": import_id, "p_owner_id": owner, "p_profile_id": profile, "p_call_id": call, "p_name": manifest["author"], "p_title": manifest["title"], "p_source_sha256": hashlib.sha256(transcript.encode()).hexdigest(), "p_book": book, "p_transcript": transcript})
        repository.update("profiles", {"id": "eq." + profile, "owner_id": "eq." + owner}, {"source": "live"})
        require(receipt["chapter_count"] == 7 and receipt["body_characters"] == 5366, "真实李奶奶成稿完整复制到隔离云端家庭")
        rejected(lambda: repository.rpc("enqueue_draft", {"p_owner": other_owner, "p_call": call, "p_request": str(uuid4()), "p_replace": True}), "22023", "其他家庭无法创建本家庭生成任务")
        rejected(lambda: repository.rpc("enqueue_draft", {"p_owner": owner, "p_call": call, "p_request": str(uuid4()), "p_replace": False}), "22023", "已有成稿的重新整理必须明确确认")
        request_id = str(uuid4())
        task = repository.rpc("enqueue_draft", {"p_owner": owner, "p_call": call, "p_request": request_id, "p_replace": True})
        repeated = repository.rpc("enqueue_draft", {"p_owner": owner, "p_call": call, "p_request": request_id, "p_replace": True})
        require(repeated["id"] == task["id"], "同一请求只创建一个持久生成任务")
        tokens = [str(uuid4()), str(uuid4())]
        def claim(token):
            return repository.rpc("claim_draft", {"p_task": task["id"], "p_token": token})
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(claim, tokens))
        require(sum(row is not None for row in claims) == 1, "两个真实并发处理进程只允许一个领取成功")
        winner = next(index for index, row in enumerate(claims) if row is not None)
        token, wrong_token = tokens[winner], tokens[1 - winner]
        task = claims[winner]
        require(repository.rpc("heartbeat_draft", {"p_task": task["id"], "p_owner": owner, "p_token": wrong_token}) is False, "错误领取凭据无法延长租约")
        require(repository.rpc("heartbeat_draft", {"p_task": task["id"], "p_owner": owner, "p_token": token}) is True, "正确处理进程可以持久续期")
        trace = CloudTrace(app, task, token, transcript, time.monotonic() - 1)
        trace.cache_write("runtime-real-book", book)
        require(trace.cache_read("runtime-real-book") == book, "真实完整成稿检查点写入并解密读回一致")
        system = (ROOT / "backend/app/prompts/events.txt").read_text()
        cache_id = trace.key("model:" + json.dumps([system, transcript], ensure_ascii=False))
        reserved = repository.rpc("model_begin", {"p_id": cache_id, "p_owner": owner, "p_call": call, "p_task": task["id"], "p_token": token})
        require(reserved["created"] is True and reserved["status"] == "pending", "模型调用前的未知结果状态先持久保存")
        try:
            asyncio.run(trace.complete(system, transcript))
        except WritingError as exc:
            require(exc.code == "model_outcome_unknown", "已有未知结果请求不会重新调用收费模型")
        else:
            raise AssertionError("未知模型请求被重复执行")
        late_id = hashlib.sha256((import_id + "known-historical-result").encode()).hexdigest()
        repository.rpc("model_begin", {"p_id": late_id, "p_owner": owner, "p_call": call, "p_task": task["id"], "p_token": token})
        expired_at = datetime.fromisoformat(repository.one("draft_tasks", filters={"id": "eq." + task["id"]})["lease_until"])
        print(json.dumps({"waiting_for_real_lease_expiry": True, "seconds": max(0, round((expired_at - datetime.now(timezone.utc)).total_seconds()))}), flush=True)
        while datetime.now(timezone.utc) <= expired_at:
            time.sleep(1)
        require(repository.rpc("heartbeat_draft", {"p_task": task["id"], "p_owner": owner, "p_token": token}) is False, "实际过期的租约无法自行复活")
        try:
            trace.cache_write("runtime-real-book", {"source_sha256": hashlib.sha256(transcript.encode()).hexdigest()})
        except WritingError as exc:
            require(exc.code == "lease_lost", "租约实际过期后禁止旧进程修改检查点")
        else:
            raise AssertionError("过期租约仍可写入检查点")
        require(trace.cache_read("runtime-real-book") == book, "拒绝过期写入后完整成稿保持一致")
        new_token = str(uuid4())
        reclaimed = repository.rpc("claim_draft", {"p_task": task["id"], "p_token": new_token})
        require(reclaimed is not None and reclaimed["lease_token"] == new_token, "实际过期的持久任务可由新处理进程接手")
        rejected(lambda: repository.rpc("model_begin", {"p_id": hashlib.sha256(uuid4().bytes).hexdigest(), "p_owner": owner, "p_call": call, "p_task": task["id"], "p_token": token}), "22023", "旧进程无法创建新的收费模型请求")
        # 载荷来自已经完成的真实成稿，仅验证结果存储事务，不验证模型效果。
        encoded = trace.encode(book)
        finish = {"p_id": late_id, "p_owner": owner, "p_call": call, "p_task": task["id"], "p_token": token, "p_status": "completed", "p_payload": encoded, "p_usage": {}}
        require(repository.rpc("model_finish", {**finish, "p_token": wrong_token}) is False, "其他进程不能代写已发起请求的结果")
        require(repository.rpc("model_finish", finish) is True, "原请求的已知结果可在租约转移后补记且无需重发请求")
        require(repository.rpc("model_finish", finish) is True, "同一已知结果重复保存保持幂等")
        require(repository.rpc("model_finish", {**finish, "p_payload": trace.encode(transcript)}) is False, "已保存的模型结果不能被旧进程覆盖")
        saved = repository.one("writing_cache", filters={"id": "eq." + late_id, "owner_id": "eq." + owner})
        require(trace.decode(saved["payload"]) == book, "不可变结果与真实成稿逐项一致")
        fresh = CloudTrace(app, reclaimed, new_token, transcript, time.monotonic() - 1)
        fresh.cache_write("runtime-real-book", book)
        require(fresh.cache_read("runtime-real-book") == book, "新处理进程可继续保存真实检查点")
        result = {"passed": len(checks), "checks": checks, "real_cloud_database": True, "actual_lease_expiry_waited": True, "source": "existing_selected_li_book", "model_calls": 0, "private_originals_preserved": True}
        (ROOT / ".intermediate/cloud-runtime-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": len(checks), "actual_lease_expiry_waited": True, "model_calls": 0}, ensure_ascii=False))
    finally:
        for table in ["writing_runs", "writing_cache", "draft_tasks", "paragraph_evidence", "paragraphs", "chapters", "memory_items", "fact_items", "transcripts", "imports", "analytics_events", "audit_logs", "call_contexts"]:
            repository.remove(table, {"owner_id": "eq." + owner})
        repository.remove("calls", {"owner_id": "eq." + owner})
        repository.remove("book_settings", {"profile_id": "eq." + profile})
        repository.remove("profiles", {"owner_id": "eq." + owner})
        repository.remove("owners", {"id": "eq." + owner})
        repository.remove("owners", {"id": "eq." + other_owner})
        repository.close()


if __name__ == "__main__":
    main()
