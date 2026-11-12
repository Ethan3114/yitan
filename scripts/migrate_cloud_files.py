import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.cloudbase import CloudRepository

CHUNK_BYTES = 8 * 1024 * 1024


def main():
    manifest_path = ROOT / "data/migration/li/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("cloud_database_verified"):
        raise ValueError("必须先完成正文和转写原文迁移验证")
    values = dotenv_values(ROOT / "backend/.env")
    fernet = Fernet(values["AUDIO_FERNET_KEY"].encode())
    repository = CloudRepository(values["CLOUDBASE_ENV_ID"], values["CLOUDBASE_API_KEY"])
    base = f"https://{values['CLOUDBASE_ENV_ID']}.api.tcloudbasegateway.com/v1/storages/object/yitan-private/"
    owner = manifest["destination_owner_id"]
    verified = []
    with httpx.Client(base_url=base, headers={"Authorization": "Bearer " + values["CLOUDBASE_API_KEY"]}, timeout=httpx.Timeout(120, connect=10), follow_redirects=False) as client:
        try:
            for item in manifest["files"]:
                path = (ROOT / item["path"]).resolve(strict=True)
                if not path.is_relative_to(ROOT / "data"):
                    raise ValueError("迁移资料不在 data 目录")
                with path.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                if digest != item["sha256"] or path.stat().st_size != item["bytes"]:
                    raise ValueError("迁移来源校验不符")
                file_id = hashlib.sha256((owner + manifest["cloud_call_id"] + item["role"] + digest).encode()).hexdigest()
                previous = repository.one("private_files", filters={"id": "eq." + file_id, "owner_id": "eq." + owner})
                part_count = (item["bytes"] + CHUNK_BYTES - 1) // CHUNK_BYTES
                def transfer(index):
                    object_name = f"{owner}/{file_id}/{index:06d}.fernet"
                    with path.open("rb") as source:
                        source.seek(index * CHUNK_BYTES)
                        chunk = source.read(CHUNK_BYTES)
                    if not previous:
                        response = client.post(object_name, content=fernet.encrypt(chunk), headers={"Content-Type": "application/octet-stream", "Cache-Control": "private, no-store"})
                        if response.status_code not in {200, 201, 409}:
                            raise RuntimeError(f"云文件上传失败 HTTP {response.status_code}")
                    downloaded = client.get(object_name)
                    if downloaded.status_code != 200:
                        raise RuntimeError(f"云文件读取失败 HTTP {downloaded.status_code}")
                    plain = fernet.decrypt(downloaded.content)
                    if plain != chunk:
                        raise ValueError("云端文件块与本机原文不符")
                    part = {"object": object_name, "bytes": len(plain), "sha256": hashlib.sha256(plain).hexdigest()}
                    if previous and previous["parts"][index] != part:
                        raise ValueError("云端文件目录与实际文件不符")
                    print(json.dumps({"role": item["role"], "part_verified": index + 1, "parts": part_count}, ensure_ascii=False), flush=True)
                    return part, plain

                if previous and len(previous["parts"]) != part_count:
                    raise ValueError("云端文件块数量不符")
                restored = hashlib.sha256()
                size = 0
                parts = []
                # 并发传输四块，按文件原顺序累计下载内容的完整校验值。
                with ThreadPoolExecutor(max_workers=4) as executor:
                    for part, plain in executor.map(transfer, range(part_count)):
                        parts.append(part)
                        restored.update(plain)
                        size += len(plain)
                if restored.hexdigest() != digest or size != item["bytes"]:
                    raise ValueError("云端完整文件校验不符")
                if not previous:
                    repository.insert("private_files", {
                        "id": file_id, "owner_id": owner, "profile_id": manifest["cloud_profile_id"],
                        "call_id": manifest["cloud_call_id"], "role": item["role"],
                        "source_sha256": digest, "size_bytes": item["bytes"], "parts": parts,
                    })
                with httpx.Client(timeout=30) as anonymous:
                    denied = anonymous.get(base + parts[0]["object"])
                    if denied.status_code not in {401, 403, 404}:
                        raise AssertionError("云端私有文件允许匿名读取")
                verified.append({"role": item["role"], "bytes": size, "sha256": digest, "cloud_file_id": file_id})
                print(json.dumps({"role": item["role"], "verified": True, "anonymous_denied": True}, ensure_ascii=False), flush=True)
            manifest.update({"status": "cloud_sources_verified", "uploaded": True, "cloud_files_verified": True, "pending": [], "verified_cloud_files": verified})
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            manifest_path.chmod(0o600)
            print(json.dumps({"files_verified": len(verified), "total_bytes": sum(item["bytes"] for item in verified), "local_files_preserved": True, "model_calls": 0}, ensure_ascii=False))
        finally:
            repository.close()


if __name__ == "__main__":
    main()
