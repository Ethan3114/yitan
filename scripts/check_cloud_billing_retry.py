import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.cloudbase import CloudDatabaseError, CloudRepository


def main():
    values = dotenv_values(ROOT / "backend/.env")
    cloud = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    manifest = json.loads((ROOT / "data/migration/li/manifest.json").read_text())
    files = {row["role"]: ROOT / row["path"] for row in manifest["files"]}
    book, source = json.loads(files["memoir"].read_text()), files["transcript"].read_text()
    owner, profile, call = [str(uuid4()) for _ in range(3)]
    checks = []
    def require(value, label):
        if not value:
            raise AssertionError(label)
        checks.append(label)
    def blocked(code, label):
        try:
            cloud.rpc("enqueue_draft", {"p_owner": owner, "p_call": call, "p_request": str(uuid4()), "p_replace": True})
        except CloudDatabaseError as exc:
            require(exc.code == code, label)
        else:
            raise AssertionError(label)
    cloud.insert("owners", {"id": owner})
    try:
        cloud.rpc("import_book", {"p_import_id": "billing-" + uuid4().hex, "p_owner_id": owner, "p_profile_id": profile, "p_call_id": call, "p_name": manifest["author"], "p_title": manifest["title"], "p_source_sha256": hashlib.sha256(source.encode()).hexdigest(), "p_book": book, "p_transcript": source})
        cloud.update("profiles", {"id": "eq." + profile, "owner_id": "eq." + owner}, {"source": "live"})
        task = cloud.rpc("enqueue_draft", {"p_owner": owner, "p_call": call, "p_request": str(uuid4()), "p_replace": True})
        token = str(uuid4())
        claimed = cloud.rpc("claim_draft", {"p_task": task["id"], "p_token": token})
        require(claimed is not None, "隔离真实成稿任务领取成功")
        reservation = hashlib.sha256(uuid4().bytes).hexdigest()
        cloud.rpc("model_begin", {"p_id": reservation, "p_owner": owner, "p_call": call, "p_task": task["id"], "p_token": token})
        cloud.update("draft_tasks", {"id": "eq." + task["id"], "lease_token": "eq." + token}, {"status": "failed", "error_code": "processing_interrupted", "lease_token": None, "lease_until": None})
        blocked("P0101", "未确认模型结果时新的请求不能再次进入收费队列")
        require(len(list(cloud.rows("draft_tasks", filters={"call_id": "eq." + call}, columns="id", order="id.asc"))) == 1, "被拒绝的重复整理没有遗留新任务")
        cloud.remove("writing_cache", {"id": "eq." + reservation, "owner_id": "eq." + owner})
        next_task = cloud.rpc("enqueue_draft", {"p_owner": owner, "p_call": call, "p_request": str(uuid4()), "p_replace": True})
        token = str(uuid4())
        require(cloud.rpc("claim_draft", {"p_task": next_task["id"], "p_token": token}) is not None, "没有未知结果后可领取新的隔离任务")
        require(cloud.rpc("transcription_begin", {"p_owner": owner, "p_call": call, "p_task": next_task["id"], "p_token": token, "p_object": "yitan/verification/" + next_task["id"]}) is True, "转写发送前的待确认状态先保存")
        cloud.update("draft_tasks", {"id": "eq." + next_task["id"], "lease_token": "eq." + token}, {"status": "failed", "error_code": "processing_interrupted", "lease_token": None, "lease_until": None})
        blocked("P0102", "未确认转写编号时新的请求不能重复发起转写")
        require(len(list(cloud.rows("draft_tasks", filters={"call_id": "eq." + call}, columns="id", order="id.asc"))) == 2, "转写重复请求被拒绝后没有遗留新任务")
        result = {"passed": len(checks), "checks": checks, "real_cloud_database": True, "model_calls": 0, "transcription_calls": 0, "known_provider_id_resume": "requires_real_provider_receipt"}
        (ROOT / ".intermediate/cloud-billing-retry-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": len(checks), "model_calls": 0, "transcription_calls": 0, "known_provider_id_resume": "待真实提供方编号验证"}, ensure_ascii=False))
    finally:
        for table in ["writing_runs", "writing_cache", "draft_tasks", "paragraph_evidence", "paragraphs", "chapters", "memory_items", "fact_items", "transcripts", "imports", "analytics_events", "audit_logs"]:
            cloud.remove(table, {"owner_id": "eq." + owner})
        cloud.remove("calls", {"owner_id": "eq." + owner})
        cloud.remove("book_settings", {"profile_id": "eq." + profile})
        cloud.remove("profiles", {"owner_id": "eq." + owner})
        cloud.remove("owners", {"id": "eq." + owner})
        cloud.close()


if __name__ == "__main__":
    main()
