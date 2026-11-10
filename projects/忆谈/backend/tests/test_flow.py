"""阶段 2-1：登录、隔离、同意、照片、加密和转写帧。"""

from app.core.crypto import build_fernet, decrypt_bytes, encrypt_bytes
from app.models import Owner, Profile
from app.services.asr_codec import decode_frame, encode_audio, encode_full_client
from app.services.store import new_id, sniff_image


def login(client):
    response = client.post("/api/v1/auth/login", json={"code": "yitan-local"})
    assert response.status_code == 200
    return response


def test_wrong_invite(client):
    response = client.post("/api/v1/auth/login", json={"code": "nope"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invite_invalid"


def test_login_and_profile_roundtrip(client):
    login(client)
    created = client.post("/api/v1/profiles", json={"elder_name": "王爷爷", "hometown": "绍兴"})
    assert created.status_code == 200
    profile_id = created.json()["id"]
    listed = client.get("/api/v1/profiles")
    assert listed.json()["items"][0]["elder_name"] == "王爷爷"
    detail = client.get(f"/api/v1/profiles/{profile_id}")
    assert detail.json()["hometown"] == "绍兴"


def test_other_family_is_hidden(client):
    login(client)
    db = client.app.state.db_factory()
    other = Owner(id=new_id())
    db.add(other)
    db.flush()
    db.add(Profile(id=new_id(), owner_id=other.id, elder_name="别人的父亲", hometown="", family_names="", topics_want="", topics_avoid=""))
    db.commit()
    db.close()
    names = [item["elder_name"] for item in client.get("/api/v1/profiles").json()["items"]]
    assert "别人的父亲" not in names


def test_reject_bad_photo(client):
    login(client)
    profile_id = client.post("/api/v1/profiles", json={"elder_name": "李奶奶"}).json()["id"]
    response = client.post(f"/api/v1/profiles/{profile_id}/photos", files={"file": ("a.txt", b"hello", "text/plain")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "file_type"


def test_accept_jpeg(client):
    login(client)
    profile_id = client.post("/api/v1/profiles", json={"elder_name": "李奶奶"}).json()["id"]
    from io import BytesIO
    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (32, 32), color="white").save(output, format="JPEG")
    jpeg = output.getvalue()
    response = client.post(f"/api/v1/profiles/{profile_id}/photos", files={"file": ("old.jpg", jpeg, "image/jpeg")})
    assert response.status_code == 200
    assert sniff_image(jpeg) == "image/jpeg"


def test_card_number_is_removed_from_text():
    from app.services.redact import redact_sensitive

    cleaned = redact_sensitive("我的卡号是4111111111111111，请别记下。")
    assert "4111" not in cleaned
    assert "1973" in redact_sensitive("七三年是1973年")


def test_two_chapters_keep_life_order(client):
    from datetime import datetime, timedelta, timezone

    from app.models import Call, Chapter, Paragraph

    login(client)
    profile_id = client.post("/api/v1/profiles", json={"elder_name": "李奶奶"}).json()["id"]
    owner = client.get("/api/v1/me").json()["owner_id"]
    db = client.app.state.db_factory()
    call_id = new_id()
    first = new_id()
    second = new_id()
    base = datetime.now(timezone.utc)
    db.add(Call(id=call_id, owner_id=owner, profile_id=profile_id, status="ready"))
    db.add(Chapter(id=first, owner_id=owner, call_id=call_id, title="北京的家", created_at=base))
    db.add(Chapter(id=second, owner_id=owner, call_id=call_id, title="到了河西", created_at=base + timedelta(seconds=1)))
    db.add(Paragraph(id=new_id(), owner_id=owner, chapter_id=first, position=0, text="我在北京。", source_quote="北京"))
    db.add(Paragraph(id=new_id(), owner_id=owner, chapter_id=second, position=0, text="我去了河西。", source_quote="河西"))
    db.commit()
    db.close()
    chapters = client.get(f"/api/v1/profiles/{profile_id}").json()["calls"][0]["chapters"]
    assert [item["title"] for item in chapters] == ["北京的家", "到了河西"]


def test_opening_uses_last_chapter():
    from app.services.brief import opening_line

    line = opening_line("下田", "天不亮就跟着我爹下田。")
    assert "下田" in line
    assert "4111" not in opening_line("卡", "卡号4111111111111111")


def test_consent_required(client):
    login(client)
    profile_id = client.post("/api/v1/profiles", json={"elder_name": "赵爷爷"}).json()["id"]
    denied = client.post("/api/v1/calls", json={"profile_id": profile_id, "consent": False})
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "consent_required"
    assert client.get(f"/api/v1/profiles/{profile_id}").json()["calls"] == []


def test_fact_can_be_rejected(client):
    from app.models import Call, FactItem
    from app.services.store import new_id

    login(client)
    profile_id = client.post("/api/v1/profiles", json={"elder_name": "赵爷爷"}).json()["id"]
    owner = client.get("/api/v1/me").json()["owner_id"]
    db = client.app.state.db_factory()
    call_id = new_id()
    fact_id = new_id()
    db.add(Call(id=call_id, owner_id=owner, profile_id=profile_id, status="ready"))
    db.add(FactItem(id=fact_id, owner_id=owner, call_id=call_id, topic="待核对", detail="年份拿不准", source_quote=""))
    db.commit()
    db.close()
    response = client.post(f"/api/v1/facts/{fact_id}", json={"status": "rejected", "detail": "这年不对"})
    assert response.status_code == 200


def test_voice_key_missing_does_not_open_call(client):
    login(client)
    profile_id = client.post("/api/v1/profiles", json={"elder_name": "赵爷爷"}).json()["id"]
    response = client.post("/api/v1/calls", json={"profile_id": profile_id, "consent": True})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "voice_not_configured"


def test_encrypt_roundtrip():
    from cryptography.fernet import Fernet

    fernet = build_fernet(Fernet.generate_key().decode("utf-8"))
    blob = encrypt_bytes(fernet, b"pcm-bytes")
    assert decrypt_bytes(fernet, blob) == b"pcm-bytes"


def test_asr_frame_roundtrip():
    frame = encode_full_client({"request": {"model_name": "bigmodel"}})
    decoded = decode_frame(frame)
    assert decoded["payload"]["request"]["model_name"] == "bigmodel"
    audio = encode_audio(b"\x00\x01" * 8, last=True)
    heard = decode_frame(audio)
    assert heard["message_type"] == "response"
    assert (audio[1] & 0x0F) == 0x2
