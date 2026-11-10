import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.core.crypto import build_fernet, encrypt_bytes
from app.models import utcnow


class WritingTrace:
    def __init__(self, settings, call_id: str, source: str, owner_id="standalone"):
        self.id = str(uuid4())
        self.path = settings.data_path / "writing" / call_id / f"{self.id}.json.enc"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fernet = build_fernet(settings.audio_fernet_key)
        self.payload = {"id": self.id, "call_id": call_id, "created_at": utcnow().isoformat(), "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "status": "running", "steps": [], "metrics": []}
        model_version = hashlib.sha256(settings.writing_model.encode()).hexdigest()[:16]
        self.cache_path = settings.data_path / "writing" / "cache" / owner_id / model_version / self.payload["source_sha256"]
        self.cache_path.mkdir(parents=True, exist_ok=True)
        self.save()

    def cache_read(self, key):
        path = self.cache_path / f"{key}.json.enc"
        return json.loads(self.fernet.decrypt(path.read_bytes())) if path.exists() else None

    def cache_write(self, key, value):
        path = self.cache_path / f"{key}.json.enc"
        pending = path.with_suffix(".pending")
        pending.write_bytes(encrypt_bytes(self.fernet, json.dumps(value, ensure_ascii=False).encode()))
        pending.replace(path)

    def save(self):
        pending = self.path.with_suffix(".pending")
        pending.write_bytes(encrypt_bytes(self.fernet, json.dumps(self.payload, ensure_ascii=False).encode()))
        pending.replace(self.path)

    def record(self, stage: str, data):
        self.payload["steps"].append({"stage": stage, "at": utcnow().isoformat(), "data": data})
        self.save()

    def finish(self, status: str, metrics: list, code: str = ""):
        self.payload.update(status=status, metrics=metrics, error_code=code, finished_at=utcnow().isoformat())
        self.save()
