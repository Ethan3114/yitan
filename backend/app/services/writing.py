import json
import hashlib
import asyncio
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


class WritingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class OutputIncomplete(WritingError):
    def __init__(self):
        super().__init__("output_incomplete", "生成内容尚未完整返回，已保存处理记录")


class Shape(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Shape):
    line: int
    quote: str = Field(min_length=2)


class Event(Shape):
    id: str
    subject: str
    when: str
    where: str
    background: str
    process: str
    outcome: str
    connection: str
    evidence: list[Evidence] = Field(min_length=1)
    certainty: Literal["confirmed", "uncertain"]
    doubt: str


class Coverage(Shape):
    start: int
    end: int
    event_ids: list[str]
    reason: str


class Catalogue(Shape):
    events: list[Event]
    coverage: list[Coverage]


class Held(Shape):
    event_id: str
    reason: str = Field(min_length=1)


class ChapterPlan(Shape):
    title: str
    event_ids: list[str]
    bridge: str


class Plan(Shape):
    people: list[str]
    chapters: list[ChapterPlan] = Field(min_length=1)
    held: list[Held]


class Sentence(Shape):
    text: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class Prose(Shape):
    event_ids: list[str] = Field(min_length=1)
    sentences: list[Sentence] = Field(min_length=1)


class Page(Shape):
    paragraphs: list[Prose] = Field(min_length=1)


class UncertainFact(Shape):
    topic: str
    detail: str
    evidence: list[Evidence] = Field(min_length=1)


class Repair(Shape):
    title: str
    event_ids: list[str]
    held: list[Held]
    uncertain: list[UncertainFact]


class SentenceCheck(Shape):
    id: str
    supported: bool
    readable: bool
    attribution_correct: bool
    evidence_complete: bool
    reason: str


class Review(Shape):
    checks: list[SentenceCheck]
    missing_events: list[str]
    issues: list[str]


class ChapterCheck(Shape):
    chapter: int
    accepted: bool
    issues: list[str]


class BookReview(Shape):
    chapters: list[ChapterCheck]
    issues: list[str]


class SentenceEdit(Shape):
    id: str
    text: str
    evidence: list[Evidence]


class SentenceEdits(Shape):
    edits: list[SentenceEdit]


class WritingProgress(Shape):
    plan: Plan
    drafts: list[list[Prose]]
    uncertain: list[UncertainFact]
    feedback: list[list[str]] = Field(default_factory=list)


def apply_sentence_edits(paragraphs: list[Prose], edits: SentenceEdits, event_ids: list[str], lines: list[dict]) -> list[Prose]:
    ids = [edit.id for edit in edits.edits]
    allowed = {row["id"] for row in sentence_packet(paragraphs)}
    if len(ids) != len(set(ids)) or not set(ids) <= allowed:
        raise WritingError("revision_invalid", "修订句子编号重复或不存在")
    updates = {edit.id: edit for edit in edits.edits}
    revised = []
    for p, paragraph in enumerate(paragraphs, 1):
        sentences = []
        for s, sentence in enumerate(paragraph.sentences, 1):
            edit = updates.get(f"p{p}s{s}")
            if edit is None:
                sentences.append(sentence.model_copy(deep=True))
            elif edit.text.strip():
                sentences.append(Sentence(text=edit.text, evidence=edit.evidence))
        if sentences:
            revised.append(Prose(event_ids=paragraph.event_ids, sentences=sentences))
    validate_page(Page(paragraphs=revised), event_ids, lines)
    return revised


def source_lines(source: str) -> list[dict]:
    return [{"line": i, "text": text} for i, text in enumerate(source.splitlines(), 1) if text.strip()]


def batches(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def source_batches(lines: list[dict], characters: int = 9000) -> list[list[dict]]:
    groups, current, length = [], [], 0
    for line in lines:
        if current and (length + len(line["text"]) > characters or len(current) >= 100):
            groups.append(current)
            current, length = [], 0
        current.append(line)
        length += len(line["text"])
    if current:
        groups.append(current)
    return groups


def validate_evidence(evidence: list[Evidence], lines: list[dict]) -> None:
    indexed = {line["line"]: line["text"] for line in lines}
    for ref in evidence:
        if ref.line not in indexed or ref.quote not in indexed[ref.line]:
            matches = [number for number, text in indexed.items() if ref.quote in text]
            if len(matches) == 1:
                ref.line = matches[0]
                continue
            raise WritingError("evidence_invalid", f"第 {ref.line} 行没有这段精确引用：{ref.quote}。请重新读取该行，并提供连续原文；实际出现在其他行时使用正确行号。")


def validate_catalogue(catalogue: Catalogue, primary: list[dict], lines: list[dict]) -> None:
    ids = [event.id for event in catalogue.events]
    if len(ids) != len(set(ids)):
        raise WritingError("event_duplicate", "事件编号重复")
    for event in catalogue.events:
        validate_evidence(event.evidence, lines)
    covered = set()
    primary_ids = {line["line"] for line in primary}
    referenced = set()
    for row in catalogue.coverage:
        if row.start > row.end or not set(row.event_ids) <= set(ids) or (not row.event_ids and not row.reason.strip()):
            raise WritingError("coverage_invalid", "原文覆盖记录不完整")
        covered.update(range(row.start, row.end + 1))
        referenced.update(row.event_ids)
    if not primary_ids <= covered or set(ids) != referenced:
        raise WritingError("coverage_missing", f"仍有原文或事件未被说明：原文行 {sorted(primary_ids - covered)}；事件 {sorted(set(ids) - referenced)}")


def validate_plan(plan: Plan, events: list[Event]) -> None:
    expected = {event.id for event in events}
    assigned = [event for chapter in plan.chapters for event in chapter.event_ids]
    held = [item.event_id for item in plan.held]
    counts = Counter(assigned + held)
    if set(counts) != expected or any(count != 1 for count in counts.values()):
        raise WritingError("plan_coverage", f"章节规划存在遗漏或重复事件。遗漏：{sorted(expected - set(counts))}；重复：{[key for key, count in counts.items() if count != 1]}；未知编号：{sorted(set(counts) - expected)}。请保留所有真实事件，每个编号恰好分配一次。")
    uncertain = {event.id for event in events if event.certainty == "uncertain"}
    if not uncertain <= set(held):
        raise WritingError("unresolved_event", f"尚未确认的经历不能进入正文，须移入 held：{sorted(uncertain - set(held))}")


def validate_page(page: Page, event_ids: list[str], lines: list[dict]) -> None:
    covered = set()
    for paragraph in page.paragraphs:
        covered.update(paragraph.event_ids)
        for sentence in paragraph.sentences:
            validate_evidence(sentence.evidence, lines)
    if covered != set(event_ids):
        raise WritingError("prose_coverage", f"正文没有完整覆盖本次指定事件。遗漏：{sorted(set(event_ids) - covered)}；多余：{sorted(covered - set(event_ids))}")


def sentence_packet(paragraphs: list[Prose]) -> list[dict]:
    return [{"id": f"p{p + 1}s{s + 1}", **sentence.model_dump()} for p, paragraph in enumerate(paragraphs) for s, sentence in enumerate(paragraph.sentences)]


def validate_checks(review: Review, expected: set[str]) -> None:
    ids = [check.id for check in review.checks]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise WritingError("review_incomplete", f"逐句审查遗漏了句子或重复编号。遗漏：{sorted(expected - set(ids))}；多余：{sorted(set(ids) - expected)}；重复：{[key for key, count in Counter(ids).items() if count > 1]}")


def review_failures(review: Review, paragraphs: list[Prose]) -> list[str]:
    validate_checks(review, {row["id"] for row in sentence_packet(paragraphs)})
    failures = list(review.issues) + [f"未覆盖事件：{event}" for event in review.missing_events]
    failures.extend(f"{check.id}: {check.reason}" for check in review.checks if not all([check.supported, check.readable, check.attribution_correct, check.evidence_complete]))
    return failures


def prompt_text(name: str, shape) -> str:
    return (PROMPTS / name).read_text() + "\n严格遵循此 JSON Schema，所有 required 字段都必须提供。收到 validation_feedback 时逐项修正并重新返回完整结果。\n" + json.dumps(shape.model_json_schema(), ensure_ascii=False)


def request_cache_key(name: str, payload, shape) -> str:
    return hashlib.sha256((prompt_text(name, shape) + json.dumps(payload, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()


def chapter_audit_payload(lines, people, chapter, events, paragraphs):
    return {"source": lines, "people": people, "chapter": chapter.model_dump(), "events": [event.model_dump() for event in events], "paragraphs": [p.model_dump() for p in paragraphs], "sentences": sentence_packet(paragraphs)}


async def generate(complete, source: str, checkpoint, cache_read=None, cache_write=None) -> dict:
    lines = source_lines(source)
    checkpoint("source", {"lines": lines})
    limit = asyncio.Semaphore(4)

    async def ask(prompt_name, payload, shape, label, validate=None):
        prompt = prompt_text(prompt_name, shape)
        key = request_cache_key(prompt_name, payload, shape)
        cacheable = prompt_name in {"events.txt", "chapter_audit.txt"}
        if cacheable and cache_read:
            cached = cache_read(key)
            if cached is not None:
                parsed = shape.model_validate(cached)
                if validate:
                    validate(parsed)
                checkpoint(label, parsed.model_dump())
                return parsed
        for attempt in range(3):
            async with limit:
                raw = await complete(prompt, json.dumps(payload, ensure_ascii=False))
            try:
                parsed = shape.model_validate_json(raw)
                if validate:
                    validate(parsed)
            except (ValidationError, WritingError) as exc:
                checkpoint(label + "-validation", {"attempt": attempt, "response": raw, "error": str(exc)})
                if attempt == 2:
                    raise WritingError("structure_invalid", "模型输出未通过结构或原文覆盖检查，处理记录已保存") from None
                payload = {**payload, "validation_feedback": str(exc), "previous_response": raw}
                prompt += "\n本次输出未通过程序校验，必须修正以下具体问题后返回完整 JSON：\n" + str(exc)
                continue
            checkpoint(label, parsed.model_dump())
            if cacheable and cache_write:
                cache_write(key, parsed.model_dump())
            return parsed

    async def extract(primary):
        payload = {"source": lines, "primary_lines": [line["line"] for line in primary], "focus": primary}
        try:
            result = await ask("events.txt", payload, Catalogue, f"events-{primary[0]['line']}-{primary[-1]['line']}", lambda value: validate_catalogue(value, primary, lines))
        except WritingError as exc:
            if exc.code not in {"output_incomplete", "structure_invalid"}:
                raise
            if len(primary) <= 1:
                raise
            middle = len(primary) // 2
            return [*(await extract(primary[:middle])), *(await extract(primary[middle:]))]
        validate_catalogue(result, primary, lines)
        prefix = f"L{primary[0]['line']}-"
        for event in result.events:
            event.id = prefix + event.id
        for row in result.coverage:
            row.event_ids = [prefix + value for value in row.event_ids]
        return [result]

    catalogue_key = hashlib.sha256(("catalogue-v1" + source + (PROMPTS / "events.txt").read_text() + json.dumps(Catalogue.model_json_schema())).encode()).hexdigest()
    cached_catalogue = cache_read(catalogue_key) if cache_read else None
    if cached_catalogue is not None:
        restored = Catalogue.model_validate(cached_catalogue)
        validate_catalogue(restored, lines, lines)
        catalogues = [restored]
    else:
        catalogues = []
        extracted = await asyncio.gather(*(extract(group) for group in source_batches(lines)), return_exceptions=True)
        for result in extracted:
            if isinstance(result, BaseException):
                raise result
            catalogues.extend(result)
    events = [event for catalogue in catalogues for event in catalogue.events]
    if not events:
        raise WritingError("no_events", "原文中没有可整理的经历")
    checkpoint("catalogue", {"events": [event.model_dump() for event in events], "coverage": [row.model_dump() for catalogue in catalogues for row in catalogue.coverage]})
    if cache_write:
        cache_write(catalogue_key, {"events": [event.model_dump() for event in events], "coverage": [row.model_dump() for catalogue in catalogues for row in catalogue.coverage]})
    progress_key = hashlib.sha256(("writing-progress-v1" + json.dumps([event.model_dump() for event in events], ensure_ascii=False, sort_keys=True)).encode()).hexdigest()
    saved_progress = cache_read(progress_key) if cache_read else None
    progress = WritingProgress.model_validate(saved_progress) if saved_progress else None
    plan = progress.plan if progress else await ask("chapter_plan.txt", {"source": lines, "events": [event.model_dump() for event in events]}, Plan, "plan", lambda value: validate_plan(value, events))
    validate_plan(plan, events)
    checkpoint("plan", plan.model_dump())
    event_map = {event.id: event for event in events}
    pending_facts = progress.uncertain if progress else []
    saved_drafts = progress.drafts if progress else [[] for _ in plan.chapters]
    saved_feedback = progress.feedback if progress and progress.feedback else [[] for _ in plan.chapters]
    if len(saved_drafts) != len(plan.chapters):
        raise WritingError("progress_invalid", "已保存草稿与章节规划数量不一致")
    if len(saved_feedback) != len(plan.chapters):
        raise WritingError("progress_invalid", "已保存审查意见与章节规划数量不一致")
    for fact in pending_facts:
        validate_evidence(fact.evidence, lines)

    def save_progress():
        if cache_write:
            cache_write(progress_key, WritingProgress(plan=plan, drafts=saved_drafts, uncertain=pending_facts, feedback=saved_feedback).model_dump())

    def book_payload(drafts):
        return {
            "source": lines,
            "people": plan.people,
            "uncertain": [fact.model_dump() for fact in pending_facts],
            "held": [{"event": event_map[item.event_id].process, "reason": item.reason} for item in plan.held],
            "manuscript": [{"chapter": index + 1, "title": plan.chapters[index].title, "paragraphs": [{"text": "".join(sentence.text for sentence in paragraph.sentences)} for paragraph in paragraphs]} for index, paragraphs in enumerate(drafts)],
        }

    async def repair_chapter(index, feedback):
        chapter = plan.chapters[index]
        def validate_repair(value):
            counts = Counter(value.event_ids + [item.event_id for item in value.held])
            if set(counts) != set(chapter.event_ids) or any(count != 1 for count in counts.values()):
                raise WritingError("repair_coverage", f"修订必须恰好分配这些事件各一次：{chapter.event_ids}；不能增加或遗漏编号")
            for fact in value.uncertain:
                validate_evidence(fact.evidence, lines)
        repaired = await ask("chapter_repair.txt", {"source": lines, "chapter": chapter.model_dump(), "events": [event_map[key].model_dump() for key in chapter.event_ids], "feedback": feedback}, Repair, f"chapter-{index + 1}-repair", validate_repair)
        chapter.title = repaired.title
        chapter.event_ids = repaired.event_ids
        plan.held.extend(repaired.held)
        pending_facts.extend(repaired.uncertain)
        validate_plan(plan, events)
        save_progress()

    async def write_chapter(index, feedback, previous_draft=None):
        chapter = plan.chapters[index]
        paragraphs = []
        if previous_draft and {key for paragraph in previous_draft for key in paragraph.event_ids} == set(chapter.event_ids):
            payload = {"source": lines, "people": plan.people, "chapter": chapter.model_dump(), "events": [event_map[key].model_dump() for key in chapter.event_ids], "previous_draft": [p.model_dump() for p in previous_draft], "sentences": sentence_packet(previous_draft), "uncertain": [fact.model_dump() for fact in pending_facts], "feedback": feedback}
            edits = await ask("sentence_revision.txt", payload, SentenceEdits, f"chapter-{index + 1}-edits", lambda value: apply_sentence_edits(previous_draft, value, chapter.event_ids, lines))
            revised = apply_sentence_edits(previous_draft, edits, chapter.event_ids, lines)
            checkpoint(f"chapter-{index + 1}-part-1", Page(paragraphs=revised).model_dump())
            return revised

        async def write_group(ids):
            payload = {"source": lines, "people": plan.people, "chapter": chapter.model_dump(), "events": [event_map[key].model_dump() for key in ids], "required_event_ids": ids, "previous_paragraphs": [p.model_dump() for p in paragraphs], "previous_draft": [p.model_dump() for p in previous_draft or []], "previous_chapter": plan.chapters[index - 1].model_dump() if index else None, "uncertain": [fact.model_dump() for fact in pending_facts], "feedback": feedback}
            try:
                page = await ask("chapter_revision.txt" if previous_draft else "chapter.txt", payload, Page, f"chapter-{index + 1}-part-{len(paragraphs) + 1}", lambda value: validate_page(value, ids, lines))
            except WritingError as exc:
                if exc.code not in {"output_incomplete", "structure_invalid"}:
                    raise
                if len(ids) <= 1:
                    raise
                middle = len(ids) // 2
                await write_group(ids[:middle])
                await write_group(ids[middle:])
                return
            validate_page(page, ids, lines)
            paragraphs.extend(page.paragraphs)

        await write_group(chapter.event_ids)
        return paragraphs

    async def checked_chapter(index, initial_feedback=None, previous_draft=None, review_existing=False):
        feedback = list(initial_feedback or [])
        if previous_draft and {key for paragraph in previous_draft for key in paragraph.event_ids} != set(plan.chapters[index].event_ids):
            feedback.append("章节事件安排已更新，请根据当前 event_ids 修订上一稿，保留明确经历并移除已经暂存核对的事件。")
        if feedback:
            await repair_chapter(index, feedback)
        for attempt in range(3):
            if not plan.chapters[index].event_ids:
                saved_drafts[index] = []
                save_progress()
                return []
            if attempt == 0 and review_existing and previous_draft and not feedback:
                paragraphs = previous_draft
                validate_page(Page(paragraphs=paragraphs), plan.chapters[index].event_ids, lines)
                checkpoint(f"chapter-{index + 1}-part-1", Page(paragraphs=paragraphs).model_dump())
            else:
                paragraphs = await write_chapter(index, feedback, previous_draft)
            previous_draft = paragraphs
            saved_drafts[index] = paragraphs
            save_progress()
            async def audit(sentences):
                payload = chapter_audit_payload(lines, plan.people, plan.chapters[index], [event_map[key] for key in plan.chapters[index].event_ids], paragraphs)
                payload["sentences"] = sentences
                try:
                    return await ask("chapter_audit.txt", payload, Review, f"chapter-{index + 1}-audit-{attempt}", lambda value: validate_checks(value, {row["id"] for row in sentences}))
                except OutputIncomplete:
                    if len(sentences) <= 1:
                        raise
                    middle = len(sentences) // 2
                    left, right = await audit(sentences[:middle]), await audit(sentences[middle:])
                    return Review(checks=left.checks + right.checks, missing_events=list(set(left.missing_events + right.missing_events)), issues=list(dict.fromkeys(left.issues + right.issues)))
            review = await audit(sentence_packet(paragraphs))
            feedback = review_failures(review, paragraphs)
            saved_feedback[index] = feedback
            save_progress()
            if not feedback:
                return paragraphs
            if attempt < 2:
                await repair_chapter(index, feedback)
        raise WritingError("chapter_review_failed", f"第 {index + 1} 章仍有待核对内容，已保存审查记录")

    if all(saved_drafts):
        preliminary = await ask("book_audit.txt", book_payload(saved_drafts), BookReview, "book-precheck")
        if sorted(item.chapter for item in preliminary.chapters) != list(range(1, len(plan.chapters) + 1)):
            raise WritingError("book_review_incomplete", "全文预审缺少章节")
        affected = [item for item in preliminary.chapters if not item.accepted or item.issues]
        for item in affected or (preliminary.chapters if preliminary.issues else []):
            saved_feedback[item.chapter - 1].extend(item.issues + preliminary.issues)
        save_progress()
    chapters = await asyncio.gather(*(checked_chapter(index, initial_feedback=saved_feedback[index], previous_draft=saved_drafts[index], review_existing=bool(saved_drafts[index])) for index in range(len(plan.chapters))), return_exceptions=True)
    for result in chapters:
        if isinstance(result, BaseException):
            raise result
    for attempt in range(3):
        retained = [(chapter, paragraphs) for chapter, paragraphs in zip(plan.chapters, chapters) if paragraphs]
        if not retained:
            raise WritingError("no_confirmed_events", "经历尚需核对，已保留原文与疑点")
        plan.chapters = [item[0] for item in retained]
        chapters = [item[1] for item in retained]
        saved_drafts = list(chapters)
        saved_feedback = [[] for _ in chapters]
        save_progress()
        validate_plan(plan, events)
        review = await ask("book_audit.txt", book_payload(chapters), BookReview, f"book-audit-{attempt}")
        ids = [item.chapter for item in review.chapters]
        if sorted(ids) != list(range(1, len(chapters) + 1)):
            raise WritingError("book_review_incomplete", "整书审查缺少章节")
        failed = [item for item in review.chapters if not item.accepted or item.issues]
        if not failed and not review.issues:
            break
        for item in failed or review.chapters:
            saved_feedback[item.chapter - 1] = item.issues + review.issues
        save_progress()
        if attempt == 2:
            raise WritingError("book_review_failed", "全文仍有衔接或事实问题，已保存审查记录")
        targets = failed or review.chapters
        for item in targets:
            chapters[item.chapter - 1] = await checked_chapter(item.chapter - 1, item.issues + review.issues, chapters[item.chapter - 1])
    output = {"chapters": [], "uncertain": [], "memories": []}
    checkpoint("plan", plan.model_dump())
    for chapter_plan, paragraphs in zip(plan.chapters, chapters):
        result = {"title": chapter_plan.title, "paragraphs": []}
        for paragraph in paragraphs:
            refs = list({(ref.line, ref.quote): ref for sentence in paragraph.sentences for ref in sentence.evidence}.values())
            text = "".join(sentence.text for sentence in paragraph.sentences)
            result["paragraphs"].append({"text": text, "source_quote": refs[0].quote, "evidence": [ref.model_dump() for ref in refs], "sentences": [s.model_dump() for s in paragraph.sentences], "event_ids": paragraph.event_ids})
            output["memories"].append({"kind": "event", "name": chapter_plan.title[:80], "detail": text, "source_quote": refs[0].quote})
        output["chapters"].append(result)
    for item in plan.held:
        event = event_map[item.event_id]
        output["uncertain"].append({"topic": event.process[:80] or "待核对经历", "detail": item.reason, "source_quote": event.evidence[0].quote})
    unique_facts = {json.dumps(fact.model_dump(), ensure_ascii=False, sort_keys=True): fact for fact in pending_facts}
    for fact in unique_facts.values():
        output["uncertain"].append({"topic": fact.topic, "detail": fact.detail, "source_quote": fact.evidence[0].quote, "evidence": [ref.model_dump() for ref in fact.evidence]})
    checkpoint("accepted", output)
    return output
