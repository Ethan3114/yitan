import hashlib
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.api.cloud_books import router as books_router
from app.api.cloud_family import router as family_router
from app.core.config import Settings
from app.services.accounts import password_hash
from app.services.cloud_files import CloudFiles
from app.services.cloudbase import CloudRepository
from app.services.store import COOKIE


def main():
    values = dotenv_values(ROOT / "backend/.env")
    repository = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    fernet = Fernet(values["AUDIO_FERNET_KEY"].encode())
    files = CloudFiles(repository, fernet, values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    app = FastAPI()
    app.state.cloud_repository, app.state.cloud_files, app.state.fernet = repository, files, fernet
    app.state.settings = Settings(cookie_secure=False)
    app.include_router(books_router)
    app.include_router(family_router)
    owners = [str(uuid4()), str(uuid4())]
    sessions = [secrets.token_urlsafe(27), secrets.token_urlsafe(27)]
    book_path = ROOT / "data/real-interviews/qwen-plus-evaluation-c19c367a-8d2c-49df-83be-7114b8061f91/book.json"
    book = json.loads(book_path.read_text(encoding="utf-8"))
    transcript = (ROOT / "data/real-interviews/li-01-tingwu.txt").read_text(encoding="utf-8")
    checks = []
    for owner, session in zip(owners, sessions, strict=True):
        repository.insert("owners", {"id": owner})
        repository.insert("accounts", {"id": str(uuid4()), "owner_id": owner, "username": "business-check-" + owner, "password_hash": password_hash(secrets.token_urlsafe(32))})
        repository.insert("sessions", {"id": session, "owner_id": owner, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    try:
        with TestClient(app) as first, TestClient(app) as second, TestClient(app) as family:
            first.cookies.set(COOKIE, sessions[0])
            second.cookies.set(COOKIE, sessions[1])
            def expect(response, status, label):
                if response.status_code != status:
                    raise AssertionError(f"{label}: HTTP {response.status_code}")
                checks.append(label)
                return response.json() if response.headers.get("content-type", "").startswith("application/json") else response.content
            created = expect(first.post("/api/v1/profiles", json={"elder_name": "云端验证档案"}), 200, "真实云端建档")
            expect(second.get("/api/v1/profiles/" + created["id"]), 404, "其他家庭无法读取档案")
            expect(first.patch("/api/v1/profiles/" + created["id"], json={"elder_name": "云端验证档案", "hometown": "北京"}), 200, "真实云端编辑档案")
            receipt = repository.rpc("import_book", {"p_import_id": "check-" + str(uuid4()), "p_owner_id": owners[0], "p_profile_id": str(uuid4()), "p_call_id": str(uuid4()), "p_name": "李奶奶", "p_title": "李奶奶的故事", "p_source_sha256": hashlib.sha256(book_path.read_bytes()).hexdigest(), "p_book": book, "p_transcript": transcript})
            profile_id = receipt["profile_id"]
            detail = expect(first.get("/api/v1/profiles/" + profile_id), 200, "完整真实成稿读取")
            chapters = detail["calls"][0]["chapters"]
            if len(chapters) != 7 or sum(len(p["text"]) for c in chapters for p in c["paragraphs"]) != 5366:
                raise AssertionError("成稿章节或正文不完整")
            checks.append("七章5366字真实正文完整")
            call_id = detail["calls"][0]["id"]
            expect(first.get("/api/v1/chapters/" + chapters[0]["id"]), 200, "章节及证据读取")
            expect(second.get("/api/v1/chapters/" + chapters[0]["id"]), 404, "其他家庭无法读取章节")
            expect(first.post("/api/v1/paragraphs/" + chapters[0]["paragraphs"][0]["id"] + "/score", json={"score": 3}), 200, "段落评价持久保存")
            expect(first.put("/api/v1/profiles/" + profile_id + "/book", json={"title": "云端验证回忆录"}), 200, "书名保存")
            image = (ROOT / "frontend/public/images/courtyard.png").read_bytes()
            photo = expect(first.post("/api/v1/profiles/" + profile_id + "/photos", files={"file": ("courtyard.png", image, "image/png")}), 200, "真实产品图片上传加密保存")
            downloaded = expect(first.get("/api/v1/photos/" + photo["id"]), 200, "照片下载解密")
            if downloaded != image:
                raise AssertionError("照片原始字节不符")
            expect(second.get("/api/v1/photos/" + photo["id"]), 404, "其他家庭无法读取照片")
            selection = {"chapter_ids": [c["id"] for c in chapters], "photo_ids": [photo["id"]], "allow_audio": False}
            preview = expect(first.post("/api/v1/profiles/" + profile_id + "/family/preview", json=selection), 200, "家人展示预览")
            publish_body = {**selection, "digest": preview["digest"], "request_id": secrets.token_hex(16)}
            publication = expect(first.post("/api/v1/profiles/" + profile_id + "/family/publish", json=publish_body), 200, "确认发布加密快照")
            duplicate = expect(first.post("/api/v1/profiles/" + profile_id + "/family/publish", json=publish_body), 200, "重复发布去重")
            if duplicate != publication:
                raise AssertionError("重复发布创建了新记录")
            invitation = expect(first.post("/api/v1/profiles/" + profile_id + "/invitations", json={"request_id": secrets.token_hex(16)}), 200, "生成一次性家人邀请")
            redemption = {"token": invitation["token"], "name": "家人验证", "request_id": secrets.token_hex(16)}
            expect(family.post("/api/v1/invitations/redeem", json=redemption), 200, "家人兑换邀请获得独立会话")
            expect(second.post("/api/v1/invitations/redeem", json={**redemption, "request_id": secrets.token_hex(16)}), 410, "家人邀请只能核销一次")
            reading = expect(family.get("/api/v1/family/reading"), 200, "家人读取授权快照")
            if reading["publication"]["chapters"][0]["paragraphs"][0] != book["chapters"][0]["paragraphs"][0]["text"]:
                raise AssertionError("家人快照正文不符")
            expect(family.get("/api/v1/family/photos/" + photo["id"]), 200, "家人读取授权照片")
            expect(family.get("/api/v1/family/audio/" + call_id), 404, "未授权原声被拒绝")
            comment_body = {"publication_id": publication["id"], "chapter_id": chapters[0]["id"], "text": "我读到了您的故事。", "request_id": secrets.token_hex(16)}
            comment = expect(family.post("/api/v1/family/comments", json=comment_body), 200, "家人留言加密保存")
            same_comment = expect(family.post("/api/v1/family/comments", json=comment_body), 200, "重复留言去重")
            if same_comment != comment:
                raise AssertionError("重复留言创建了新记录")
            expect(second.delete("/api/v1/family/comments/" + comment["id"]), 404, "其他家庭不能删除留言")
            expect(family.delete("/api/v1/family/comments/" + comment["id"]), 200, "家人可以删除自己的留言")
            edition_id = str(uuid4())
            payload = fernet.encrypt(json.dumps(chapters, ensure_ascii=False).encode()).decode()
            repository.insert("text_editions", {"id": edition_id, "owner_id": owners[0], "call_id": call_id, "request_id": secrets.token_hex(16), "payload": payload})
            expect(first.get("/api/v1/calls/" + call_id + "/editions"), 200, "历史成稿读取")
            restore = {"confirm": True, "request_id": secrets.token_hex(16)}
            expect(first.post(f"/api/v1/calls/{call_id}/editions/{edition_id}/restore", json=restore), 200, "历史恢复事务完成")
            expect(first.post(f"/api/v1/calls/{call_id}/editions/{edition_id}/restore", json=restore), 200, "历史恢复重复请求去重")
            unchanged = expect(family.get("/api/v1/family/reading"), 200, "恢复正文不替换家人已发布快照")
            if unchanged["publication"]["id"] != publication["id"]:
                raise AssertionError("家人快照被恢复操作修改")
            management = expect(first.get("/api/v1/profiles/" + profile_id + "/family"), 200, "管理者查看家人成员与留言")
            member_id = management["members"][0]["id"]
            expect(first.delete(f"/api/v1/profiles/{profile_id}/members/{member_id}"), 200, "撤销家人权限")
            expect(family.get("/api/v1/family/reading"), 401, "撤销后旧家人会话被拒绝")
    finally:
        for owner in owners:
            cloud_files = list(repository.rows("private_files", filters={"owner_id": "eq." + owner}, order="id.asc"))
            for record in cloud_files:
                for part in record["parts"]:
                    response = files.client.delete(part["object"])
                    if response.status_code not in {200, 204, 404}:
                        raise RuntimeError("验证文件清理失败")
            for table in ["family_comments", "family_invites", "family_members", "family_publications", "text_editions", "analytics_events", "private_files", "photos", "paragraph_evidence", "paragraphs", "chapters", "imports", "transcripts", "audio_assets", "memory_items", "fact_items", "calls"]:
                repository.remove(table, {"owner_id": "eq." + owner})
            profile_ids = [row["id"] for row in repository.rows("profiles", filters={"owner_id": "eq." + owner}, columns="id", order="id.asc")]
            for profile_id in profile_ids:
                repository.remove("book_settings", {"profile_id": "eq." + profile_id})
            for table in ["profiles", "audit_logs", "sessions", "accounts"]:
                repository.remove(table, {"owner_id": "eq." + owner})
            repository.remove("owners", {"id": "eq." + owner})
        files.close()
        repository.close()
    result = {"passed": len(checks), "checks": checks, "verification_records_removed": True, "model_calls": 0}
    output = ROOT / ".intermediate/cloud-business-verification.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": len(checks), "verification_records_removed": True, "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
