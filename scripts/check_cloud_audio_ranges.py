import json
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.main import create_app
from app.services.accounts import password_hash
from app.services.cloud_files import CloudFiles
from app.services.cloudbase import CloudRepository
from app.services.store import COOKIE, pcm16_to_wav


def main():
    origin = "https://yitan-range-verification.invalid"
    proxy_secret = secrets.token_urlsafe(32)
    settings = Settings(deployment_mode="cloud", cookie_secure=True, auth_pepper=secrets.token_urlsafe(32), proxy_shared_secret=proxy_secret, worker_token=secrets.token_urlsafe(32), worker_url=origin + "/api/internal/work", web_origin=origin)
    app = create_app(settings)
    repository, files = app.state.cloud_repository, app.state.cloud_files
    manifest = json.loads((ROOT / "data/migration/li/manifest.json").read_text(encoding="utf-8"))
    owner, original_call = manifest["destination_owner_id"], manifest["cloud_call_id"]
    session = secrets.token_urlsafe(27)
    other_owner, other_session = str(uuid4()), secrets.token_urlsafe(27)
    repository.insert("sessions", {"id": session, "owner_id": owner, "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()})
    repository.insert("owners", {"id": other_owner})
    repository.insert("accounts", {"id": str(uuid4()), "owner_id": other_owner, "username": "range-check-" + other_owner, "password_hash": password_hash(secrets.token_urlsafe(32)), "role": "member"})
    repository.insert("sessions", {"id": other_session, "owner_id": other_owner, "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()})
    checks = []
    directory = ROOT / ".intermediate/audio-range-checks" / other_owner
    directory.mkdir(parents=True)
    pcm_path = directory / "actual-source.pcm"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(ROOT / "data/real-interviews/li-01.m4a"), "-t", "0.2", "-ac", "1", "-ar", "16000", "-f", "s16le", str(pcm_path)], check=True)
    pcm = pcm_path.read_bytes()
    expected_wav = pcm16_to_wav(pcm, 16000)
    try:
        profile = repository.rpc("create_profile", {"p_owner": other_owner, "p_id": str(uuid4()), "p_data": {"elder_name": "原声分段验证"}})
        call_id = str(uuid4())
        repository.insert("calls", {"id": call_id, "owner_id": other_owner, "profile_id": profile["id"], "status": "ready"})
        # 使用真实录音的两段 PCM，验证跨分片读取和完整 WAV 输出。
        for index, data in enumerate([pcm[:len(pcm)//2], pcm[len(pcm)//2:]]):
            file_id = files.put(other_owner, profile["id"], call_id, "user" + str(index), data)
            repository.insert("audio_chunks", {"id": str(uuid4()), "owner_id": other_owner, "call_id": call_id, "kind": "user", "sequence": index, "file_id": file_id, "size_bytes": len(data)})
        repository.insert("audio_assets", {"id": str(uuid4()), "owner_id": other_owner, "call_id": call_id, "kind": "user", "relpath": "chunks", "size_bytes": len(pcm)})
        with TestClient(app, base_url=origin, headers={"X-Proxy-Secret": proxy_secret}) as client:
            client.cookies.set(COOKIE, session)
            original_url = f"/api/v1/calls/{original_call}/audio/playback"
            source = ROOT / "data/real-interviews/li-01.m4a"
            size = source.stat().st_size
            def original_range(selection):
                range_value, start, end = selection
                response = client.get(original_url, headers={"Range": range_value})
                with source.open("rb") as recording:
                    recording.seek(start)
                    expected = recording.read(end-start)
                if response.status_code != 206 or response.content != expected or response.headers.get("content-range") != f"bytes {start}-{end-1}/{size}":
                    raise AssertionError("真实原始录音 Range 内容或响应范围不符")
                return "原始录音" + ("开头" if start == 0 else "末尾") + "范围与本机原件一致"
            with ThreadPoolExecutor(max_workers=2) as executor:
                for label in executor.map(original_range, [("bytes=0-4095", 0, 4096), ("bytes=-4096", size-4096, size)]):
                    checks.append(label)
                    print(json.dumps({"verified": label}, ensure_ascii=False), flush=True)
            head = client.head(original_url)
            if head.status_code != 200 or head.content or int(head.headers["content-length"]) != size:
                raise AssertionError("原始录音 HEAD 不正确")
            checks.append("原始录音 HEAD 返回完整长度且无正文")
            invalid = client.get(original_url, headers={"Range": f"bytes={size+1}-"})
            if invalid.status_code != 416 or invalid.json()["error"]["code"] != "range_invalid":
                raise AssertionError("越界范围未正确拒绝")
            checks.append("越界范围返回结构化416错误")
            denied = client.get(original_url, headers={"Range": "bytes=0-4095", "Origin": "https://untrusted.invalid"})
            if denied.status_code != 403:
                raise AssertionError("跨站录音读取未被拒绝")
            checks.append("跨站录音读取被拒绝")
            client.cookies.clear()
            if client.get(original_url, headers={"Range": "bytes=0-4095"}).status_code != 404:
                raise AssertionError("匿名范围请求获得了私人录音")
            checks.append("匿名范围读取被拒绝")
            client.cookies.set(COOKIE, other_session)
            if client.get(original_url, headers={"Range": "bytes=0-4095"}).status_code != 404:
                raise AssertionError("其他家庭获得了私人录音")
            checks.append("其他家庭范围读取被拒绝")
            pcm_url = f"/api/v1/calls/{call_id}/audio/user"
            whole = client.get(pcm_url)
            if whole.status_code != 200 or whole.content != expected_wav:
                raise AssertionError("流式 WAV 与标准 wave 完整音频不一致")
            checks.append("真实PCM跨分片完整WAV与标准编码一致")
            boundaries = [(0, 44), (30, 60), (44+len(pcm)//2-20, 44+len(pcm)//2+20), (len(expected_wav)-32, len(expected_wav))]
            for start, end in boundaries:
                response = client.get(pcm_url, headers={"Range": f"bytes={start}-{end-1}"})
                if response.status_code != 206 or response.content != expected_wav[start:end]:
                    raise AssertionError("WAV 文件头或跨 PCM 分片 Range 不一致")
            checks.append("WAV文件头、头部边界、分片边界及末尾Range一致")
            header_only = client.head(pcm_url, headers={"Range": "bytes=0-43"})
            if header_only.status_code != 206 or header_only.content or header_only.headers["content-length"] != "44":
                raise AssertionError("WAV HEAD 范围不正确")
            checks.append("WAV HEAD 支持范围且不读取正文")
    finally:
        # 只清理本次新建的会话和验证家庭，不修改既有管理员或李奶奶资料。
        files.close()
        repository.close()
        repository = CloudRepository(settings.cloudbase_env_id, settings.cloudbase_api_key)
        files = CloudFiles(repository, app.state.fernet, settings.cloudbase_env_id, settings.cloudbase_api_key)
        repository.remove("sessions", {"id": "eq." + session, "owner_id": "eq." + owner})
        for record in repository.rows("private_files", filters={"owner_id": "eq." + other_owner}, order="id.asc"):
            for part in record["parts"]:
                response = files.client.delete(part["object"])
                if response.status_code not in {200, 204, 404}:
                    raise RuntimeError("验证录音清理失败")
        for table in ["audio_chunks", "audio_assets", "private_files", "calls", "analytics_events", "profiles", "sessions", "accounts"]:
            repository.remove(table, {"owner_id": "eq." + other_owner})
        repository.remove("owners", {"id": "eq." + other_owner})
        pcm_path.unlink()
        files.close()
        repository.close()
    result = {"passed": len(checks), "checks": checks, "existing_source_data_unchanged": True, "verification_records_removed": True, "model_calls": 0}
    (ROOT / ".intermediate/cloud-audio-range-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": len(checks), "existing_source_data_unchanged": True, "verification_records_removed": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
