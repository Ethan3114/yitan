from dataclasses import asdict, dataclass, field

from app.services.redact import redact_sensitive


def _text(event: dict) -> str:
    for key in ("text", "delta", "transcript", "content"):
        value = event.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or len(value) > 64000:
            raise ValueError("语音文字事件格式无效")
        return value
    return ""


def _identifier(event: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = event.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or len(value) > 256:
            raise ValueError("语音轮次标识格式无效")
        return value
    return ""


@dataclass
class UserCaption:
    turn_id: str
    provider_query_id: str = ""
    text: str = ""
    status: str = "candidate"


@dataclass
class AssistantCaption:
    turn_id: str
    provider_query_id: str = ""
    provider_reply_id: str = ""
    text: str = ""
    text_done: bool = False
    audio_done: bool = False
    response_done: bool = False
    script_text: str = ""
    script_request_id: str = ""
    playback: str = "unconfirmed"
    segments: list[dict] = field(default_factory=list)
    ended_audio: set[int] = field(default_factory=set)


class RealtimeCaptions:
    def __init__(self):
        self.users: list[UserCaption] = []
        self.assistants: list[AssistantCaption] = []
        self._users_by_query: dict[str, UserCaption] = {}
        self._assistants_by_reply: dict[str, AssistantCaption] = {}
        self._user: UserCaption | None = None
        self._assistant: AssistantCaption | None = None
        self._audio_assistant: AssistantCaption | None = None
        self._audio_seq = 0
        self._cancel_requests: dict[str, list[str]] = {}
        self._script_requests: list[dict] = []

    def _user_turn(self, event: dict) -> UserCaption:
        query = _identifier(event, ("query_id", "question_id", "item_id"))
        turn = self._users_by_query.get(query) if query else None
        if turn is None and self._user and self._user.status == "candidate" and (not query or not self._user.provider_query_id):
            turn = self._user
        if turn is None:
            turn = UserCaption(f"u{len(self.users) + 1}", query)
            self.users.append(turn)
        if query:
            turn.provider_query_id = query
            self._users_by_query[query] = turn
        self._user = turn
        return turn

    def _assistant_turn(self, event: dict, *, audio=False) -> AssistantCaption:
        query = _identifier(event, ("query_id", "question_id"))
        reply = _identifier(event, ("reply_id", "response_id"))
        turn = self._assistants_by_reply.get(reply) if reply else None
        if turn is None:
            turn = self._assistant
            if turn and (turn.playback == "interrupted" or (query and turn.provider_query_id and query != turn.provider_query_id) or (reply and turn.provider_reply_id and reply != turn.provider_reply_id)):
                turn = None
            if turn and (turn.response_done or (turn.audio_done and (audio or turn.text_done))):
                turn = None
        if turn is None:
            turn = AssistantCaption(f"a{len(self.assistants) + 1}", query, reply)
            self.assistants.append(turn)
        if query:
            turn.provider_query_id = query
        if reply:
            turn.provider_reply_id = reply
            self._assistants_by_reply[reply] = turn
        if turn.playback != "interrupted" and (self._assistant is None or self.assistants.index(turn) >= self.assistants.index(self._assistant)):
            self._assistant = turn
        if audio and turn.playback != "interrupted" and (self._audio_assistant is None or self.assistants.index(turn) >= self.assistants.index(self._audio_assistant)):
            self._audio_assistant = turn
        return turn

    def _interrupt(self) -> list[str]:
        affected = []
        for turn in self.assistants:
            if turn.playback not in {"played", "interrupted"}:
                turn.playback = "interrupted"
                affected.append(turn.turn_id)
        self._assistant = None
        return affected

    def _user_event(self, turn: UserCaption) -> dict:
        return {"type": "user_text", "turn_id": turn.turn_id, "text": redact_sensitive(turn.text), "text_mode": "replace", "final": turn.status == "confirmed", "status": turn.status, "provider_query_id": turn.provider_query_id}

    def _assistant_event(self, turn: AssistantCaption) -> dict:
        return {"type": "assistant_text", "turn_id": turn.turn_id, "text": redact_sensitive(turn.text), "text_mode": "replace", "final": turn.text_done, "source": "model", "provider_query_id": turn.provider_query_id, "provider_reply_id": turn.provider_reply_id}

    def _done_event(self, turn: AssistantCaption) -> dict:
        return {"type": "assistant_done", "turn_id": turn.turn_id, "text_done": turn.text_done, "audio_done": turn.audio_done, "interrupted": turn.playback == "interrupted"}

    def _new_segment(self, turn: AssistantCaption, text: str) -> tuple[dict, dict]:
        segment = {"segment_id": f"{turn.turn_id}:s{len(turn.segments) + 1}", "text": text, "audio_seqs": []}
        turn.segments.append(segment)
        if turn.playback != "interrupted" and (self._audio_assistant is None or self.assistants.index(turn) >= self.assistants.index(self._audio_assistant)):
            self._audio_assistant = turn
        visible_text = redact_sensitive(text)
        event = {"type": "assistant_tts", "turn_id": turn.turn_id, "segment_id": segment["segment_id"], "text": visible_text, "text_available": bool(visible_text), "source": "tts", "state": "queued", "discarded": turn.playback == "interrupted", "provider_query_id": turn.provider_query_id, "provider_reply_id": turn.provider_reply_id}
        return segment, event

    def _bind_script(self, turn: AssistantCaption) -> list[dict]:
        if turn.playback == "interrupted" or turn.script_request_id:
            return []
        position = self.assistants.index(turn)
        for request in self._script_requests:
            if request["status"] != "sent" or position < request["assistant_boundary"]:
                continue
            if request["user_generation"] != len(self.users):
                request["status"] = "canceled"
                continue
            request.update(status="bound", turn_id=turn.turn_id)
            turn.script_text = request["text"]
            turn.script_request_id = request["request_id"]
            return [{"type": "assistant_text", "turn_id": turn.turn_id, "text": redact_sensitive(turn.script_text), "text_mode": "replace", "final": True, "source": "script", "reference_only": True}]
        return []

    def absorb(self, event: dict) -> list[dict]:
        kind = event.get("type")
        outgoing = []
        if kind in {"input_audio_buffer.speech_started", "conversation.item.input_audio_transcription.started"}:
            turn = self._user_turn(event)
            for request in self._script_requests:
                if request["status"] == "sent":
                    request["status"] = "canceled"
            outgoing.append({"type": "interrupted", "reason": "user_speech", "user_turn_id": turn.turn_id, "assistant_turn_ids": self._interrupt()})
            outgoing.append(self._user_event(turn))
        elif kind in {"conversation.item.input_audio_transcription.delta", "conversation.item.input_audio_transcription.completed", "conversation.item.input_audio_transcription.failed"}:
            turn = self._user_turn(event)
            text = _text(event)
            if text:
                # 官方 Web Demo 的 ASR delta 是当前整段候选稿，后续结果覆盖前文。
                turn.text = text
            if kind.endswith(".completed"):
                turn.status = "confirmed"
            elif kind.endswith(".failed"):
                turn.status = "failed"
            outgoing.append(self._user_event(turn))
        elif kind in {"response.output_text.delta", "response.output_text.done"}:
            turn = self._assistant_turn(event)
            text = _text(event)
            if kind.endswith(".delta"):
                turn.text += text
            else:
                if text:
                    turn.text = text
                turn.text_done = True
            outgoing.append(self._assistant_event(turn))
            if turn.text_done:
                outgoing.append(self._done_event(turn))
        elif kind == "response.output_audio.started":
            turn = self._assistant_turn(event, audio=True)
            _, started = self._new_segment(turn, _text(event))
            outgoing.extend(self._bind_script(turn))
            outgoing.append(started)
        elif kind == "response.output_audio.delta":
            audio = event.get("delta") or event.get("audio") or ""
            if not audio:
                return outgoing
            reply = _identifier(event, ("reply_id", "response_id"))
            turn = self._assistants_by_reply.get(reply) if reply else self._audio_assistant
            if not reply and turn and (turn.audio_done or turn.response_done):
                turn = None
            if turn is None:
                turn = self._assistant_turn(event, audio=True)
            if not turn.segments:
                _, started = self._new_segment(turn, "")
                outgoing.append(started)
            outgoing.extend(self._bind_script(turn))
            segment = turn.segments[-1]
            sequence = self._audio_seq
            self._audio_seq += 1
            segment["audio_seqs"].append(sequence)
            outgoing.append({"type": "audio", "pcm": audio, "turn_id": turn.turn_id, "segment_id": segment["segment_id"], "audio_seq": sequence, "discarded": turn.playback == "interrupted"})
        elif kind == "response.output_audio.done":
            reply = _identifier(event, ("reply_id", "response_id"))
            turn = self._assistants_by_reply.get(reply) if reply else self._audio_assistant
            if turn:
                turn.audio_done = True
                self._confirm_playback(turn)
                outgoing.append(self._done_event(turn))
        elif kind == "response.canceled":
            reply = _identifier(event, ("reply_id", "response_id"))
            turn = self._assistants_by_reply.get(reply) if reply else None
            event_id = _identifier(event, ("event_id",))
            requested = self._cancel_requests.pop(event_id, None)
            if requested is None and self._cancel_requests:
                requested = self._cancel_requests.pop(next(iter(self._cancel_requests)))
            targets = [turn.turn_id] if turn else requested or []
            affected = []
            for target in self.assistants:
                if target.turn_id in targets and target.playback != "played":
                    target.playback = "interrupted"
                    affected.append(target.turn_id)
            outgoing.append({"type": "interrupted", "reason": "response_canceled", "user_turn_id": self._user.turn_id if self._user else "", "assistant_turn_ids": affected})
        elif kind == "response.done":
            reply = _identifier(event, ("reply_id", "response_id"))
            turn = self._assistants_by_reply.get(reply) if reply else self._audio_assistant
            if turn:
                turn.response_done = True
        return outgoing

    def request_cancel(self, event_id: str) -> None:
        self._cancel_requests[event_id] = [turn.turn_id for turn in self.assistants if turn.playback != "played"]

    def request_script(self, request_id: str, text: str, user_generation: int, assistant_boundary: int) -> list[dict]:
        self._script_requests.append({"request_id": request_id, "text": text, "user_generation": user_generation, "assistant_boundary": assistant_boundary, "status": "sent" if user_generation == len(self.users) else "canceled"})
        turn = self._audio_assistant
        return self._bind_script(turn) if turn and turn.segments else []

    def _confirm_playback(self, turn: AssistantCaption) -> None:
        audio = {sequence for segment in turn.segments for sequence in segment["audio_seqs"]}
        if turn.playback != "interrupted" and turn.audio_done and audio and audio <= turn.ended_audio:
            turn.playback = "played"

    def playback_event(self, payload: dict) -> None:
        turn_id = payload.get("turn_id")
        turn = next((item for item in self.assistants if item.turn_id == turn_id), None)
        if turn is None:
            return
        kind = payload.get("kind")
        if kind == "interrupt" and turn.playback != "played":
            turn.playback = "interrupted"
        elif kind == "playback_started" and turn.playback not in {"played", "interrupted"}:
            turn.playback = "playing"
        elif kind == "ended" and isinstance(payload.get("audio_seq"), int):
            turn.ended_audio.add(payload["audio_seq"])
            self._confirm_playback(turn)

    def snapshot(self) -> dict:
        assistants = []
        for turn in self.assistants:
            row = asdict(turn)
            row["text_source"] = "model"
            row["text"] = redact_sensitive(row["text"])
            row["script_text"] = redact_sensitive(row["script_text"])
            for segment in row["segments"]:
                segment["text_source"] = "tts"
                segment["text"] = redact_sensitive(segment["text"])
            row["ended_audio"] = sorted(turn.ended_audio)
            assistants.append(row)
        users = [{**asdict(turn), "text": redact_sensitive(turn.text)} for turn in self.users]
        scripts = [{**request, "text": redact_sensitive(request["text"])} for request in self._script_requests]
        return {"version": 1, "users": users, "assistants": assistants, "script_requests": scripts}

    @property
    def user_text(self) -> list[str]:
        return [turn.text for turn in self.users if turn.text]

    @property
    def assistant_text(self) -> list[str]:
        return [turn.text for turn in self.assistants if turn.text]
