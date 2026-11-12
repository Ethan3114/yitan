import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from app.services.cloud_writing import CloudTrace, YieldWork
from app.services.grounding import elder_text, keep_grounded
from app.services.redact import redact_sensitive
from app.services.tingwu import _bucket, _call, _client, format_turns
from app.services.writing import WritingError


def now():
    return datetime.now(timezone.utc).isoformat()


def transcribe(app, task, token, deadline, stopped):
    from app.api.cloud_calls import audio_bytes
    repository, settings = app.state.cloud_repository, app.state.settings
    saved = repository.one("transcripts", filters={"owner_id": "eq." + task["owner_id"], "call_id": "eq." + task["call_id"], "track": "eq.full"})
    if saved and saved["text"].strip():
        return
    if not settings.tingwu_ready:
        raise WritingError("tingwu_not_configured", "完整转写服务尚未准备好")
    call = repository.one("calls", filters={"id": "eq." + task["call_id"], "owner_id": "eq." + task["owner_id"]})
    bucket, client = _bucket(settings), _client(settings, auto_retry=False)
    if not task.get("transcription_id"):
        if task.get("step") == "transcribe_pending":
            raise WritingError("transcription_outcome_unknown", "上次转写请求尚未确认，请联系管理员核实后继续")
        if stopped.is_set():
            raise WritingError("processing_interrupted", "处理已经停止，录音和任务记录均已保留")
        conversation = audio_bytes(app, task["owner_id"], call["id"], "conversation")
        audio = conversation if conversation else audio_bytes(app, task["owner_id"], call["id"], "user")
        if not audio:
            raise WritingError("audio_missing", "找不到完整录音，原有文字仍然保留")
        content, mime = audio
        object_key = f"yitan/transcription/{task['owner_id']}/{task['id']}." + ("m4a" if mime == "audio/mp4" else "wav")
        bucket.put_object(object_key, content)
        begun = repository.rpc("transcription_begin", {"p_owner": task["owner_id"], "p_call": task["call_id"], "p_task": task["id"], "p_token": token, "p_object": object_key})
        if not begun or stopped.is_set():
            raise WritingError("processing_interrupted", "处理权限已经变化，录音和任务记录均已保留")
        result = _call(client, "PUT", "/openapi/tingwu/v2/tasks", {
            "AppKey": settings.tingwu_app_key,
            "Input": {"SourceLanguage": "cn", "FileUrl": bucket.sign_url("GET", object_key, 4 * 3600, slash_safe=True), "TaskKey": task["id"]},
            "Parameters": {"Transcription": {"DiarizationEnabled": True, "Diarization": {"SpeakerCount": 0}}},
        }, {"type": "offline"})
        provider_id = str((result.get("Data") or {}).get("TaskId") or "")
        if result.get("Code") not in {0, "0"} or not provider_id:
            raise WritingError("tingwu_failed", "转写请求未成功，录音已经保存")
        task.update(transcription_id=provider_id, transcription_object=object_key)
        recorded = repository.rpc("transcription_record", {"p_owner": task["owner_id"], "p_call": task["call_id"], "p_task": task["id"], "p_token": token, "p_provider_id": provider_id})
        if not recorded:
            raise WritingError("transcription_result_conflict", "转写任务编号未能保存，请联系管理员核实费用后继续")
    while time.monotonic() < deadline - 20:
        if stopped.is_set():
            raise WritingError("processing_interrupted", "处理已经停止，转写任务编号已经保留")
        info = _call(client, "GET", f"/openapi/tingwu/v2/tasks/{task['transcription_id']}")
        data = info.get("Data") or {}
        if data.get("TaskStatus") == "FAILED":
            raise WritingError("tingwu_failed", "转写未完成，录音已经保存")
        if data.get("TaskStatus") == "COMPLETED":
            result_url = (data.get("Result") or {}).get("Transcription")
            if not result_url or not result_url.startswith("https://"):
                raise WritingError("tingwu_failed", "转写结果地址无效")
            response = httpx.get(result_url, timeout=60)
            response.raise_for_status()
            text = redact_sensitive(format_turns((response.json().get("Transcription") or {}).get("Paragraphs") or []))
            if not text:
                raise WritingError("no_transcript", "录音中没有识别到可用文字")
            committed = repository.rpc("finish_transcript", {"p_owner": task["owner_id"], "p_call": task["call_id"], "p_task": task["id"], "p_token": token, "p_text": text})
            if not committed:
                raise WritingError("lease_lost", "任务已由其他处理进程接手")
            if task.get("transcription_object"):
                bucket.delete_object(task["transcription_object"])
            return
        stopped.wait(5)
    raise YieldWork()


async def process_one(app):
    repository = app.state.cloud_repository
    candidates = list(repository.rows("draft_tasks", filters={"status": "in.(queued,running)"}, order="created_at.asc"))
    task, token = None, str(uuid4())
    for candidate in candidates:
        task = await asyncio.to_thread(repository.rpc, "claim_draft", {"p_task": candidate["id"], "p_token": token})
        if task:
            break
    if not task:
        return {"processed": False}
    deadline = time.monotonic() + 780
    trace = None
    stopped = threading.Event()
    controller = asyncio.current_task()
    pulse_error = None
    async def heartbeat():
        while True:
            await asyncio.sleep(20)
            changed = await asyncio.to_thread(repository.rpc, "heartbeat_draft", {"p_task": task["id"], "p_owner": task["owner_id"], "p_token": token})
            if not changed:
                raise WritingError("lease_lost", "任务处理权限已经转移")
    pulse = asyncio.create_task(heartbeat())
    def heartbeat_finished(finished):
        nonlocal pulse_error
        if not finished.cancelled() and finished.exception():
            pulse_error = finished.exception()
            stopped.set()
            controller.cancel()
    pulse.add_done_callback(heartbeat_finished)
    try:
        await asyncio.to_thread(transcribe, app, task, token, deadline, stopped)
        active = await asyncio.to_thread(repository.rpc, "heartbeat_draft", {"p_task": task["id"], "p_owner": task["owner_id"], "p_token": token})
        if not active:
            raise WritingError("lease_lost", "任务处理权限已经转移")
        await asyncio.to_thread(repository.update, "draft_tasks", {"id": "eq." + task["id"], "lease_token": "eq." + token}, {"step": "writing", "updated_at": now()})
        texts = {item["track"]: item["text"] for item in repository.rows("transcripts", filters={"call_id": "eq." + task["call_id"], "owner_id": "eq." + task["owner_id"]})}
        source = redact_sensitive(elder_text(texts))
        if not source:
            raise WritingError("no_transcript", "没有可用的完整讲述，无法整理章节")
        trace = CloudTrace(app, task, token, source, deadline)
        book = await trace.generate(source)
        for chapter in book["chapters"]:
            chapter["title"] = redact_sensitive(chapter["title"])[:80]
            for paragraph in chapter["paragraphs"]:
                paragraph["text"] = redact_sensitive(paragraph["text"])
                paragraph["source_quote"] = redact_sensitive(paragraph["source_quote"])
                if not paragraph["text"].strip() or not paragraph["evidence"]:
                    raise WritingError("evidence_missing", "正文缺少对应原文，旧稿已经保留")
        book["memories"] = [item for item in book["memories"] if keep_grounded(item["name"] or item["detail"], item["source_quote"], source)]
        from app.services.cloud_books import chapter_views
        previous = chapter_views(repository, task["owner_id"], task["call_id"])
        encoded = app.state.fernet.encrypt(json.dumps(previous, ensure_ascii=False).encode()).decode() if previous else ""
        accepted = [{"title": chapter["title"], "paragraphs": [{"text": paragraph["text"], "source_quote": paragraph["source_quote"], "source_quotes": paragraph["evidence"]} for paragraph in chapter["paragraphs"]]} for chapter in book["chapters"]]
        accepted_payload = app.state.fernet.encrypt(json.dumps(accepted, ensure_ascii=False).encode()).decode()
        committed = await asyncio.to_thread(repository.rpc, "commit_draft", {"p_owner": task["owner_id"], "p_call": task["call_id"], "p_task": task["id"], "p_token": token, "p_book": book, "p_previous": encoded, "p_accepted": accepted_payload})
        if not committed:
            raise WritingError("lease_lost", "任务处理权限已经转移，旧稿保留")
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        trace.finish("completed")
        return {"processed": True, "status": "succeeded"}
    except YieldWork:
        if trace:
            trace.finish("paused")
        repository.update("draft_tasks", {"id": "eq." + task["id"], "lease_token": "eq." + token}, {"status": "queued", "lease_token": None, "lease_until": None, "updated_at": now()})
        return {"processed": True, "status": "queued"}
    except (Exception, asyncio.CancelledError) as exc:
        stopped.set()
        code = getattr(pulse_error or exc, "code", "processing_interrupted")
        if trace:
            trace.finish("failed", code)
        changed = repository.update("draft_tasks", {"id": "eq." + task["id"], "lease_token": "eq." + token, "status": "eq.running"}, {"status": "failed", "error_code": code, "lease_token": None, "lease_until": None, "updated_at": now()})
        if changed:
            stage = "transcription" if changed[0]["step"] in {"queued", "transcribe_pending", "transcribing"} else "writing"
            repository.insert("analytics_events", {"id": str(uuid4()), "name": "operation_failed", "owner_id": task["owner_id"], "entity_id": task["call_id"], "source": "server", "stage": stage, "error_code": code[:48]})
        else:
            current = repository.one("draft_tasks", filters={"id": "eq." + task["id"], "owner_id": "eq." + task["owner_id"]}, columns="status")
            if current and current["status"] == "succeeded":
                return {"processed": True, "status": "succeeded"}
        return {"processed": True, "status": "failed", "error_code": code}
    finally:
        stopped.set()
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
