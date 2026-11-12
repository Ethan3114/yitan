import asyncio
import base64
import hmac
import io
import json
import time
import wave
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from starlette.responses import FileResponse, MalformedRangeHeader, RangeNotSatisfiable

from app.api.cloud_accounts import cloud_owner
from app.api.live import _forward
from app.core.errors import error_response
from app.schemas import CallIn, DraftTaskIn
from app.services.brief import latest_cloud_opening
from app.services.cloudbase import CloudDatabaseError
from app.services.doubao import RealtimeSession, VoiceError, opening_greeting
from app.services.redact import redact_sensitive
from app.services.store import COOKIE, pcm16_to_wav
from app.services.timeline import TimelineCapture

router = APIRouter(prefix="/api")


def now():
    return datetime.now(timezone.utc).isoformat()


def task_view(row):
    return {key: row[key] for key in ["id", "call_id", "status", "step", "error_code", "created_at", "updated_at"]}


def call_record(request, owner, call_id):
    return request.app.state.cloud_repository.one("calls", filters={"id": "eq." + call_id, "owner_id": "eq." + owner}) if owner else None


def audio_bytes(app, owner, call_id, kind):
    repository = app.state.cloud_repository
    asset = repository.one("audio_assets", filters={"call_id": "eq." + call_id, "owner_id": "eq." + owner, "kind": "eq." + kind})
    if asset:
        if asset["relpath"] != "chunks":
            return app.state.cloud_files.get(owner, asset["relpath"]), "audio/wav"
        chunks = list(repository.rows("audio_chunks", filters={"call_id": "eq." + call_id, "owner_id": "eq." + owner, "kind": "eq." + kind}, order="sequence.asc"))
        for index, chunk in enumerate(chunks):
            if chunk["sequence"] != index:
                raise ValueError("录音文件分片顺序不连续")
        pcm = b"".join(app.state.cloud_files.get(owner, chunk["file_id"]) for chunk in chunks)
        if len(pcm) != asset["size_bytes"]:
            raise ValueError("录音完整长度不符")
        return pcm16_to_wav(pcm, 24000 if kind == "assistant" else 16000), "audio/wav"
    original = repository.one("private_files", filters={"call_id": "eq." + call_id, "owner_id": "eq." + owner, "role": "eq.original_audio"})
    if original and kind in {"user", "playback"}:
        return app.state.cloud_files.get(owner, original["id"]), "audio/mp4"
    return None


def audio_range_response(request, size, content_type, kind, reader):
    start, end, status = 0, size, 200
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "X-Recording-Kind": kind}
    if request.headers.get("range"):
        try:
            ranges = FileResponse._parse_range_header(request.headers["range"], size)
        except (MalformedRangeHeader, RangeNotSatisfiable):
            response = error_response(416, "range_invalid", "录音读取范围无效")
            response.headers.update({**headers, "Content-Range": f"bytes */{size}"})
            return response
        if len(ranges) != 1:
            response = error_response(416, "range_invalid", "请一次读取一段录音")
            response.headers.update({**headers, "Content-Range": f"bytes */{size}"})
            return response
        start, end = ranges[0]
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end-1}/{size}"
    headers["Content-Length"] = str(end - start)
    if request.method == "HEAD":
        return Response(status_code=status, media_type=content_type, headers=headers)
    return StreamingResponse(reader(start, end), status_code=status, media_type=content_type, headers=headers)


def pcm_header(size, rate):
    if size <= 0 or size % 2:
        raise ValueError("录音长度无效")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.setnframes(size // 2)
        recording.writeframesraw(b"")
        header = buffer.getvalue()
    return header


def audio_response(request, owner, call_id, kind="playback"):
    origin = request.headers.get("origin")
    if origin and origin != request.app.state.settings.web_origin:
        return error_response(403, "origin_denied", "请求来源不被允许")
    if not call_record(request, owner, call_id):
        return error_response(404, "not_found", "找不到这次访谈")
    if kind not in {"playback", "conversation", "user", "assistant"}:
        return error_response(400, "audio_kind", "不支持这种录音类型")
    if kind == "playback":
        asset = request.app.state.cloud_repository.one("audio_assets", filters={"call_id": "eq." + call_id, "owner_id": "eq." + owner, "kind": "eq.conversation"}, columns="id")
        kind = "conversation" if asset else "user"
    repository = request.app.state.cloud_repository
    saved_asset = repository.one("audio_assets", filters={"call_id": "eq." + call_id, "owner_id": "eq." + owner, "kind": "eq." + kind})
    if saved_asset:
        if saved_asset["relpath"] != "chunks":
            saved_file = repository.one("private_files", filters={"id": "eq." + saved_asset["relpath"], "owner_id": "eq." + owner})
            if saved_file is None:
                return error_response(404, "not_found", "找不到授权录音")
            return audio_range_response(request, saved_file["size_bytes"], "audio/wav", kind, lambda start, end: request.app.state.cloud_files.iter_range(owner, saved_file["id"], start, end))
        chunks = list(repository.rows("audio_chunks", filters={"call_id": "eq." + call_id, "owner_id": "eq." + owner, "kind": "eq." + kind}, order="sequence.asc"))
        if any(chunk["sequence"] != index for index, chunk in enumerate(chunks)) or sum(chunk["size_bytes"] for chunk in chunks) != saved_asset["size_bytes"]:
            raise ValueError("录音分片或总长度不符")
        header = pcm_header(saved_asset["size_bytes"], 24000 if kind == "assistant" else 16000)
        def read_pcm_range(start, end):
            if start < len(header):
                yield header[start:min(end, len(header))]
            position = len(header)
            for chunk in chunks:
                next_position = position + chunk["size_bytes"]
                if start < next_position and end > position:
                    yield from request.app.state.cloud_files.iter_range(owner, chunk["file_id"], max(0, start-position), min(chunk["size_bytes"], end-position))
                position = next_position
                if position >= end:
                    break
        return audio_range_response(request, len(header) + saved_asset["size_bytes"], "audio/wav", kind, read_pcm_range)
    if kind == "user":
        original = repository.one("private_files", filters={"call_id": "eq." + call_id, "owner_id": "eq." + owner, "role": "eq.original_audio"})
        if original:
            return audio_range_response(request, original["size_bytes"], "audio/mp4", kind, lambda start, end: request.app.state.cloud_files.iter_range(owner, original["id"], start, end))
    return error_response(404, "not_found", "录音尚未保存完成")


@router.post("/v1/calls")
def open_call(body: CallIn, request: Request, owner=Depends(cloud_owner)):
    if owner is None:
        return error_response(401, "unauthorized", "请先登录")
    if not body.consent:
        return error_response(400, "consent_required", "老人还没有同意录音")
    if not request.app.state.settings.voice_ready:
        return error_response(409, "voice_not_configured", "语音访谈服务尚未准备好")
    try:
        row = request.app.state.cloud_repository.rpc("open_call", {"p_owner": owner, "p_profile": body.profile_id, "p_request": body.request_id or str(uuid4()), "p_photo": body.photo_id, "p_topic": body.topic})
    except CloudDatabaseError as exc:
        if exc.status == 400:
            return error_response(409, "call_unavailable", "请先结束已有访谈，并确认档案和照片有效")
        raise
    return {"id": row["id"], "status": row["status"]}


@router.get("/v1/calls/{call_id}")
def detail(call_id: str, request: Request, owner=Depends(cloud_owner)):
    row = call_record(request, owner, call_id)
    if row is None:
        return error_response(404, "not_found", "找不到这次访谈")
    texts = {item["track"]: item["text"] for item in request.app.state.cloud_repository.rows("transcripts", filters={"call_id": "eq." + call_id, "owner_id": "eq." + owner})}
    return {"id": row["id"], "status": row["status"], "failure_code": row["failure_code"], "texts": texts}


@router.api_route("/v1/calls/{call_id}/audio/{kind}", methods=["GET", "HEAD"])
def audio(call_id: str, kind: str, request: Request, owner=Depends(cloud_owner)):
    return audio_response(request, owner, call_id, kind)


@router.post("/v1/calls/{call_id}/abandon")
def abandon(call_id: str, request: Request, owner=Depends(cloud_owner)):
    if call_record(request, owner, call_id) is None:
        return error_response(404, "not_found", "找不到这次访谈")
    request.app.state.cloud_repository.update("calls", {"id": "eq." + call_id, "owner_id": "eq." + owner, "status": "eq.live"}, {"status": "failed", "failure_code": "connection_closed", "ended_at": now()})
    return {"ok": True}


def launch_worker(app):
    async def dispatch():
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(850, connect=20)) as client:
            async with client.stream("POST", app.state.settings.worker_url, headers={"X-Worker-Token": app.state.settings.worker_token}) as response:
                response.raise_for_status()
                async for _ in response.aiter_bytes():
                    pass
    work = asyncio.create_task(dispatch())
    app.state.draft_workers.add(work)
    def finished(result):
        app.state.draft_workers.discard(result)
        if not result.cancelled() and result.exception():
            # 队列和检查点已在云端，定时任务会重新领取。
            app.state.worker_dispatch_error = type(result.exception()).__name__
    work.add_done_callback(finished)


@router.post("/v1/calls/{call_id}/draft-tasks", status_code=202)
async def enqueue(call_id: str, body: DraftTaskIn, request: Request, owner=Depends(cloud_owner)):
    if call_record(request, owner, call_id) is None:
        return error_response(404, "not_found", "找不到这次访谈")
    if not request.app.state.settings.writing_ready:
        return error_response(409, "model_not_configured", "写作服务尚未准备好")
    try:
        row = await asyncio.to_thread(request.app.state.cloud_repository.rpc, "enqueue_draft", {"p_owner": owner, "p_call": call_id, "p_request": body.request_id, "p_replace": body.replace})
    except CloudDatabaseError as exc:
        if exc.code in {"P0101", "P0102"}:
            code = "model_outcome_unknown" if exc.code == "P0101" else "transcription_outcome_unknown"
            return error_response(409, code, "上次收费请求的结果尚未确认，请联系管理员核实后继续，已有内容已经保存")
        if exc.status == 400:
            return error_response(409, "confirm_replace", "请等访谈结束；已有章节时需要确认重新整理")
        raise
    launch_worker(request.app)
    return task_view(row)


@router.post("/v1/calls/{call_id}/draft", status_code=202)
async def legacy_draft(call_id: str, request: Request, owner=Depends(cloud_owner)):
    return await enqueue(call_id, DraftTaskIn(request_id=str(uuid4())), request, owner)


@router.get("/v1/draft-tasks/{task_id}")
def read_task(task_id: str, request: Request, owner=Depends(cloud_owner)):
    row = request.app.state.cloud_repository.one("draft_tasks", filters={"id": "eq." + task_id, "owner_id": "eq." + owner}) if owner else None
    if row is None:
        return error_response(404, "not_found", "找不到这次整理")
    return task_view(row)


@router.post("/internal/work")
async def work(request: Request):
    if not hmac.compare_digest(request.headers.get("x-worker-token", ""), request.app.state.settings.worker_token):
        return error_response(403, "worker_denied", "请求未被授权")
    from app.services.cloud_tasks import process_one
    async def stream():
        task = asyncio.create_task(process_one(request.app))
        try:
            while not task.done():
                yield ": heartbeat\n\n"
                await asyncio.wait({task}, timeout=10)
            result = await task
            yield "event: done\ndata: " + json.dumps(result) + "\n\n"
        except Exception:
            yield 'event: error\ndata: {"error":{"code":"worker_failed","message":"处理记录已经保存"}}\n\n'
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.websocket("/v1/calls/{call_id}/live")
async def live(websocket: WebSocket, call_id: str):
    app = websocket.app
    settings, repository = app.state.settings, app.state.cloud_repository
    if not hmac.compare_digest(websocket.headers.get("x-proxy-secret", ""), settings.proxy_shared_secret) or websocket.headers.get("origin") != settings.web_origin:
        await websocket.close(code=1008)
        return
    owner = await asyncio.to_thread(repository.owner_from_session, websocket.cookies.get(COOKIE, ""), datetime.now(timezone.utc))
    row = await asyncio.to_thread(repository.one, "calls", filters={"id": "eq." + call_id, "owner_id": "eq." + (owner or ""), "status": "eq.live"})
    if not row:
        await websocket.close(code=1008)
        return
    token = str(uuid4())
    claimed = await asyncio.to_thread(repository.rpc, "claim_call", {"p_owner": owner, "p_call": call_id, "p_token": token})
    if not claimed:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    profile = await asyncio.to_thread(repository.one, "profiles", filters={"id": "eq." + row["profile_id"], "owner_id": "eq." + owner})
    context = await asyncio.to_thread(repository.one, "call_contexts", filters={"call_id": "eq." + call_id, "owner_id": "eq." + owner})
    opening = await asyncio.to_thread(latest_cloud_opening, repository, owner, row["profile_id"], call_id)
    session = RealtimeSession(settings)
    timeline, user_pcm = TimelineCapture(), bytearray()
    positions = {"user": 0, "assistant": 0, "conversation": 0}
    sequences = {kind: 0 for kind in positions}
    capture_complete = False
    failure = None
    checkpoint_lock = asyncio.Lock()
    capture_started = time.monotonic()

    async def checkpoint(status=None, error=""):
        async with checkpoint_lock:
            parts = []
            saved_positions, saved_sequences = dict(positions), dict(sequences)
            for kind, data in {"user": user_pcm, "assistant": session.assistant_pcm, "conversation": timeline.pcm}.items():
                delta = bytes(data[positions[kind]:])
                if not delta:
                    continue
                file_id = await asyncio.to_thread(app.state.cloud_files.put, owner, row["profile_id"], call_id, kind + str(sequences[kind]), delta)
                parts.append({"kind": kind, "sequence": sequences[kind], "file_id": file_id, "size_bytes": len(delta)})
                saved_positions[kind] += len(delta)
                saved_sequences[kind] += 1
            payload = {"version": 1, "clock": "browser_audio_render", "sample_rate": 16000, "frames": timeline.next_seq, "duration_ms": len(timeline.pcm) / 32, "complete": capture_complete, "events": timeline.events, "provider_events": dict(session.event_counts), "provider_event_metadata": session.event_metadata, "opening_requested": session.opening_requested, "first_audio_received": session.first_audio.is_set(), "captions": session.captions.snapshot()}
            encoded = app.state.fernet.encrypt(json.dumps(payload).encode()).decode()
            texts = {"realtime_user": redact_sensitive("".join(session.user_text)), "realtime_assistant": redact_sensitive("".join(session.assistant_text))}
            saved = await asyncio.to_thread(repository.rpc, "call_checkpoint", {"p_owner": owner, "p_call": call_id, "p_token": token, "p_chunks": parts, "p_texts": texts, "p_timeline": encoded, "p_status": status, "p_error": error})
            if not saved:
                raise RuntimeError("访谈保存权限已经失效")
            positions.update(saved_positions)
            sequences.update(saved_sequences)

    async def upload():
        nonlocal capture_complete
        started = time.monotonic()
        while time.monotonic() - started < 1800:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=45)
            if len(raw) > 100000:
                raise ValueError("录音消息超过限制")
            message = json.loads(raw)
            if message.get("type") == "stop":
                capture_complete = message.get("capture_complete") is True
                return
            if message.get("type") == "timeline":
                if len(timeline.events) >= 50000:
                    raise ValueError("时间轴事件超过限制")
                timeline.event(message)
                session.captions.playback_event(message)
            elif message.get("type") == "audio":
                chunk = base64.b64decode(message.get("pcm", ""), validate=True)
                if not chunk or len(chunk) > 16000 or len(chunk) % 2:
                    raise ValueError("录音分片格式无效")
                elapsed = time.monotonic() - capture_started
                if len(user_pcm) + len(chunk) > (elapsed + 10) * 32000:
                    raise ValueError("录音发送速度无效")
                if "mixed" in message:
                    if float(message.get("at_ms", 0)) > (elapsed + 2) * 1000:
                        raise ValueError("录音时间轴超出本次访谈时间")
                    timeline.append(message)
                user_pcm.extend(chunk)
                await session.send_pcm(chunk)
        raise VoiceError("duration_limit", "本次访谈已到三十分钟，声音已经保存，可以下次接着讲")

    async def download():
        while True:
            event = await session.receive_event()
            if event.get("type") == "session.closed":
                return
            await _forward(websocket, event)

    async def persist_periodically():
        while True:
            await asyncio.sleep(20)
            await websocket.send_json({"type": "ping"})
            saving = asyncio.create_task(checkpoint())
            try:
                await asyncio.shield(saving)
            except asyncio.CancelledError:
                await saving
                raise

    jobs = []
    try:
        background = f"称呼：{profile['elder_name']}。想聊的话题：{profile['topics_want']}。不想聊的话题：{profile['topics_avoid']}。"
        if context and context["topic"]:
            background += "\n本次家属提供的话题或照片说明（事实需老人确认）：" + context["topic"]
        await session.connect(opening.context, background)
        await asyncio.to_thread(repository.insert, "analytics_events", {"id": str(uuid4()), "name": "call_connected", "owner_id": owner, "source": "server", "entity_id": call_id})
        uploader, downloader, saver = [asyncio.create_task(coro()) for coro in [upload, download, persist_periodically]]
        opening_guard = asyncio.create_task(session.wait_for_first_audio())
        jobs = [uploader, downloader, saver, opening_guard]
        await websocket.send_json({"type": "ready"})
        await websocket.send_json({"type": "opening_requested"})
        for message in await session.say(opening_greeting(opening.spoken)):
            await websocket.send_json(message)
        waiting = set(jobs)
        while True:
            done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            if opening_guard in done:
                await opening_guard
                waiting.remove(opening_guard)
                done.remove(opening_guard)
            if done:
                break
        for job in done:
            await job
        if uploader not in done:
            raise VoiceError("voice_interrupted", "连接已结束，收到的声音会保存")
        await session.request_finish()
        await asyncio.wait_for(downloader, timeout=20)
        if timeline.next_seq and not capture_complete:
            failure = VoiceError("capture_incomplete", "录音末尾未能完整确认，收到的声音已经保存")
    except Exception as exc:
        failure = exc if isinstance(exc, VoiceError) else VoiceError("voice_interrupted", "通话中断，收到的声音已经保存")
    finally:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await session.close()
        await checkpoint("failed" if failure else "ready", failure.code if failure else "")
    try:
        await websocket.send_json({"type": "error", "error": {"code": failure.code, "message": failure.message}} if failure else {"type": "done"})
        await websocket.close()
    except WebSocketDisconnect:
        return
