import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import wave
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings
from app.core.crypto import build_fernet
from app.services.cloudbase import CloudRepository


class EvidenceFailure(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise EvidenceFailure(code)


def safe_failure(kind, error, traceback):
    code = str(error) if isinstance(error, EvidenceFailure) else "record_operation_failed"
    print(json.dumps({"recorded": False, "failure_code": code, "model_calls": 0}))


sys.excepthook = safe_failure


def load_json(filename, private=False):
    if private:
        require(filename.stat().st_mode & 0o077 == 0, "private_evidence_permissions_invalid")
    return json.loads(filename.read_text(encoding="utf-8"))


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None, "evidence_timestamp_missing_timezone")
    return parsed


def verify_local(directory):
    result = load_json(directory / "result.json", private=True)
    prepared = load_json(directory / "prepared.json", private=True)
    pointer = load_json(directory / "private-call-pointer.json", private=True)
    require(result.get("passed") is True, "real_call_verification_not_passed")
    require(result.get("model_calls_started") == 1 and result.get("model_session_limit") == 1, "real_call_session_count_invalid")
    require(result.get("replay_count") == 2 and result.get("source_count") == 1, "same_source_replay_count_invalid")
    require(len(result.get("checks", [])) == 33 and len(set(result["checks"])) == 33, "real_call_check_count_invalid")
    require(result.get("cleanup") == {"audio_closed": True, "logout": True, "browser_closed": True}, "real_call_cleanup_incomplete")
    require(result.get("browser", {}).get("headless") is True, "real_call_browser_mode_invalid")
    coverage = result["coverage"]
    require(coverage["two_replays_completed"] is True and coverage["asr_unique_final_turns"] == 2 and coverage["asr_final_events"] == 2 and coverage["real_interruption"] is True and coverage["repeated_final_text_across_turns_observed"] is True, "two_turn_real_coverage_incomplete")
    required_checks = {"actual_asr_snapshot_replacement", "asr_final_single_entry", "distinct_user_turn_entries_preserved", "both_replays_have_distinct_final_turns", "actual_rms_drives_mouth", "all_three_saved_tracks_match_actual_pcm", "spoken_requires_actual_end", "reference_label_disclosed", "no_draft_or_transcription_request"}
    require(required_checks <= set(result["checks"]), "required_real_checks_missing")
    finished = timestamp(result["finished_at"])
    require(timestamp(prepared["prepared_at"]) <= timestamp(result["started_at"]) <= finished, "evidence_timestamp_order_invalid")
    require(bool(prepared.get("shared")), "shared_source_hashes_missing")
    for relative, expected in prepared["shared"].items():
        source = (ROOT / relative).resolve()
        require(source.is_relative_to(ROOT / "frontend"), "shared_source_path_invalid")
        require(hashlib.sha256(source.read_bytes()).hexdigest() == expected, "shared_source_hash_changed")
    require(result["source_sha256"] == prepared["source"]["sha256"], "authorized_source_hash_mismatch")
    source = (ROOT / prepared["source"]["path"]).resolve()
    require(source.is_relative_to(ROOT / ".intermediate") and hashlib.sha256(source.read_bytes()).hexdigest() == prepared["source"]["sha256"], "authorized_source_file_hash_mismatch")
    recording_events = [event for event in result["browser_observed"]["events"] if event["kind"] == "recording_started"]
    require(len(recording_events) == 2 and [event["replay_index"] for event in recording_events] == [1, 2] and all(event["same_authorized_source"] is True and event["during_assistant_playback"] is True for event in recording_events), "actual_replay_events_invalid")
    observations = result["browser_observed"]["observations"]
    require(any(item["rms"] > 0.005 and item["mouth"] > 0.02 and item["activity"] == "speaking" and item["muted"] is False for item in observations), "actual_rms_mouth_evidence_missing")
    for track, rate in [("user", 16000), ("assistant", 24000), ("conversation", 16000)]:
        filename = directory / (track + ".wav")
        require(filename.stat().st_mode & 0o077 == 0, "private_audio_permissions_invalid")
        with wave.open(str(filename), "rb") as audio:
            require(audio.getnchannels() == 1 and audio.getsampwidth() == 2 and audio.getframerate() == rate, "saved_audio_format_mismatch")
            pcm = audio.readframes(audio.getnframes())
        expected = result["audio"][track]
        require(expected["exact_pcm_match"] is True and expected["sample_rate"] == rate and len(pcm) == expected["bytes"] and hashlib.sha256(pcm).hexdigest() == expected["sha256"], "saved_audio_hash_mismatch")
    require(result["audio"]["actual_playback_component_rms"] > 0.001, "actual_playback_mix_not_verified")
    return result, pointer, finished


def verify_cloud(settings, result, pointer):
    qa = load_json(ROOT / ".intermediate/public-runtime/account.json", private=True)
    require(qa["owner_id"] == pointer["owner_id"], "qa_call_owner_mismatch")
    repository = CloudRepository(settings.cloudbase_env_id, settings.cloudbase_api_key)
    try:
        account = repository.one("accounts", filters={"owner_id": "eq." + pointer["owner_id"], "username": "eq." + qa["username"]}, columns="owner_id,role,enabled,analytics_excluded")
        require(account is not None and account["role"] == "member" and account["enabled"] is True and account["analytics_excluded"] is True, "qa_account_scope_invalid")
        call = repository.one("calls", filters={"id": "eq." + pointer["call_id"], "owner_id": "eq." + pointer["owner_id"]}, columns="status,failure_code,created_at,ended_at")
        require(call is not None and call["status"] == "ready" and not call["failure_code"] and call["ended_at"], "qa_call_not_ready")
        row = repository.one("call_timelines", filters={"call_id": "eq." + pointer["call_id"], "owner_id": "eq." + pointer["owner_id"]}, columns="payload")
    finally:
        repository.close()
    require(row is not None, "qa_timeline_missing")
    timeline = json.loads(build_fernet(settings.audio_fernet_key).decrypt(row["payload"].encode()))
    require(timeline.get("complete") is True and timeline.get("opening_requested") is True and timeline.get("first_audio_received") is True, "real_timeline_incomplete")
    require(timeline["frames"] == result["persisted_timeline"]["frames"] and abs(timeline["duration_ms"] / 1000 - result["audio"]["conversation"]["duration_seconds"]) < 0.001, "real_timeline_capture_mismatch")
    provider = timeline["provider_events"]
    require(provider.get("session.created") == 1 and provider.get("session.closed") == 1 and provider.get("conversation.item.input_audio_transcription.completed") == 2 and provider.get("response.output_audio.started", 0) >= 3 and provider.get("response.output_audio.delta", 0) > 0 and provider.get("response.output_audio.done", 0) > 0, "actual_provider_events_missing")
    users = timeline["captions"]["users"]
    require(len(users) == 2 and len({user["turn_id"] for user in users}) == 2 and all(user["status"] == "confirmed" and user["text"] for user in users) and users[0]["text"] == users[1]["text"], "persisted_two_user_turns_invalid")
    require(result["browser_observed"]["counts"].get("interrupted") == 2, "real_interruption_count_mismatch")
    assistants = timeline["captions"]["assistants"]
    require(sum(turn["playback"] == "interrupted" for turn in assistants) == 2, "persisted_interruption_mismatch")
    ended = {event["audio_seq"] for event in timeline["events"] if event["kind"] == "ended"}
    for turn in assistants:
        sequences = {value for segment in turn["segments"] for value in segment["audio_seqs"]}
        if turn["playback"] == "played":
            require(turn["audio_done"] is True and bool(sequences) and sequences <= ended and sequences <= set(turn["ended_audio"]), "played_without_actual_end")
    duration = (timestamp(call["ended_at"]) - timestamp(call["created_at"])).total_seconds()
    require(0 < duration < 100 and duration >= result["audio"]["conversation"]["duration_seconds"], "actual_call_duration_invalid")
    return duration


def build_record(finished, model, duration, call_id):
    require(re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", model) is not None, "voice_model_name_invalid")
    identifier = "sha256:" + hashlib.sha256(call_id.encode()).hexdigest()
    cells = [finished.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"), f"语音专属·豆包 {model}·真实录音字幕二维；同一授权片段回放2次；会话{duration:.3f}秒", identifier, "本次未采集", "本次未采集", "33项通过；三轨PCM一致；两轮去重与2次打断；实际播放音量驱动嘴部", "0", "通过"]
    require(len(cells) == 8 and all("|" not in cell and "\n" not in cell and "\r" not in cell for cell in cells), "smoke_record_cells_invalid")
    return identifier, "| " + " | ".join(cells) + " |\n"


def append_record(finished, model, duration, call_id):
    identifier, record = build_record(finished, model, duration, call_id)
    destination = ROOT / "docs/evidence/阶段4/冒烟记录.md"
    # 在文件锁保护下仅追加本次真实证据，重复运行保持文件原样。
    with destination.open("r+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        previous = handle.read()
        if identifier.encode() in previous:
            return False
        addition = (b"" if previous.endswith(b"\n") else b"\n") + record.encode()
        handle.seek(0, os.SEEK_END)
        require(handle.write(addition) == len(addition), "smoke_record_append_incomplete")
        handle.flush()
        os.fsync(handle.fileno())
        handle.seek(0)
        after = handle.read()
        require(after == previous + addition and after.count(identifier.encode()) == 1, "smoke_record_append_verification_failed")
    return True


def main():
    parser = argparse.ArgumentParser(description="只读核验真实语音显示证据，自动追加语音专属冒烟记录")
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / ".intermediate/replay-voice-check")
    arguments = parser.parse_args()
    directory = arguments.evidence_dir.resolve()
    require(directory.is_relative_to(ROOT / ".intermediate"), "evidence_directory_outside_private_workspace")
    result, pointer, finished = verify_local(directory)
    settings = Settings(_env_file=ROOT / "backend/.env.deploy")
    duration = verify_cloud(settings, result, pointer)
    recorded = append_record(finished, settings.doubao_model, duration, pointer["call_id"])
    print(json.dumps({"recorded": recorded, "already_recorded": not recorded, "checks": 33, "real_sessions": 1, "same_source_replays": 2, "three_pcm_hashes_verified": True, "cloud_call_ready": True, "excluded_member_verified": True, "persisted_user_turns": 2, "cleanup_verified": True, "session_seconds": round(duration, 3), "model_calls": 0, "destination": "docs/evidence/阶段4/冒烟记录.md"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
