import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from cloudrun_tools import ROOT, cloudrun_client, service_detail

WORK = ROOT / ".intermediate/public-runtime"
ORIGIN = "https://yitan-web-341879-6-1525683845.sh.run.tcloudbase.com"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def expect(response, status=200):
    if response.status_code != status:
        raise RuntimeError(f"发布持久化验证失败 HTTP {response.status_code}")
    return response


def book_snapshot(credentials, profile_id, call_id, *, audio_hash):
    with httpx.Client(base_url=ORIGIN, headers={"Origin": ORIGIN}, timeout=120, follow_redirects=False) as client:
        expect(client.post("/api/v1/auth/password-login", json={"username": credentials["username"], "password": credentials["password"]}))
        try:
            profile = expect(client.get("/api/v1/profiles/" + profile_id)).json()
            call = next(item for item in profile["calls"] if item["id"] == call_id)
            chapters = call["chapters"]
            if not chapters:
                raise AssertionError("发布验证回忆录没有正文")
            canonical = json.dumps(chapters, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            snapshot = {
                "chapter_count": len(chapters),
                "body_characters": sum(len(paragraph["text"]) for chapter in chapters for paragraph in chapter["paragraphs"]),
                "memoir_sha256": hashlib.sha256(canonical).hexdigest(),
                "transcript_sha256": hashlib.sha256(call["texts"]["full"].encode()).hexdigest(),
            }
            endpoint = f"/api/v1/calls/{call_id}/audio/playback"
            metadata = expect(client.head(endpoint))
            snapshot["audio_bytes"] = int(metadata.headers["Content-Length"])
            if audio_hash:
                restored = hashlib.sha256()
                size = 0
                with client.stream("GET", endpoint) as recording:
                    expect(recording)
                    for chunk in recording.iter_bytes():
                        restored.update(chunk)
                        size += len(chunk)
                if size != snapshot["audio_bytes"]:
                    raise AssertionError("公网录音长度与 HEAD 不符")
                snapshot["audio_sha256"] = restored.hexdigest()
            return snapshot
        finally:
            expect(client.post("/api/v1/auth/logout"))


def snapshot():
    state = read_json(WORK / "account.json")
    if state.get("voice_verified") is not True or state.get("writing_verified") is not True:
        raise AssertionError("必须先完成真实语音和写作验证")
    admin = read_json(ROOT / ".intermediate/private/cloud-admin.json")
    original = read_json(ROOT / "data/migration/li/manifest.json")
    li = book_snapshot(admin, original["cloud_profile_id"], original["cloud_call_id"], audio_hash=False)
    if li["chapter_count"] != 7 or li["body_characters"] != 5366:
        raise AssertionError("李奶奶完整回忆录数量不符")
    expected_audio = next(item["bytes"] for item in original["files"] if item["role"] == "original_audio")
    if li["audio_bytes"] != expected_audio:
        raise AssertionError("李奶奶原始录音长度不符")
    release = book_snapshot(state, state["profile_id"], state["call_id"], audio_hash=True)
    cloud = cloudrun_client()
    versions = {}
    for name in ["yitan-api", "yitan-web"]:
        detail = service_detail(cloud, name)
        active = max(detail["OnlineVersionInfos"], key=lambda row: float(row["FlowRatio"]))
        if float(active["FlowRatio"]) != 100:
            raise AssertionError("服务仍在切换版本，必须等待完整发布后检查")
        versions[name] = active["VersionName"]
    return {"li": li, "release": release}, versions


def main():
    parser = argparse.ArgumentParser(description="通过真实公网记录重新发布前后的成稿、音频与版本证据")
    parser.add_argument("phase", choices=["before", "after"])
    args = parser.parse_args()
    proof_path = WORK / "republish.json"
    content, versions = snapshot()
    if args.phase == "before":
        if proof_path.exists():
            raise ValueError("发布前证据已经存在，禁止覆盖")
        proof = {"origin": ORIGIN, "checked_at_before": now(), "before_version": versions, "before": content, "persisted": False}
        save_json(proof_path, proof)
        print(json.dumps({"phase": "before", "snapshot_saved": True, "li_chapters": content["li"]["chapter_count"], "li_characters": content["li"]["body_characters"], "versions": versions}, ensure_ascii=False))
        return
    proof = read_json(proof_path)
    before = proof["before"]
    changed = all(versions[name] != proof["before_version"][name] for name in versions)
    memoir_equal = all(content[name]["memoir_sha256"] == before[name]["memoir_sha256"] for name in content)
    transcript_equal = all(content[name]["transcript_sha256"] == before[name]["transcript_sha256"] for name in content)
    audio_equal = content["release"]["audio_sha256"] == before["release"]["audio_sha256"]
    length_equal = all(content[name]["audio_bytes"] == before[name]["audio_bytes"] for name in content)
    if not all([changed, memoir_equal, transcript_equal, audio_equal, length_equal]):
        raise AssertionError("重新发布后的版本或资料一致性检查未通过")
    proof.update({
        "checked_at": now(), "after_version": versions, "after": content, "versions_changed": changed,
        "memoir_sha256_equal": memoir_equal, "transcript_sha256_equal": transcript_equal,
        "audio_sha256_equal": audio_equal, "audio_lengths_equal": length_equal, "persisted": True,
    })
    save_json(proof_path, proof)
    state_path = WORK / "account.json"
    state = read_json(state_path)
    state["republish_verified"] = True
    save_json(state_path, state)
    print(json.dumps({"persisted": True, "audio_sha256_equal": audio_equal, "memoir_sha256_equal": memoir_equal, "versions_changed": changed, "after_version": versions}, ensure_ascii=False))


if __name__ == "__main__":
    main()
