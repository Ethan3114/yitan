from io import BytesIO

from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import Call, CallContext, Owner, Profile
from app.services import store


def test_book_and_profile_persist_across_app_restart(client):
    client.post("/api/v1/auth/login", json={"code": "yitan-local"}).raise_for_status()
    profile_id = client.post("/api/v1/profiles", json={"elder_name": "验收档案"}).json()["id"]
    client.put(f"/api/v1/profiles/{profile_id}/book", json={"title": "故乡的记忆"}).raise_for_status()
    client.patch(f"/api/v1/profiles/{profile_id}", json={"elder_name": "验收档案", "hometown": "绍兴"}).raise_for_status()
    with TestClient(create_app(client.app.state.settings)) as restarted:
        restarted.post("/api/v1/auth/login", json={"code": "yitan-local"}).raise_for_status()
        saved = restarted.get(f"/api/v1/profiles/{profile_id}").json()
        assert saved["book_title"] == "故乡的记忆"
        assert saved["hometown"] == "绍兴"


def test_photo_encryption_read_and_ownership(client):
    client.post("/api/v1/auth/login", json={"code": "yitan-local"}).raise_for_status()
    profile_id = client.post("/api/v1/profiles", json={"elder_name": "照片验收"}).json()["id"]
    output = BytesIO()
    Image.new("RGB", (32, 24), "white").save(output, format="PNG")
    content = output.getvalue()
    uploaded = client.post(f"/api/v1/profiles/{profile_id}/photos", files={"file": ("photo.png", content, "image/png")})
    uploaded.raise_for_status()
    photo_id = uploaded.json()["id"]
    listed = client.get(f"/api/v1/profiles/{profile_id}/photos").json()["items"]
    assert listed[0]["id"] == photo_id
    response = client.get(listed[0]["url"])
    assert response.content == content
    assert response.headers["cache-control"] == "private, no-store"
    client.post("/api/v1/auth/logout").raise_for_status()
    assert client.get(f"/api/v1/photos/{photo_id}").status_code in {401, 404}


def test_foreign_book_cannot_be_read_or_changed(client):
    client.post("/api/v1/auth/login", json={"code": "yitan-local"}).raise_for_status()
    with client.app.state.db_factory() as db:
        owner = Owner(id=store.new_id())
        db.add(owner)
        db.flush()
        row = Profile(id=store.new_id(), owner_id=owner.id, elder_name="隔离测试档案")
        db.add(row)
        db.commit()
        profile_id = row.id
    assert client.put(f"/api/v1/profiles/{profile_id}/book", json={"title": "越权修改"}).status_code == 404
    assert client.patch(f"/api/v1/profiles/{profile_id}", json={"elder_name": "越权修改"}).status_code == 404
    assert client.get(f"/api/v1/profiles/{profile_id}/photos").status_code == 404


def test_call_and_context_share_transaction(client):
    client.post("/api/v1/auth/login", json={"code": "yitan-local"}).raise_for_status()
    owner = client.get("/api/v1/me").json()["owner_id"]
    profile_id = client.post("/api/v1/profiles", json={"elder_name": "事务验收"}).json()["id"]
    with client.app.state.db_factory() as db:
        call = store.create_call(db, owner, profile_id, commit=False)
        call_id = call.id
        db.add(CallContext(id=f"{owner}:transaction", owner_id=owner, call_id=call.id))
        db.rollback()
    with client.app.state.db_factory() as db:
        assert db.get(Call, call_id) is None
        assert db.scalar(select(CallContext).where(CallContext.call_id == call_id)) is None
