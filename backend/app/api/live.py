"""浏览器和豆包之间的实时通话。"""

import base64
import asyncio
import json

from fastapi import APIRouter, WebSocket
from sqlalchemy.orm import Session
from sqlalchemy import select
from websockets.exceptions import ConnectionClosed
from app.models import CallContext, Profile

from app.services.brief import latest_opening
from app.services.calls import persist_call
from app.services.doubao import RealtimeSession, VoiceError, opening_greeting
from app.services.store import get_call, owner_from_session, save_audio, COOKIE
from app.services.timeline import TimelineCapture
from app.services.analytics import server_event
from app.services.redact import redact_sensitive

router = APIRouter()


def _db(websocket: WebSocket) -> Session:
    return websocket.app.state.db_factory()


@router.websocket("/api/v1/calls/{call_id}/live")
async def live(websocket: WebSocket, call_id: str) -> None:
    await websocket.accept()
    db = _db(websocket)
    try:
        owner = owner_from_session(db, websocket.cookies.get(COOKIE))
        call = get_call(db, owner, call_id) if owner else None
        if call is None or call.status != "live":
            await websocket.send_json({"type": "error", "error": {"code": "not_found", "message": "这通通话不存在或已经结束"}})
            await websocket.close()
            return
        session = RealtimeSession(websocket.app.state.settings)
        opening = latest_opening(db, owner, call.profile_id, call.id)
        context = db.scalar(select(CallContext).where(CallContext.call_id == call.id))
        profile = db.get(Profile, call.profile_id)
        background = f"称呼：{profile.elder_name}。想聊的话题：{profile.topics_want}。不想聊的话题：{profile.topics_avoid}。"
        if context and context.topic:
            background += f"\n本次家属提供的话题或照片说明（事实需老人确认）：{context.topic}"
        try:
            await session.connect(opening.context, background)
        except VoiceError as exc:
            await session.close()
            await persist_call(db, websocket.app.state.fernet, websocket.app.state.settings.data_path, call, b"", b"", "", "", None, exc)
            await websocket.send_json({"type": "error", "error": {"code": exc.code, "message": exc.message}})
            await websocket.close()
            return
        user_pcm = bytearray()
        timeline = TimelineCapture()
        timeline.captions = session.captions
        timeline.provider_events = session.event_counts
        timeline.provider_event_metadata = session.event_metadata
        capture_complete = False
        last_saved = 0
        failure: VoiceError | None = None
        async def upload():
            nonlocal last_saved, capture_complete
            while True:
                raw = await websocket.receive_text()
                message = json.loads(raw)
                if message.get("type") == "stop":
                    capture_complete = message.get("capture_complete") is True
                    break
                if message.get("type") == "timeline":
                    timeline.event(message)
                    session.captions.playback_event(message)
                    continue
                if message.get("type") != "audio":
                    continue
                chunk = base64.b64decode(message.get("pcm") or "")
                if "mixed" in message:
                    timeline.append(message)
                user_pcm.extend(chunk)
                if len(user_pcm) - last_saved >= 160000:
                    save_audio(db, websocket.app.state.fernet, websocket.app.state.settings.data_path, owner, call.id, "user", bytes(user_pcm))
                    save_audio(db, websocket.app.state.fernet, websocket.app.state.settings.data_path, owner, call.id, "assistant", bytes(session.assistant_pcm))
                    timeline.save(db, websocket.app.state.fernet, websocket.app.state.settings.data_path, owner, call.id)
                    last_saved = len(user_pcm)
                await session.send_pcm(chunk)

        async def download():
            while True:
                event = await session.receive_event()
                if event.get("type") == "session.closed":
                    return
                await _forward(websocket, event)

        uploader = asyncio.create_task(upload())
        downloader = asyncio.create_task(download())
        opening_guard = asyncio.create_task(session.wait_for_first_audio())
        try:
            server_event(db, "call_connected", owner, call.id)
            db.commit()
            await websocket.send_json({"type": "ready"})
            await websocket.send_json({"type": "opening_requested"})
            for message in await session.say(opening_greeting(opening.spoken)):
                await websocket.send_json(message)
            waiting = {uploader, downloader, opening_guard}
            while True:
                completed, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                if opening_guard in completed:
                    await opening_guard
                    waiting.remove(opening_guard)
                    completed.remove(opening_guard)
                if completed:
                    break
            if downloader in completed:
                await downloader
                raise VoiceError("voice_interrupted", "语音服务结束了连接，已经录下的声音会保存")
            await uploader
            await session.request_finish()
            await asyncio.wait_for(downloader, timeout=20)
        except VoiceError as exc:
            failure = exc
        except ConnectionClosed as exc:
            print(f"voice connection closed: received={exc.rcvd.code if exc.rcvd else None}, sent={exc.sent.code if exc.sent else None}", flush=True)
            failure = VoiceError("voice_interrupted", "通话中断了，已经录下的声音会留下")
        except Exception as exc:
            print(f"call interrupted: {type(exc).__name__}; events={session.event_counts}", flush=True)
            failure = VoiceError("voice_interrupted", "通话中断了，已经录下的声音会留下")
        finally:
            uploader.cancel()
            downloader.cancel()
            opening_guard.cancel()
            await asyncio.gather(uploader, downloader, opening_guard, return_exceptions=True)
            await session.close()
        if timeline.next_seq and not capture_complete and failure is None:
            failure = VoiceError("capture_incomplete", "录音末尾未能完整确认，已经收到的对话录音已保存")
        timeline.save(db, websocket.app.state.fernet, websocket.app.state.settings.data_path, owner, call.id, capture_complete)
        await persist_call(
            db,
            websocket.app.state.fernet,
            websocket.app.state.settings.data_path,
            call,
            bytes(user_pcm),
            bytes(session.assistant_pcm),
            "".join(session.user_text),
            "".join(session.assistant_text),
            None,
            failure,
        )
        if failure is None:
            await websocket.send_json({"type": "done"})
        else:
            try:
                await websocket.send_json({"type": "error", "error": {"code": failure.code, "message": failure.message}})
            except Exception:
                pass
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        db.close()


async def _forward(websocket: WebSocket, event: dict) -> None:
    if "_client_events" in event:
        for message in event["_client_events"]:
            if "text" in message:
                message = {**message, "text": redact_sensitive(message["text"])}
                if message.get("type") == "assistant_tts":
                    message["text_available"] = bool(message["text"])
            await websocket.send_json(message)
        return
    kind = event.get("type")
    if kind in {"input_audio_buffer.speech_started", "conversation.item.input_audio_transcription.started"}:
        await websocket.send_json({"type": "interrupted", "reason": "user_speech"})
    elif kind == "conversation.item.input_audio_transcription.delta":
        text = redact_sensitive(event.get("delta") or event.get("text") or "")
        if text:
            await websocket.send_json({"type": "user_text", "text": text})
    elif kind == "response.output_text.delta":
        text = redact_sensitive(event.get("delta") or event.get("text") or "")
        if text:
            await websocket.send_json({"type": "assistant_text", "text": text})
    elif kind == "response.output_audio.delta":
        audio = event.get("delta") or event.get("audio") or ""
        if audio:
            await websocket.send_json({"type": "audio", "pcm": audio})
