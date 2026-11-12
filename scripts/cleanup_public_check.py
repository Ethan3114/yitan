import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.cloudbase import CloudRepository


def main():
    parser = argparse.ArgumentParser(description="保存公网验收费用记录；仅在明确执行时删除该验收账户及其资料")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    directory = ROOT / ".intermediate/public-runtime"
    state_path = directory / "account.json"
    if not state_path.resolve(strict=True).is_relative_to(ROOT / ".intermediate"):
        raise ValueError("验收账户文件必须位于本机忽略目录")
    ignored = subprocess.run(["git", "check-ignore", "--quiet", str(state_path)], cwd=ROOT)
    if ignored.returncode:
        raise ValueError("验收账户文件未被 Git 忽略")
    state = json.loads(state_path.read_text())
    username, owner = state["username"], state["owner_id"]
    if re.fullmatch(r"release-[a-f0-9]{16}", username) is None:
        raise ValueError("只允许清理专用公网验收账户")
    protected = json.loads((ROOT / ".intermediate/private/cloud-admin.json").read_text())
    li = json.loads((ROOT / "data/migration/li/manifest.json").read_text())
    if owner in {protected["owner_id"], li["destination_owner_id"]}:
        raise ValueError("禁止清理管理员或历史回忆录所属家庭")
    values = dotenv_values(ROOT / "backend/.env")
    cloud = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    storage_url = f"https://{values['CLOUDBASE_ENV_ID']}.api.tcloudbasegateway.com/v1/storages/object/yitan-private/"
    try:
        account = cloud.one("accounts", filters={"owner_id": "eq." + owner, "username": "eq." + username}, columns="id,role")
        if account is None or account["role"] != "member":
            raise ValueError("验收账户身份不符或账户已经清理")
        if state.get("account_id") is not None and state["account_id"] != account["id"]:
            raise ValueError("验收账户编号不符")
        tasks = list(cloud.rows("draft_tasks", filters={"owner_id": "eq." + owner}, columns="id,status,step,error_code,transcription_id,created_at,updated_at", order="id.asc"))
        cache = list(cloud.rows("writing_cache", filters={"owner_id": "eq." + owner}, columns="id,status,usage,task_id,created_at", order="id.asc"))
        if any(row["status"] in {"queued", "running"} for row in tasks) or any(row["status"] == "pending" for row in cache):
            raise ValueError("任务仍在处理或存在未确认的收费结果，禁止清理")
        profiles = list(cloud.rows("profiles", filters={"owner_id": "eq." + owner}, columns="id", order="id.asc"))
        if any(row["id"] == li["cloud_profile_id"] for row in profiles):
            raise ValueError("历史回忆录出现在清理范围，禁止继续")
        files = list(cloud.rows("private_files", filters={"owner_id": "eq." + owner}, columns="id,parts,size_bytes", order="id.asc"))
        for item in files:
            if any(not part["object"].startswith(owner + "/") for part in item["parts"]):
                raise ValueError("文件路径不属于验收家庭")
        usage = {
            "captured_at": datetime.now(timezone.utc).isoformat(), "owner_id": owner, "username": username,
            "tasks": tasks, "model_requests": cache,
            "files": len(files), "source_bytes": sum(row["size_bytes"] for row in files),
            "deleted": False,
        }
        receipt = directory / "usage-before-cleanup.json"
        receipt.write_text(json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        receipt.chmod(0o600)
        if not args.execute:
            print(json.dumps({"audited": True, "model_request_records": len(cache), "files": len(files), "deleted": False}, ensure_ascii=False))
            return
        if not all(state.get(key) is True for key in ["voice_verified", "writing_verified", "republish_verified"]):
            raise ValueError("必须先完成真实语音、成稿和重新发布后恢复验证")
        # 先撤销该账户会话，清理期间禁止重新访问；管理员与其他家庭不在筛选条件内。
        cloud.update("accounts", {"id": "eq." + account["id"], "owner_id": "eq." + owner}, {"enabled": False})
        cloud.remove("sessions", {"owner_id": "eq." + owner})
        with httpx.Client(base_url=storage_url, headers={"Authorization": "Bearer " + values["CLOUDBASE_API_KEY"]}, timeout=60, follow_redirects=False) as storage:
            for item in files:
                for part in item["parts"]:
                    response = storage.delete(quote(part["object"], safe="/"))
                    if response.status_code not in {200, 204, 404}:
                        raise RuntimeError("验收文件清理未完成")
        for visitor in cloud.rows("analytics_visitors", filters={"owner_id": "eq." + owner}, columns="id", order="id.asc"):
            cloud.remove("analytics_events", {"visitor_id": "eq." + visitor["id"]})
        for table in ["family_comments", "family_invites", "family_members", "family_publications", "writing_cache", "writing_runs", "text_editions", "draft_tasks", "audio_chunks", "call_timelines", "call_contexts", "analytics_events", "analytics_visitors", "paragraph_evidence", "paragraphs", "chapters", "transcripts", "audio_assets", "memory_items", "fact_items", "photos", "imports", "private_files", "calls", "audit_logs"]:
            cloud.remove(table, {"owner_id": "eq." + owner})
        for profile in profiles:
            cloud.remove("book_settings", {"profile_id": "eq." + profile["id"]})
        cloud.remove("profiles", {"owner_id": "eq." + owner})
        cloud.remove("password_recoveries", {"account_id": "eq." + account["id"]})
        cloud.remove("registration_invites", {"used_by": "eq." + owner})
        cloud.remove("registration_invites", {"created_by": "eq." + owner})
        cloud.remove("accounts", {"id": "eq." + account["id"], "owner_id": "eq." + owner})
        cloud.remove("owners", {"id": "eq." + owner})
        if cloud.one("owners", filters={"id": "eq." + owner}) is not None:
            raise AssertionError("验收家庭仍然存在")
        usage.update(deleted=True, deleted_at=datetime.now(timezone.utc).isoformat())
        receipt.write_text(json.dumps(usage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state.update(cleaned=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"deleted": True, "usage_records_preserved": len(cache), "admin_and_li_preserved": True}, ensure_ascii=False))
    finally:
        cloud.close()


if __name__ == "__main__":
    main()
