import json
import time
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Chapter, FactItem, MemoryItem, Paragraph, ParagraphEvidence, WritingRun, utcnow
from app.services.writing import Evidence, OutputIncomplete, generate
from app.services.writing_trace import WritingTrace
from app.services.grounding import elder_text, keep_grounded, transcripts_disagree
from app.services.memoir import reader_fact
from app.services.redact import redact_sensitive
from app.services.store import new_id, transcripts_for

_PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
PROMPT = (_PROMPTS / "chapter.txt").read_text(encoding="utf-8")
TIMELINE_PROMPT = (_PROMPTS / "timeline.txt").read_text(encoding="utf-8")
REVIEW_PROMPT = (_PROMPTS / "review.txt").read_text(encoding="utf-8")
MODEL_METRICS: ContextVar[list | None] = ContextVar("model_metrics", default=None)


class DraftError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _Memory(BaseModel):
    kind: str = "event"
    name: str = ""
    detail: str = ""
    source_quote: str = ""


class _Paragraph(BaseModel):
    text: str = ""
    source_quote: str = ""
    evidence: list[Evidence] = Field(default_factory=list)


class _Uncertain(BaseModel):
    topic: str = ""
    detail: str = ""
    source_quote: str = ""


class _Node(BaseModel):
    when: str = ""
    event: str = ""
    who: str = ""
    source_quote: str = ""


class _Timeline(BaseModel):
    nodes: list[_Node] = Field(default_factory=list)
    uncertain: list[_Uncertain] = Field(default_factory=list)


class _ChapterDraft(BaseModel):
    title: str = "这段回忆"
    paragraphs: list[_Paragraph] = Field(default_factory=list)


class _Book(BaseModel):
    chapters: list[_ChapterDraft] = Field(default_factory=list)
    memories: list[_Memory] = Field(default_factory=list)
    uncertain: list[_Uncertain] = Field(default_factory=list)


class _Draft(BaseModel):
    title: str = "这段回忆"
    memories: list[_Memory] = Field(default_factory=list)
    paragraphs: list[_Paragraph] = Field(default_factory=list)
    uncertain: list[_Uncertain] = Field(default_factory=list)


class _Review(BaseModel):
    accept: bool = False
    title: str = ""
    paragraphs: list[_Paragraph] = Field(default_factory=list)


def _parse(raw: str) -> _Draft:
    data = json.loads(raw)
    return _Draft.model_validate(data)


def _load(raw: str, model: type[BaseModel]) -> BaseModel:
    try:
        return model.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DraftError("draft_failed", "这一段没有整理出来，原话还在，可以再试一次") from exc


async def _complete(settings: Settings, system: str, user: str) -> str:
    last_error: Exception | None = None
    started = time.monotonic()
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                response = await client.post(
                    settings.writing_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {settings.writing_key}"},
                    json={
                        "model": settings.writing_model,
                        **({"enable_thinking": True} if settings.writing_provider == "qwen" else {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}),
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                        "max_tokens": 32768,
                    },
                )
            response.raise_for_status()
            payload = response.json()
            metrics = MODEL_METRICS.get()
            if metrics is not None:
                metrics.append({"request_id": payload.get("id", "-"), "model": payload.get("model", settings.writing_model), "usage": payload.get("usage", {}), "elapsed": time.monotonic() - started, "retries": attempt, "finish_reason": payload["choices"][0].get("finish_reason")})
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":
                raise OutputIncomplete()
            if choice.get("finish_reason") != "stop":
                raise DraftError("model_interrupted", "模型未完整返回内容，生成记录已保留")
            return choice["message"]["content"]
        except (DraftError, OutputIncomplete):
            raise
        except Exception as exc:
            last_error = exc
    raise DraftError("draft_failed", "这一段没有整理出来，原话还在，可以再试一次") from last_error


async def compose(settings: Settings, source: str, trace=None) -> _Book:
    trace = trace or WritingTrace(settings, "standalone", source)
    async def complete(system, user):
        try:
            return await _complete(settings, system, user)
        finally:
            trace.payload["metrics"] = MODEL_METRICS.get() or []
            trace.save()
    return _Book.model_validate(await generate(complete, source, trace.record, trace.cache_read, trace.cache_write))


async def _ask(settings: Settings, source: str) -> _Draft:
    book = await compose(settings, source)
    paragraphs: list[_Paragraph] = []
    title = "这段回忆"
    for chapter in book.chapters:
        title = chapter.title or title
        paragraphs.extend(chapter.paragraphs)
    return _Draft(title=title, memories=book.memories, paragraphs=paragraphs, uncertain=book.uncertain)


async def _review(settings: Settings, source: str, draft: _Draft) -> _Review:
    packet = json.dumps(
        {
            "source": source,
            "draft_title": draft.title,
            "paragraphs": [item.model_dump() for item in draft.paragraphs],
        },
        ensure_ascii=False,
    )
    return _Review.model_validate(json.loads(await _complete(settings, REVIEW_PROMPT, packet)))


def _clear_previous(db: Session, owner_id: str, call_id: str) -> None:
    chapter_ids = list(db.scalars(select(Chapter.id).where(Chapter.owner_id == owner_id, Chapter.call_id == call_id)))
    if chapter_ids:
        paragraph_ids = select(Paragraph.id).where(Paragraph.chapter_id.in_(chapter_ids))
        db.execute(delete(ParagraphEvidence).where(ParagraphEvidence.paragraph_id.in_(paragraph_ids)))
        db.execute(delete(Paragraph).where(Paragraph.chapter_id.in_(chapter_ids)))
        db.execute(delete(Chapter).where(Chapter.id.in_(chapter_ids)))
    db.execute(delete(MemoryItem).where(MemoryItem.owner_id == owner_id, MemoryItem.call_id == call_id))
    db.execute(delete(FactItem).where(FactItem.owner_id == owner_id, FactItem.call_id == call_id))
    db.flush()


async def build_draft(db: Session, settings: Settings, owner_id: str, call_id: str, profile_id: str) -> Chapter:
    if not settings.writing_ready:
        raise DraftError("model_not_configured", "还没有配置写作服务的钥匙")
    rows = transcripts_for(db, owner_id, call_id)
    texts = {row.track: row.text for row in rows}
    raw_source = elder_text(texts)
    source = redact_sensitive(raw_source)
    card_removed = source != raw_source.strip()
    if not source:
        raise DraftError("no_transcript", "这通通话还没有老人的原话，不能写章节")
    trace = WritingTrace(settings, call_id, source, owner_id)
    run = WritingRun(id=trace.id, owner_id=owner_id, call_id=call_id, status="running", trace_path=str(trace.path))
    db.add(run)
    db.commit()
    metrics = []
    token = MODEL_METRICS.set(metrics)
    try:
        book = await compose(settings, source, trace)
    except Exception as exc:
        code = getattr(exc, "code", "writing_failed")
        trace.finish("failed", metrics, code)
        run.status, run.error_code = "failed", code
        db.commit()
        raise
    finally:
        MODEL_METRICS.reset(token)
    saved: list[tuple[str, list[tuple[str, str, list[Evidence]]]]] = []
    for chapter_draft in book.chapters:
        kept_lines: list[tuple[str, str, list[Evidence]]] = []
        for item in chapter_draft.paragraphs:
            text = redact_sensitive(item.text).strip()
            quote = redact_sensitive(item.source_quote).strip()
            if text != (item.text or "").strip() or quote != (item.source_quote or "").strip():
                card_removed = True
            if not text or not item.evidence:
                raise DraftError("evidence_missing", "正文缺少逐句原文证据")
            kept_lines.append((text, quote, item.evidence))
        if kept_lines:
            title = (redact_sensitive(chapter_draft.title).strip() or "这段回忆")[:80]
            saved.append((title, kept_lines))
    if not saved:
        raise DraftError("not_a_memoir", "这通还写不成一段能给家人读的回忆。原话还在，可以再试一次")
    trace.record("previous_edition", chapter_views(db, owner_id, call_id))
    _clear_previous(db, owner_id, call_id)
    started = utcnow()
    chapter: Chapter | None = None
    for index, (title, lines) in enumerate(saved):
        chapter = Chapter(
            id=new_id(),
            owner_id=owner_id,
            call_id=call_id,
            title=title,
            created_at=started + timedelta(seconds=index),
        )
        db.add(chapter)
        for position, (text, quote, evidence) in enumerate(lines):
            paragraph_id = new_id()
            db.add(
                Paragraph(
                    id=paragraph_id,
                    owner_id=owner_id,
                    chapter_id=chapter.id,
                    position=position,
                    text=text,
                    source_quote=quote,
                )
            )
            db.flush()
            for ref in evidence:
                db.add(ParagraphEvidence(id=new_id(), owner_id=owner_id, paragraph_id=paragraph_id, source_line=ref.line, quote=ref.quote))
    allowed = {"person", "place", "time", "event"}
    for item in book.memories:
        name = redact_sensitive(item.name or "未命名")[:80]
        detail = redact_sensitive(item.detail)
        quote = redact_sensitive(item.source_quote).strip()
        if not keep_grounded(name or detail, quote, source):
            continue
        db.add(
            MemoryItem(
                id=new_id(),
                owner_id=owner_id,
                profile_id=profile_id,
                call_id=call_id,
                kind=item.kind if item.kind in allowed else "event",
                name=name or "未命名",
                detail=detail,
                source_quote=quote,
            )
        )
    if card_removed:
        db.add(
            FactItem(
                id=new_id(),
                owner_id=owner_id,
                call_id=call_id,
                topic="银行卡号已略去",
                detail="原话里有一串银行卡号，没有写进正文，也没有记进记忆",
                source_quote="",
            )
        )
    if transcripts_disagree(texts):
        db.add(
            FactItem(
                id=new_id(),
                owner_id=owner_id,
                call_id=call_id,
                topic="两路转写不一致",
                detail="两遍听写不完全一样，正文按更完整的那遍来写",
                source_quote=source[:180],
            )
        )
    for item in book.uncertain:
        db.add(
            FactItem(
                id=new_id(),
                owner_id=owner_id,
                call_id=call_id,
                topic=(item.topic or "待核对")[:80],
                detail=redact_sensitive(item.detail),
                source_quote=redact_sensitive(item.source_quote),
            )
        )
    run.status = "completed"
    trace.finish("completed", metrics)
    db.commit()
    if chapter is None:
        raise DraftError("not_a_memoir", "这通还写不成一段能给家人读的回忆。原话还在，可以再试一次")
    db.refresh(chapter)
    return chapter


def _fact_for_reader(row: FactItem) -> dict:
    topic, detail = reader_fact(row.topic, row.detail)
    return {"id": row.id, "topic": topic, "detail": detail, "status": row.status}


def chapter_views(db: Session, owner_id: str, call_id: str) -> list[dict]:
    """返回本次通话的全部章节，核对事项放在首章。"""
    chapters = list(
        db.scalars(
            select(Chapter)
            .where(Chapter.owner_id == owner_id, Chapter.call_id == call_id)
            .order_by(Chapter.created_at, Chapter.id)
        )
    )
    if not chapters:
        return []
    facts = list(db.scalars(select(FactItem).where(FactItem.owner_id == owner_id, FactItem.call_id == call_id)))
    memories = list(db.scalars(select(MemoryItem).where(MemoryItem.owner_id == owner_id, MemoryItem.call_id == call_id)))
    fact_rows = [_fact_for_reader(row) for row in facts]
    memory_rows = [{"kind": row.kind, "name": row.name, "detail": row.detail} for row in memories]
    views: list[dict] = []
    for index, chapter in enumerate(chapters):
        paragraphs = list(
            db.scalars(
                select(Paragraph)
                .where(Paragraph.owner_id == owner_id, Paragraph.chapter_id == chapter.id)
                .order_by(Paragraph.position)
            )
        )
        views.append(
            {
                "id": chapter.id,
                "title": chapter.title,
                "paragraphs": [
                    {"id": row.id, "text": row.text, "source_quote": row.source_quote, "score": row.score,
                     "source_quotes": [{"line": ref.source_line, "quote": ref.quote} for ref in db.scalars(select(ParagraphEvidence).where(ParagraphEvidence.owner_id == owner_id, ParagraphEvidence.paragraph_id == row.id).order_by(ParagraphEvidence.source_line))]} for row in paragraphs
                ],
                "facts": fact_rows if index == 0 else [],
                "memories": memory_rows if index == 0 else [],
            }
        )
    return views


def chapter_view(db: Session, owner_id: str, call_id: str) -> dict | None:
    """开场提起的是后一章，下一次从人生后半接着说。"""
    views = chapter_views(db, owner_id, call_id)
    return views[-1] if views else None


def update_fact(db: Session, owner_id: str, fact_id: str, status: str, detail: str) -> bool:
    row = db.get(FactItem, fact_id)
    if row is None or row.owner_id != owner_id or status not in {"open", "confirmed", "rejected"}:
        return False
    row.status = status
    if detail.strip():
        row.detail = redact_sensitive(detail)[:500]
    db.commit()
    return True


def score_paragraph(db: Session, owner_id: str, paragraph_id: str, score: int) -> bool:
    row = db.get(Paragraph, paragraph_id)
    if row is None or row.owner_id != owner_id or score not in {1, 2, 3}:
        return False
    row.score = score
    db.commit()
    return True
