import hashlib
from urllib.parse import quote

import httpx


class CloudFiles:
    def __init__(self, repository, fernet, env_id, api_key):
        self.repository, self.fernet = repository, fernet
        self.client = httpx.Client(
            base_url=f"https://{env_id}.api.tcloudbasegateway.com/v1/storages/object/yitan-private/",
            headers={"Authorization": "Bearer " + api_key},
            timeout=httpx.Timeout(120, connect=10), follow_redirects=False,
        )

    def close(self):
        self.client.close()

    def put(self, owner_id, profile_id, call_id, role, data: bytes, content_type="application/octet-stream") -> str:
        if not data or len(data) > 320 * 1024 * 1024:
            raise ValueError("文件为空或超过保存限制")
        profile = self.repository.one("profiles", filters={"id": "eq." + profile_id, "owner_id": "eq." + owner_id}, columns="id")
        if profile is None:
            raise PermissionError("文件没有对应的家庭档案")
        checksum = hashlib.sha256(data).hexdigest()
        file_id = hashlib.sha256((owner_id + profile_id + (call_id or "") + role + checksum).encode()).hexdigest()
        if self.repository.one("private_files", filters={"id": "eq." + file_id, "owner_id": "eq." + owner_id}, columns="id"):
            return file_id
        parts = []
        for offset in range(0, len(data), 8 * 1024 * 1024):
            chunk = data[offset:offset + 8 * 1024 * 1024]
            object_name = f"{owner_id}/{file_id}/{len(parts):06d}.fernet"
            response = self.client.post(object_name, content=self.fernet.encrypt(chunk), headers={"Content-Type": "application/octet-stream", "Cache-Control": "private, no-store"})
            if response.status_code not in {200, 201, 409}:
                raise RuntimeError("加密文件保存失败")
            if response.status_code == 409:
                existing = self.client.get(object_name)
                if existing.status_code != 200 or self.fernet.decrypt(existing.content) != chunk:
                    raise ValueError("已有文件内容与上传内容不符")
            parts.append({"object": object_name, "bytes": len(chunk), "sha256": hashlib.sha256(chunk).hexdigest()})
        self.repository.insert("private_files", {
            "id": file_id, "owner_id": owner_id, "profile_id": profile_id, "call_id": call_id,
            "role": role[:32], "source_sha256": checksum, "size_bytes": len(data), "parts": parts,
        })
        return file_id

    def get(self, owner_id, file_id) -> bytes:
        record = self.repository.one("private_files", filters={"id": "eq." + file_id, "owner_id": "eq." + owner_id})
        if record is None:
            raise FileNotFoundError("找不到授权文件")
        restored = bytearray()
        for part in record["parts"]:
            if not part["object"].startswith(owner_id + "/"):
                raise PermissionError("文件路径与家庭不符")
            response = self.client.get(quote(part["object"], safe="/"))
            if response.status_code != 200:
                raise RuntimeError("加密文件读取失败")
            plain = self.fernet.decrypt(response.content)
            if len(plain) != part["bytes"] or hashlib.sha256(plain).hexdigest() != part["sha256"]:
                raise ValueError("文件块完整性检查失败")
            restored.extend(plain)
        if len(restored) != record["size_bytes"] or hashlib.sha256(restored).hexdigest() != record["source_sha256"]:
            raise ValueError("完整文件校验失败")
        return bytes(restored)

    def iter_range(self, owner_id, file_id, start, end):
        record = self.repository.one("private_files", filters={"id": "eq." + file_id, "owner_id": "eq." + owner_id})
        if record is None or not 0 <= start < end <= record["size_bytes"]:
            raise FileNotFoundError("文件读取范围无效")
        if not record["parts"] or any(part["bytes"] <= 0 for part in record["parts"]) or sum(part["bytes"] for part in record["parts"]) != record["size_bytes"]:
            raise ValueError("文件分块目录与完整长度不符")
        position = 0
        for part in record["parts"]:
            next_position = position + part["bytes"]
            if start < next_position and end > position:
                if not part["object"].startswith(owner_id + "/"):
                    raise PermissionError("文件路径与家庭不符")
                response = self.client.get(quote(part["object"], safe="/"))
                if response.status_code != 200:
                    raise RuntimeError("文件块读取失败")
                plain = self.fernet.decrypt(response.content)
                if len(plain) != part["bytes"] or hashlib.sha256(plain).hexdigest() != part["sha256"]:
                    raise ValueError("文件块完整性检查失败")
                yield plain[max(0, start - position):min(len(plain), end - position)]
            position = next_position
            if position >= end:
                break
