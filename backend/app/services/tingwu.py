"""通话结束后，把整段录音交给通义听悟做全量转写。钥匙只在服务端。"""

import json
import time
from pathlib import Path

import httpx
import oss2
from aliyunsdkcore.auth.credentials import AccessKeyCredential
from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.redact import redact_sensitive
from app.services.store import read_audio, replace_transcript, transcripts_for
from app.models import CallTimeline

DOMAIN = "tingwu.cn-beijing.aliyuncs.com"


class TingwuError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def format_turns(paragraphs: list[dict]) -> str:
    """听悟按说话人切开的段落，收成带时间的逐字稿。"""
    lines: list[str] = []
    for paragraph in paragraphs:
        words = paragraph.get("Words") or []
        text = "".join(str(word.get("Text") or "") for word in words).strip()
        if not text or not words:
            continue
        start_ms = int(words[0].get("Start") or 0)
        speaker = paragraph.get("SpeakerId") or "?"
        minute, second = divmod(start_ms // 1000, 60)
        lines.append(f"[{minute:02d}:{second:02d} 发言人{speaker}] {text}")
    return "\n".join(lines)


def _client(settings: Settings) -> AcsClient:
    credential = AccessKeyCredential(
        settings.alibaba_cloud_access_key_id.strip(),
        settings.alibaba_cloud_access_key_secret.strip(),
    )
    return AcsClient(region_id="cn-beijing", credential=credential)


def _call(client: AcsClient, method: str, uri: str, body: dict | None = None, query: dict | None = None) -> dict:
    request = CommonRequest()
    request.set_accept_format("json")
    request.set_domain(DOMAIN)
    request.set_version("2023-09-30")
    request.set_protocol_type("https")
    request.set_method(method)
    request.set_uri_pattern(uri)
    request.add_header("Content-Type", "application/json")
    for key, value in (query or {}).items():
        request.add_query_param(key, value)
    if body is not None:
        request.set_content(json.dumps(body).encode("utf-8"))
    raw = client.do_action_with_exception(request)
    return json.loads(raw)


def _bucket(settings: Settings) -> oss2.Bucket:
    auth = oss2.Auth(
        settings.alibaba_cloud_access_key_id.strip(),
        settings.alibaba_cloud_access_key_secret.strip(),
    )
    endpoint = f"https://oss-{settings.oss_region.strip()}.aliyuncs.com"
    return oss2.Bucket(auth, endpoint, settings.oss_bucket.strip())


def transcribe_wav(settings: Settings, wav: bytes, object_key: str) -> str:
    """上传私有文件，听悟读完后删除。只开转写和说话人区分，不开摘要。"""
    if not settings.tingwu_ready:
        raise TingwuError("tingwu_not_configured", "还没有配好通义听悟")
    bucket = _bucket(settings)
    client = _client(settings)
    try:
        bucket.put_object(object_key, wav)
        file_url = bucket.sign_url("GET", object_key, 4 * 3600, slash_safe=True)
        created = _call(
            client,
            "PUT",
            "/openapi/tingwu/v2/tasks",
            {
                "AppKey": settings.tingwu_app_key.strip(),
                "Input": {"SourceLanguage": "cn", "FileUrl": file_url, "TaskKey": object_key[-40:]},
                "Parameters": {
                    "Transcription": {
                        "DiarizationEnabled": True,
                        "Diarization": {"SpeakerCount": 0},
                    }
                },
            },
            {"type": "offline"},
        )
        task_id = str((created.get("Data") or {}).get("TaskId") or "")
        if created.get("Code") not in {0, "0"} or not task_id:
            raise TingwuError("tingwu_failed", "听悟没有开始转写，录音还在，可以再试一次")
        status = ""
        result_url = ""
        for _ in range(240):
            info = _call(client, "GET", f"/openapi/tingwu/v2/tasks/{task_id}")
            data = info.get("Data") or {}
            status = str(data.get("TaskStatus") or "")
            if status == "COMPLETED":
                result_url = str((data.get("Result") or {}).get("Transcription") or "")
                break
            if status == "FAILED":
                raise TingwuError("tingwu_failed", "听悟没有转出文字，录音还在，可以再试一次")
            time.sleep(5)
        if status != "COMPLETED" or not result_url:
            raise TingwuError("tingwu_failed", "听悟还没转完，录音还在，可以再试一次")
        payload = httpx.get(result_url, timeout=60).json()
        text = format_turns((payload.get("Transcription") or {}).get("Paragraphs") or [])
        if not text:
            raise TingwuError("tingwu_failed", "这段录音没有转出文字")
        return text
    except TingwuError:
        raise
    except Exception as exc:
        raise TingwuError("tingwu_failed", "听悟没有转出文字，录音还在，可以再试一次") from exc
    finally:
        try:
            bucket.delete_object(object_key)
        except Exception:
            pass


def ensure_full_transcript(
    db: Session,
    settings: Settings,
    fernet,
    data_dir: Path,
    owner_id: str,
    call_id: str,
) -> None:
    """新通话转写双方合成录音，历史通话保留已有原声与逐字稿。"""
    existing = {row.track: row.text for row in transcripts_for(db, owner_id, call_id)}
    if (existing.get("full") or "").strip():
        return
    if not settings.tingwu_ready:
        raise TingwuError("tingwu_not_configured", "全量转写服务尚未配置，请联系管理家属")
    conversation = read_audio(db, fernet, data_dir, owner_id, call_id, "conversation")
    if conversation is None and db.get(CallTimeline, call_id) is not None:
        raise TingwuError("conversation_missing", "这次访谈的合成录音缺失，请先核对录音文件")
    pcm = conversation if conversation is not None else read_audio(db, fernet, data_dir, owner_id, call_id, "user")
    if not pcm:
        raise TingwuError("audio_missing", "没有找到本次访谈录音，无法完成全量转写")
    from app.services.store import pcm16_to_wav

    text = redact_sensitive(transcribe_wav(settings, pcm16_to_wav(pcm, 16000), f"yitan/{call_id}.wav"))
    assistant = (existing.get("realtime_assistant") or "").strip()
    if assistant and conversation is None:
        text = text + "\n\n[访谈者，这一路没有逐句时间]\n" + assistant
    replace_transcript(db, owner_id, call_id, "full", text)
    replace_transcript(db, owner_id, call_id, "transcription_source", "conversation" if conversation is not None else "legacy_microphone")
