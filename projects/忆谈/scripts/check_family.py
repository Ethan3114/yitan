import json
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings
from app.main import create_app


def main():
    settings = get_settings()
    target = ROOT / ".intermediate/family-validation"
    target.mkdir(exist_ok=True)
    database = target / f"family-{uuid4()}.db"
    with sqlite3.connect(f"file:{settings.data_path / 'yitan.db'}?mode=ro", uri=True) as source, sqlite3.connect(database) as dest:
        source.backup(dest)
    local = settings.model_copy(update={"database_url": f"sqlite:///{database}", "data_dir": str(target)})
    app = create_app(local)
    manager, guest, other = TestClient(app), TestClient(app), TestClient(app)
    manager.post("/api/v1/auth/login", json={"code": settings.invite_code}).raise_for_status()
    profile_id = "f919fddd-be5f-56ed-8681-b48d64af6e63"
    profile = manager.get(f"/api/v1/profiles/{profile_id}").json()
    call = next(c for c in profile["calls"] if len(c["chapters"]) == 8)
    chapter = call["chapters"][0]
    selection = {"chapter_ids": [chapter["id"]], "photo_ids": [], "allow_audio": False}
    preview = manager.post(f"/api/v1/profiles/{profile_id}/family/preview", json=selection)
    preview.raise_for_status()
    body = {**selection, "digest": preview.json()["digest"], "request_id": str(uuid4())}
    published = manager.post(f"/api/v1/profiles/{profile_id}/family/publish", json=body)
    published.raise_for_status()
    publication_id = published.json()["id"]
    assert manager.post(f"/api/v1/profiles/{profile_id}/family/publish", json=body).json()["id"] == publication_id
    assert guest.get(f"/api/v1/profiles/{profile_id}").status_code == 401
    invitation = {"request_id": str(uuid4()), "allow_audio": False}
    invite = manager.post(f"/api/v1/profiles/{profile_id}/invitations", json=invitation).json()
    assert manager.post(f"/api/v1/profiles/{profile_id}/invitations", json=invitation).json()["token"] == invite["token"]
    redemption = {"token": invite["token"], "name": "家人阅读验证", "request_id": str(uuid4())}
    guest.post("/api/v1/invitations/redeem", json=redemption).raise_for_status()
    guest.post("/api/v1/invitations/redeem", json=redemption).raise_for_status()
    assert other.post("/api/v1/invitations/redeem", json={**redemption, "request_id": str(uuid4())}).status_code == 410
    reading = guest.get("/api/v1/family/reading").json()
    assert len(reading["publication"]["chapters"]) == 1
    assert reading["publication"]["chapters"][0]["paragraphs"] == [p["text"] for p in chapter["paragraphs"]]
    assert "source_quote" not in json.dumps(reading) and "facts" not in json.dumps(reading)
    assert guest.get(f"/api/v1/chapters/{chapter['id']}").status_code == 404
    assert guest.get(f"/api/v1/calls/{call['id']}").status_code == 401
    assert guest.get(f"/api/v1/family/audio/{call['id']}").status_code == 404
    assert guest.post(f"/api/v1/profiles/{profile_id}/invitations", json=invitation).status_code == 404
    comment = {"publication_id": publication_id, "chapter_id": chapter["id"], "text": "已读到这段真实回忆。", "request_id": str(uuid4())}
    response = guest.post("/api/v1/family/comments", json=comment)
    response.raise_for_status()
    cid = response.json()["id"]
    assert guest.post("/api/v1/family/comments", json=comment).json()["id"] == cid
    assert other.delete(f"/api/v1/family/comments/{cid}").status_code == 404
    assert len(manager.get(f"/api/v1/profiles/{profile_id}/family").json()["comments"]) == 1
    history = manager.get(f"/api/v1/calls/{call['id']}/editions")
    history.raise_for_status()
    assert history.json()["items"]
    edition = history.json()["items"][0]
    restore = {"confirm": True, "request_id": str(uuid4())}
    manager.post(f"/api/v1/calls/{call['id']}/editions/{edition['id']}/restore", json=restore).raise_for_status()
    manager.post(f"/api/v1/calls/{call['id']}/editions/{edition['id']}/restore", json=restore).raise_for_status()
    assert guest.get("/api/v1/family/reading").json()["publication"] == reading["publication"]
    # 重新创建应用和数据库连接，沿用已签发的会话。
    restarted = TestClient(create_app(local))
    restarted.cookies.update(guest.cookies)
    assert restarted.get("/api/v1/family/reading").json()["comments"][0]["id"] == cid
    member = manager.get(f"/api/v1/profiles/{profile_id}/family").json()["members"][-1]
    manager.delete(f"/api/v1/profiles/{profile_id}/members/{member['id']}").raise_for_status()
    assert restarted.get("/api/v1/family/reading").status_code == 401
    assert guest.post("/api/v1/family/comments", json={**comment, "request_id": str(uuid4())}).status_code == 404
    manager.delete(f"/api/v1/family/comments/{cid}").raise_for_status()
    manager.delete(f"/api/v1/profiles/{profile_id}/family/publication").raise_for_status()
    assert manager.get(f"/api/v1/profiles/{profile_id}/family").json()["publication"] is None
    # 使用实际采集的页面图片验证照片服务，不替代访谈或模型结果。
    image_path = ROOT / "docs/evidence/阶段3-2/家人管理-390px.png"
    if image_path.exists():
        content = image_path.read_bytes()
        uploaded = manager.post(f"/api/v1/profiles/{profile_id}/photos", files={"file": ("页面验证.png", content, "image/png")})
        uploaded.raise_for_status()
        photo_id = uploaded.json()["id"]
        current_profile = manager.get(f"/api/v1/profiles/{profile_id}").json()
        current_chapter = next(c for c in current_profile["calls"] if c["id"] == call["id"])["chapters"][0]
        selection = {"chapter_ids": [current_chapter["id"]], "photo_ids": [photo_id], "allow_audio": False}
        view = manager.post(f"/api/v1/profiles/{profile_id}/family/preview", json=selection).json()
        manager.post(f"/api/v1/profiles/{profile_id}/family/publish", json={**selection, "digest": view["digest"], "request_id": str(uuid4())}).raise_for_status()
        new_invite = manager.post(f"/api/v1/profiles/{profile_id}/invitations", json={"request_id": str(uuid4())}).json()
        other.post("/api/v1/invitations/redeem", json={"token": new_invite["token"], "name": "照片验证", "request_id": str(uuid4())}).raise_for_status()
        assert other.get(f"/api/v1/family/photos/{photo_id}").content == content
        assert guest.get(f"/api/v1/family/photos/{photo_id}").status_code == 404
        manager.delete(f"/api/v1/profiles/{profile_id}/family/publication").raise_for_status()
        assert other.get(f"/api/v1/family/photos/{photo_id}").status_code == 404
        print("通过：真实图片授权读取、撤销成员及取消展示后照片不可读取。")
    (target / "database.json").write_text(json.dumps({"database": str(database), "profile_id": profile_id, "call_id": call["id"]}))
    print("通过：真实回忆录副本、发布幂等、邀请一次使用、兑换重试、正文隔离、原声授权、留言幂等、越权拒绝、历史恢复、展示版本保持、应用重启与成员撤销。")


if __name__ == "__main__":
    main()
