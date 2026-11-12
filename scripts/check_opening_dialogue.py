import argparse
import asyncio
import audioop
import io
import json
import os
import re
import sys
import time
import wave
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".intermediate/opening-dialogue"
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.services.cloudbase import CloudRepository
from app.services.doubao import RealtimeSession, opening_greeting
from check_voice_opening import assert_idle


def private_write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
    path.chmod(0o600)


def private_json(path: Path, value: dict) -> None:
    private_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def real_title(repository: CloudRepository) -> str:
    manifest = json.loads((ROOT / "data/migration/li/manifest.json").read_text())
    if manifest.get("cloud_database_verified") is not True:
        raise ValueError("李奶奶已授权来源尚未通过云端资料核验")
    owner, call_id, profile_id = [manifest[key] for key in ["destination_owner_id", "cloud_call_id", "cloud_profile_id"]]
    call = repository.one("calls", filters={"owner_id": "eq." + owner, "id": "eq." + call_id, "profile_id": "eq." + profile_id}, columns="id")
    if not call:
        raise ValueError("已授权李奶奶来源的家庭或档案不一致")
    for chapter in repository.rows("chapters", filters={"owner_id": "eq." + owner, "call_id": "eq." + call_id}, columns="title", order="created_at.asc,id.asc", page_size=10):
        title = chapter["title"].strip()
        if 2 <= len(title) <= 24 and not re.search(r"[\d？?]", title):
            return title
    raise ValueError("李奶奶已授权成稿中没有适合简短回顾的标题")


def text_checks(title: str) -> dict:
    from app.services.brief import RETURNING_OPENING, callback_line

    previous = callback_line(title)
    texts = {"first": opening_greeting(), "callback": opening_greeting(previous), "returning": opening_greeting(RETURNING_OPENING)}
    if not previous or title not in texts["callback"]:
        raise AssertionError("回顾开场没有保留真实章节标题")
    if texts["returning"] == texts["first"]:
        raise AssertionError("有历史且无可靠内容时缺少再次见面的区别")
    results = {}
    for case, text in texts.items():
        questions = text.count("？") + text.count("?")
        clauses = [part.strip() for part in re.split(r"[，。？！,!?；;]", text) if len(part.strip()) >= 4]
        duplicates = [clause for clause, count in Counter(clauses).items() if count > 1]
        if not text.strip() or len(text) > 100 or questions != 1 or duplicates:
            raise AssertionError("开场篇幅、单个问题或重复语句校验失败：" + case)
        results[case] = {"text": text, "characters": len(text), "chinese_characters": len(re.findall(r"[\u3400-\u9fff]", text)), "questions": questions, "duplicate_clauses": duplicates, "previous": previous if case == "callback" else RETURNING_OPENING if case == "returning" else ""}
    return results


def qa_branches(repository: CloudRepository) -> list[dict]:
    from app.services.brief import RETURNING_OPENING, latest_cloud_opening

    state = json.loads((ROOT / ".intermediate/public-runtime/account.json").read_text())
    account = repository.one("accounts", filters={"username": "eq." + state["username"]}, columns="owner_id,role,analytics_excluded")
    if not account or account["role"] != "member" or account["analytics_excluded"] is not True:
        raise ValueError("只允许查询已从统计中排除的普通验证账号")
    owner = account["owner_id"]
    results = []
    for profile in repository.rows("profiles", filters={"owner_id": "eq." + owner}, columns="id"):
        call = next(repository.rows("calls", filters={"owner_id": "eq." + owner, "profile_id": "eq." + profile["id"]}, columns="id", order="created_at.desc,id.desc", page_size=1), None)
        if call is None:
            continue
        brief = latest_cloud_opening(repository, owner, profile["id"], call["id"])
        branch = "first" if not brief.spoken else "returning" if brief.spoken == RETURNING_OPENING else "callback"
        results.append({"branch": branch, "spoken_characters": len(brief.spoken), "context_characters": len(brief.context), "profile_id": profile["id"], "current_call_id": call["id"]})
    if not results:
        raise AssertionError("普通验证账号没有实际档案和通话可供只读检查")
    return results


async def real_voice(settings: Settings, case: str, text: dict, run_dir: Path) -> dict:
    session = RealtimeSession(settings)
    began = time.monotonic()
    requested = None
    first_audio_seconds = None
    complete_audio = False
    try:
        async with asyncio.timeout(35):
            await session.connect(text["previous"])
            await session._ws.send(json.dumps({"type": "input_audio_mute.commit", "event_id": str(uuid4())}))
            requested = time.monotonic()
            await session.say(text["text"])
            while True:
                timeout = 20 if first_audio_seconds is not None else 20 - (time.monotonic() - requested)
                if timeout <= 0:
                    raise TimeoutError("开场没有在20秒内返回真实音频")
                event = await asyncio.wait_for(session.receive_event(), timeout=timeout)
                if session.first_audio.is_set() and first_audio_seconds is None:
                    first_audio_seconds = time.monotonic() - requested
                if event.get("type") == "response.output_audio.done":
                    complete_audio = True
                    break
                if event.get("type") == "session.closed":
                    raise RuntimeError("供应商在完整开场前关闭了连接")
            await session.finish()
    finally:
        await asyncio.wait_for(session.close(), timeout=5)
        if session.assistant_pcm:
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as recording:
                recording.setnchannels(1)
                recording.setsampwidth(2)
                recording.setframerate(24000)
                recording.writeframes(bytes(session.assistant_pcm))
            private_write(run_dir / f"{case}.wav", buffer.getvalue())
    pcm = bytes(session.assistant_pcm)
    if not complete_audio or not pcm or audioop.rms(pcm, 2) <= 0 or not session.event_counts.get("session.closed"):
        raise AssertionError("真实完整开场或正常关闭没有通过校验")
    return {"case": case, "model": settings.doubao_model, "first_audio_seconds": round(first_audio_seconds, 3), "elapsed_seconds": round(time.monotonic() - began, 3), "audio_seconds": round(len(pcm) / 48000, 3), "pcm_bytes": len(pcm), "rms": audioop.rms(pcm, 2), "provider_events": dict(session.event_counts), "complete_audio": complete_audio, "normal_finish": True, "uploaded_user_audio_bytes": 0, "retries": 0}


async def main() -> None:
    parser = argparse.ArgumentParser(description="验证首次、章节回顾及再次见面开场，只进行两通限定时长的真实豆包主动开场")
    parser.add_argument("--run", action="store_true", help="源代码准备好后，执行两通真实语音；未提供时只检查真实素材与开场文字")
    args = parser.parse_args()
    settings = Settings(_env_file=ROOT / "backend/.env.deploy")
    repository = CloudRepository(settings.cloudbase_env_id, settings.cloudbase_api_key)
    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    WORK.chmod(0o700)
    run_id = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    run_dir = WORK / run_id
    run_dir.mkdir(mode=0o700)
    result = {"checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "run_id": run_id, "passed": False, "voice_requested": args.run, "voices": [], "retries": 0}
    try:
        result["idle_check"] = await asyncio.to_thread(assert_idle, repository)
        title = await asyncio.to_thread(real_title, repository)
        texts = text_checks(title)
        result["texts"] = texts
        result["actual_qa_branches"] = await asyncio.to_thread(qa_branches, repository)
        private_json(run_dir / "result.json", result)
        print(json.dumps({"stage": "text_checks", "cases": {key: {"characters": value["characters"], "questions": value["questions"], "duplicate_clauses": len(value["duplicate_clauses"])} for key, value in texts.items()}, "real_title_characters": len(title), "model_calls": 0}), flush=True)
        print(json.dumps({"stage": "actual_qa_branches", "items": [{key: value[key] for key in ["branch", "spoken_characters", "context_characters"]} for value in result["actual_qa_branches"]]}), flush=True)
        if args.run:
            for case in ["first", "callback"]:
                await asyncio.to_thread(assert_idle, repository)
                voice = await real_voice(settings, case, texts[case], run_dir)
                result["voices"].append(voice)
                private_json(run_dir / "result.json", result)
                evidence = ROOT / "docs/evidence/阶段4/冒烟记录.md"
                if not evidence.exists():
                    raise FileNotFoundError("缺少阶段4冒烟记录文件")
                label = "首次铺垫" if case == "first" else "真实章节标题回顾"
                with evidence.open("a", encoding="utf-8") as handle:
                    handle.write(f"| {datetime.now(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M:%S} | 豆包 {settings.doubao_model}·{label}主动开场；{voice['elapsed_seconds']}秒 | {run_id}:{case} | 接口未提供 | 接口未提供 | {texts[case]['characters']}字符、单个问题；{voice['first_audio_seconds']}秒首声音、完整PCM、正常finish | 0 | 通过 |\n")
                print(json.dumps({"stage": "real_voice_passed", "case": case, "first_audio_seconds": voice["first_audio_seconds"], "audio_seconds": voice["audio_seconds"], "pcm_bytes": voice["pcm_bytes"]}), flush=True)
        result["passed"] = True
    finally:
        private_json(run_dir / "result.json", result)
        repository.close()
    print(json.dumps({"passed": result["passed"], "text_cases": len(result["texts"]), "real_voice_calls": len(result["voices"]), "voice_only": True}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
