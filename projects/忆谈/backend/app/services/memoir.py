"""机审通过后，才留下能给家人读的回忆。出处对不上的句子不进正文。"""

from app.services.grounding import keep_grounded


def grounded_lines(source: str, lines: list[tuple[str, str]]) -> list[tuple[str, str]]:
    kept: list[tuple[str, str]] = []
    for text, quote in lines:
        if keep_grounded(text, quote, source):
            kept.append((text.strip(), quote.strip()))
    return kept


def choose_memoir(
    source: str,
    accepted: bool,
    reviewed: list[tuple[str, str]],
    written: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """没通过机审就没有正文。通过后先用审定稿；审定稿的出处对不上时，退回仍有出处的写手稿。"""
    if not accepted:
        return []
    kept = grounded_lines(source, reviewed)
    if kept:
        return kept[:8]
    return grounded_lines(source, written)[:8]


def reader_fact(topic: str, detail: str) -> tuple[str, str]:
    """家属看到的是人话。内部题名留在库里，不原样拿出来。"""
    if topic == "两路转写不一致":
        return "听写有出入", "两遍听写不完全一样。这段回忆按更完整的那遍来写，可以听原声对一下。"
    if topic == "无法对上原话":
        return "没放进正文", "有一句对不上老人原话，没有放进这段回忆。"
    return topic, detail
