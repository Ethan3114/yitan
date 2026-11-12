import copy
import hashlib
import json
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.cloud_books import chapter_views
from app.services.cloud_files import CloudFiles
from app.services.cloudbase import CloudDatabaseError, CloudRepository


def main():
    values = dotenv_values(ROOT / "backend/.env")
    repository = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    fernet = Fernet(values["AUDIO_FERNET_KEY"].encode())
    files = CloudFiles(repository, fernet, values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    owner, wrong_owner = str(uuid4()), str(uuid4())
    directory = ROOT / ".intermediate/commit-checks" / owner
    directory.mkdir(parents=True)
    clip = directory / "actual-source.pcm"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(ROOT / "data/real-interviews/li-01.m4a"), "-t", "0.1", "-ac", "1", "-ar", "16000", "-f", "s16le", str(clip)], check=True)
    pcm = clip.read_bytes()
    book_path = ROOT / "data/real-interviews/qwen-plus-evaluation-c19c367a-8d2c-49df-83be-7114b8061f91/book.json"
    book = json.loads(book_path.read_text(encoding="utf-8"))
    transcript = (ROOT / "data/real-interviews/li-01-tingwu.txt").read_text(encoding="utf-8")
    accepted = [{"title": c["title"], "paragraphs": [{"text": p["text"], "source_quote": p["source_quote"], "source_quotes": p["evidence"]} for p in c["paragraphs"]]} for c in book["chapters"]]
    def encode(value):
        return fernet.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()
    checks = []
    repository.insert("owners", {"id": owner})
    try:
        imported = repository.rpc("import_book", {"p_import_id": "commit-check-" + str(uuid4()), "p_owner_id": owner, "p_profile_id": str(uuid4()), "p_call_id": str(uuid4()), "p_name": "李奶奶", "p_title": "李奶奶的故事", "p_source_sha256": hashlib.sha256(book_path.read_bytes()).hexdigest(), "p_book": book, "p_transcript": transcript})
        profile_id = imported["profile_id"]
        call = repository.one("calls", filters={"profile_id": "eq." + profile_id, "owner_id": "eq." + owner})
        previous = chapter_views(repository, owner, call["id"])
        live_profile = repository.rpc("create_profile", {"p_owner": owner, "p_id": str(uuid4()), "p_data": {"elder_name": "录音完整性验证"}})
        live = repository.rpc("open_call", {"p_owner": owner, "p_profile": live_profile["id"], "p_request": secrets.token_hex(16), "p_photo": None, "p_topic": ""})
        connection = str(uuid4())
        if repository.rpc("claim_call", {"p_owner": owner, "p_call": live["id"], "p_token": connection}) is not True:
            raise AssertionError("无法领取录音验证连接，需等待其他真实验证结束")
        file_id = files.put(owner, live_profile["id"], live["id"], "user0", pcm)
        checkpoint = {"p_owner": owner, "p_call": live["id"], "p_token": connection, "p_chunks": [{"kind": "user", "sequence": 0, "file_id": file_id, "size_bytes": len(pcm)}], "p_texts": {}, "p_timeline": "", "p_status": None, "p_error": ""}
        if repository.rpc("call_checkpoint", checkpoint) is not True or repository.rpc("call_checkpoint", checkpoint) is not True:
            raise AssertionError("真实录音检查点或重复保存失败")
        checks.append("真实音频检查点重复保存去重")
        def denied(payload, label):
            try:
                repository.rpc("call_checkpoint", payload)
            except CloudDatabaseError as error:
                if error.code != "22023":
                    raise
                checks.append(label)
            else:
                raise AssertionError(label)
        mismatch = copy.deepcopy(checkpoint)
        mismatch["p_chunks"][0]["size_bytes"] += 2
        denied(mismatch, "相同序号的不同音频内容被拒绝")
        gap = copy.deepcopy(checkpoint)
        gap["p_chunks"][0]["sequence"] = 2
        denied(gap, "录音分片序号缺失被拒绝")
        foreign_file = copy.deepcopy(checkpoint)
        foreign_file["p_chunks"][0]["file_id"] = "0" * 64
        denied(foreign_file, "缺失或无权访问的音频文件被拒绝")
        final = {**checkpoint, "p_status": "ready"}
        if repository.rpc("call_checkpoint", final) is not True or repository.rpc("call_checkpoint", final) is not True:
            raise AssertionError("结束录音重复保存失败")
        events = list(repository.rows("analytics_events", filters={"owner_id": "eq." + owner, "entity_id": "eq." + live["id"], "name": "eq.recording_saved"}, order="id.asc"))
        if len(events) != 1:
            raise AssertionError("重复结束录音产生了重复成功事件")
        checks.append("结束录音重复保存不重复统计")
        task = repository.rpc("enqueue_draft", {"p_owner": owner, "p_call": call["id"], "p_request": secrets.token_hex(16), "p_replace": True})
        token = str(uuid4())
        if not repository.rpc("claim_draft", {"p_task": task["id"], "p_token": token}):
            raise AssertionError("无法领取成稿验证任务，需等待其他真实验证结束")
        payload = {"p_owner": owner, "p_call": call["id"], "p_task": task["id"], "p_token": token, "p_book": book, "p_previous": encode(previous), "p_accepted": encode(accepted)}
        if repository.rpc("commit_draft", {**payload, "p_owner": wrong_owner}) is not False:
            raise AssertionError("其他家庭可以提交成稿")
        checks.append("错误家庭提交被拒绝")
        bad_book = copy.deepcopy(book)
        bad_book["chapters"][0]["paragraphs"][0]["evidence"] = []
        try:
            repository.rpc("commit_draft", {**payload, "p_book": bad_book})
        except CloudDatabaseError as error:
            if error.code != "22023":
                raise AssertionError("成稿验证返回意外数据库错误：" + error.code) from error
        else:
            raise AssertionError("缺少证据的成稿被接受")
        if chapter_views(repository, owner, call["id"]) != previous or list(repository.rows("text_editions", filters={"owner_id": "eq." + owner}, order="id.asc")):
            raise AssertionError("失败提交修改了原稿或历史")
        checks.append("失败提交完整保留原稿与历史")
        if repository.rpc("commit_draft", payload) is not True or repository.rpc("commit_draft", payload) is not True:
            raise AssertionError("成稿保存或重复保存失败")
        editions = list(repository.rows("text_editions", filters={"owner_id": "eq." + owner, "call_id": "eq." + call["id"]}, order="created_at.asc,id.asc"))
        if len(editions) != 2:
            raise AssertionError("未同时保存原稿和已接受成稿")
        for edition in editions:
            decoded = json.loads(fernet.decrypt(edition["payload"].encode()))
            if decoded != (accepted if edition["request_id"].startswith("accepted:") else previous):
                raise AssertionError("历史快照与对应完整原稿不符")
        checks.append("原稿和已接受成稿同时加密保存")
        completed = list(repository.rows("analytics_events", filters={"owner_id": "eq." + owner, "entity_id": "eq." + call["id"], "name": "eq.memoir_completed"}, order="id.asc"))
        if len(completed) != 1:
            raise AssertionError("重复提交导致重复成功统计")
        checks.append("已成功成稿重复提交去重")
        first_task = repository.rpc("enqueue_draft", {"p_owner": owner, "p_call": live["id"], "p_request": secrets.token_hex(16), "p_replace": False})
        first_token = str(uuid4())
        if not repository.rpc("claim_draft", {"p_task": first_task["id"], "p_token": first_token}):
            raise AssertionError("无法领取首次生成验证任务")
        first_payload = {**payload, "p_call": live["id"], "p_task": first_task["id"], "p_token": first_token, "p_previous": ""}
        if repository.rpc("commit_draft", first_payload) is not True:
            raise AssertionError("首次成稿保存失败")
        first_editions = list(repository.rows("text_editions", filters={"owner_id": "eq." + owner, "call_id": "eq." + live["id"]}, order="id.asc"))
        if len(first_editions) != 1 or not first_editions[0]["request_id"].startswith("accepted:"):
            raise AssertionError("首次成稿缺少已接受历史快照")
        checks.append("首次成稿同样具有历史快照")
    finally:
        for record in repository.rows("private_files", filters={"owner_id": "eq." + owner}, order="id.asc"):
            for part in record["parts"]:
                response = files.client.delete(part["object"])
                if response.status_code not in {200, 204, 404}:
                    raise RuntimeError("验证文件清理失败")
        for table in ["writing_cache", "writing_runs", "text_editions", "draft_tasks", "audio_chunks", "call_timelines", "call_contexts", "analytics_events", "private_files", "paragraph_evidence", "paragraphs", "chapters", "imports", "transcripts", "audio_assets", "memory_items", "fact_items", "calls"]:
            repository.remove(table, {"owner_id": "eq." + owner})
        for profile in repository.rows("profiles", filters={"owner_id": "eq." + owner}, columns="id", order="id.asc"):
            repository.remove("book_settings", {"profile_id": "eq." + profile["id"]})
        for table in ["profiles", "audit_logs"]:
            repository.remove(table, {"owner_id": "eq." + owner})
        repository.remove("owners", {"id": "eq." + owner})
        clip.unlink()
        files.close()
        repository.close()
    result = {"passed": len(checks), "checks": checks, "verification_records_removed": True, "model_calls": 0}
    (ROOT / ".intermediate/cloud-commit-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": len(checks), "verification_records_removed": True, "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
