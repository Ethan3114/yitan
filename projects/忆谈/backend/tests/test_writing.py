from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.services.writing_trace import WritingTrace

from app.services.writing import (
    Catalogue, ChapterPlan, Coverage, Event, Evidence, Held, Page, Plan,
    Prose, Review, Sentence, SentenceCheck, SentenceEdit, SentenceEdits, WritingError, apply_sentence_edits, review_failures,
    source_batches, source_lines, validate_catalogue, validate_evidence,
    validate_page, validate_plan,
)

SOURCE = Path(__file__).resolve().parents[2] / "data/real-interviews/li-01-tingwu.txt"


def evidence():
    line = source_lines(SOURCE.read_text())[0]
    return Evidence(line=line["line"], quote=line["text"])


def event(key="e1", certainty="confirmed"):
    return Event(id=key, subject="待验证", when="未知", where="未知", background="", process="", outcome="未知", connection="", evidence=[evidence()], certainty=certainty, doubt="")


def paragraph():
    ref = evidence()
    return Prose(event_ids=["e1"], sentences=[Sentence(text=ref.quote, evidence=[ref])])


def test_batches_preserve_every_original_line():
    lines = source_lines(SOURCE.read_text())
    assert [line for group in source_batches(lines, 1000) for line in group] == lines


def test_evidence_requires_exact_source_position():
    lines = source_lines(SOURCE.read_text())
    validate_evidence([evidence()], lines)
    with pytest.raises(WritingError, match="精确引用"):
        validate_evidence([Evidence(line=999999, quote=evidence().quote + "（非原文）")], lines)


def test_evidence_resolves_unique_exact_quote_to_original_line():
    ref = Evidence(line=999999, quote=evidence().quote)
    validate_evidence([ref], source_lines(SOURCE.read_text()))
    assert ref.line == evidence().line


def test_ambiguous_quote_cannot_supply_missing_location():
    lines = source_lines(SOURCE.read_text())
    assert sum("我们" in line["text"] for line in lines) > 1
    with pytest.raises(WritingError, match="精确引用"):
        validate_evidence([Evidence(line=999999, quote="我们")], lines)


def test_catalogue_rejects_uncovered_source():
    lines = source_lines(SOURCE.read_text())
    value = Catalogue(events=[event()], coverage=[Coverage(start=lines[0]["line"], end=lines[0]["line"], event_ids=["e1"], reason="")])
    with pytest.raises(WritingError, match="未被说明"):
        validate_catalogue(value, lines, lines)


def test_plan_preserves_more_than_twenty_four_events():
    events = [event(f"e{i}") for i in range(40)]
    plan = Plan(people=[], chapters=[ChapterPlan(title="经历", event_ids=[e.id], bridge="") for e in events], held=[])
    validate_plan(plan, events)


def test_uncertain_event_requires_explicit_hold():
    value = event(certainty="uncertain")
    plan = Plan(people=[], chapters=[ChapterPlan(title="经历", event_ids=["e1"], bridge="")], held=[])
    with pytest.raises(WritingError, match="尚未确认"):
        validate_plan(plan, [value])


def test_plan_rejects_missing_events():
    plan = Plan(people=[], chapters=[ChapterPlan(title="经历", event_ids=["e1"], bridge="")], held=[])
    with pytest.raises(WritingError, match="遗漏或重复"):
        validate_plan(plan, [event(), event("e2")])


def test_page_requires_all_assigned_events():
    with pytest.raises(WritingError, match="完整覆盖"):
        validate_page(Page(paragraphs=[paragraph()]), ["e1", "e2"], source_lines(SOURCE.read_text()))


def test_review_cannot_omit_sentences():
    with pytest.raises(WritingError, match="遗漏了句子"):
        review_failures(Review(checks=[], missing_events=[], issues=[]), [paragraph()])


def test_unsupported_sentence_blocks_acceptance():
    check = SentenceCheck(id="p1s1", supported=False, readable=True, attribution_correct=True, evidence_complete=True, reason="缺少事实依据")
    assert review_failures(Review(checks=[check], missing_events=[], issues=[]), [paragraph()]) == ["p1s1: 缺少事实依据"]


def test_encrypted_checkpoint_isolated_by_owner_and_source(tmp_path):
    settings = Settings(data_dir=str(tmp_path), audio_fernet_key=Fernet.generate_key().decode())
    source = SOURCE.read_text()
    first = WritingTrace(settings, "call-one", source, "owner-one")
    value = event().model_dump()
    first.cache_write("event-check", value)
    restored = WritingTrace(settings, "call-two", source, "owner-one")
    assert restored.cache_read("event-check") == value
    assert WritingTrace(settings, "call-three", source, "owner-two").cache_read("event-check") is None
    assert WritingTrace(settings, "call-four", source + "\n", "owner-one").cache_read("event-check") is None
    assert evidence().quote.encode() not in (first.cache_path / "event-check.json.enc").read_bytes()


def test_sentence_revision_preserves_untouched_text_and_evidence():
    lines = source_lines(SOURCE.read_text())
    first = paragraph()
    second = Sentence(text=lines[1]["text"], evidence=[Evidence(line=lines[1]["line"], quote=lines[1]["text"])])
    first.sentences.append(second)
    before = first.model_dump()
    edits = SentenceEdits(edits=[SentenceEdit(id="p1s1", text=lines[2]["text"], evidence=[Evidence(line=lines[2]["line"], quote=lines[2]["text"])])])
    revised = apply_sentence_edits([first], edits, ["e1"], lines)
    assert revised[0].sentences[1].model_dump() == second.model_dump()
    assert first.model_dump() == before
    assert revised[0].sentences[0].text == lines[2]["text"]


def test_sentence_revision_rejects_unknown_sentence():
    with pytest.raises(WritingError, match="编号重复或不存在"):
        apply_sentence_edits([paragraph()], SentenceEdits(edits=[SentenceEdit(id="p2s1", text="", evidence=[])]), ["e1"], source_lines(SOURCE.read_text()))
