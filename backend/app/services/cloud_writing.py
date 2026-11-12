import asyncio
import hashlib
import json
import time
from uuid import uuid4

import httpx

from app.services.writing import OutputIncomplete, WritingError, generate


class YieldWork(Exception):
    pass


class CloudTrace:
    def __init__(self, app, task, token, source, deadline):
        self.app, self.task, self.token = app, task, token
        self.repository, self.fernet = app.state.cloud_repository, app.state.fernet
        self.deadline = deadline
        self.id = str(uuid4())
        self.source_hash = hashlib.sha256(source.encode()).hexdigest()
        self.payload = {"id": self.id, "call_id": task["call_id"], "steps": [], "metrics": [], "status": "running", "source_sha256": self.source_hash}
        self.repository.insert("writing_runs", {"id": self.id, "owner_id": task["owner_id"], "call_id": task["call_id"], "task_id": task["id"], "status": "running", "payload": self.encode(self.payload)})

    def encode(self, value):
        return self.fernet.encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()

    def decode(self, value):
        return json.loads(self.fernet.decrypt(value.encode()))

    def key(self, value):
        settings = self.app.state.settings
        identity = [self.task["owner_id"], self.task["call_id"], settings.writing_provider, settings.writing_url, settings.writing_model, self.source_hash, value]
        return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()

    def record(self, stage, data):
        self.payload["steps"].append({"stage": stage, "data": data})
        self.save()

    def save(self):
        self.repository.update("writing_runs", {"id": "eq." + self.id, "owner_id": "eq." + self.task["owner_id"]}, {"payload": self.encode(self.payload), "status": self.payload["status"]})

    def finish(self, status, code=""):
        self.payload.update(status=status, error_code=code)
        self.save()

    def cache_read(self, key):
        row = self.repository.one("writing_cache", filters={"id": "eq." + self.key("checkpoint:" + key), "owner_id": "eq." + self.task["owner_id"], "status": "eq.completed"})
        return self.decode(row["payload"]) if row else None

    def cache_write(self, key, value):
        cache_id = self.key("checkpoint:" + key)
        changed = self.repository.rpc("cache_checkpoint", {"p_id": cache_id, "p_owner": self.task["owner_id"], "p_call": self.task["call_id"], "p_task": self.task["id"], "p_token": self.token, "p_payload": self.encode(value)})
        if not changed:
            raise WritingError("lease_lost", "任务处理权限已经转移，检查点保持不变")

    def cached_result(self, previous):
        if previous["status"] == "completed":
            return self.decode(previous["payload"])
        if previous["status"] == "incomplete":
            raise OutputIncomplete()
        if previous["status"] in {"rejected", "interrupted"}:
            raise WritingError("model_request_rejected", "模型请求未能完成，已保存结果状态，请联系管理员检查")
        raise WritingError("model_outcome_unknown", "上次模型请求未能确认结果，请联系管理员核实费用后继续")

    async def finish_model(self, cache_id, status, value, usage):
        changed = await asyncio.to_thread(self.repository.rpc, "model_finish", {
            "p_id": cache_id, "p_owner": self.task["owner_id"], "p_call": self.task["call_id"],
            "p_task": self.task["id"], "p_token": self.token, "p_status": status,
            "p_payload": self.encode(value), "p_usage": usage,
        })
        if not changed:
            raise WritingError("model_result_conflict", "模型结果保存权限发生变化，处理记录已经保留")

    async def complete(self, system, user):
        cache_id = self.key("model:" + json.dumps([system, user], ensure_ascii=False))
        previous = await asyncio.to_thread(self.repository.one, "writing_cache", filters={"id": "eq." + cache_id, "owner_id": "eq." + self.task["owner_id"]})
        if previous:
            return self.cached_result(previous)
        if time.monotonic() > self.deadline - 330:
            raise YieldWork()
        begun = await asyncio.to_thread(self.repository.rpc, "model_begin", {"p_id": cache_id, "p_owner": self.task["owner_id"], "p_call": self.task["call_id"], "p_task": self.task["id"], "p_token": self.token})
        if not begun["created"]:
            return self.cached_result(begun)
        settings = self.app.state.settings
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
            response = await client.post(settings.writing_url.rstrip("/") + "/chat/completions", headers={"Authorization": "Bearer " + settings.writing_key}, json={
                "model": settings.writing_model,
                **({"enable_thinking": True} if settings.writing_provider == "qwen" else {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}),
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"}, "temperature": 0.2, "max_tokens": 32768,
            })
        if response.status_code in {400, 401, 403, 404, 422, 429}:
            await self.finish_model(cache_id, "rejected", {"http_status": response.status_code}, {})
            raise WritingError("model_request_rejected", "模型服务拒绝了请求，请联系管理员检查配置或额度")
        response.raise_for_status()
        payload = response.json()
        choice = payload["choices"][0]
        usage = payload.get("usage", {})
        self.payload["metrics"].append({"model": payload.get("model", settings.writing_model), "usage": usage, "elapsed": time.monotonic() - started, "request_id": payload.get("id", ""), "finish_reason": choice.get("finish_reason")})
        if choice.get("finish_reason") == "length":
            await self.finish_model(cache_id, "incomplete", choice["message"].get("content"), usage)
            raise OutputIncomplete()
        if choice.get("finish_reason") != "stop":
            await self.finish_model(cache_id, "interrupted", {"finish_reason": choice.get("finish_reason")}, usage)
            raise WritingError("model_interrupted", "模型未完整返回内容，处理记录已经保存")
        result = choice["message"]["content"]
        if not isinstance(result, str) or not result.strip():
            await self.finish_model(cache_id, "interrupted", {"finish_reason": "empty_content"}, usage)
            raise WritingError("model_response_invalid", "模型没有返回可用正文，处理记录已经保存")
        await self.finish_model(cache_id, "completed", result, usage)
        return result

    async def generate(self, source):
        return await generate(self.complete, source, self.record, self.cache_read, self.cache_write)
