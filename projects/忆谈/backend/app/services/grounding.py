"""只保留能在老人原话里找到出处的句子。出处必须落在同一轮话里。"""

import re

_TURN_LINE = re.compile(r"^\[(\d{2}):(\d{2}) 发言人(\d+)\]\s*(.*)$")


def spoken_turns(source: str) -> list[str]:
    """有说话人标签时，一轮是一行里的说话内容。没有标签时，整段算一轮。"""
    labeled = []
    for line in source.splitlines():
        match = _TURN_LINE.match(line.strip())
        if match and match.group(4).strip():
            labeled.append(match.group(4).strip())
    if labeled:
        return labeled
    text = source.strip()
    return [text] if text else []


def excerpt_around(source: str, quote: str) -> str:
    """取出出处所在的那一轮，以及随后一分钟里的话，用来补口头说法。"""
    rows: list[tuple[int, str]] = []
    for line in source.splitlines():
        match = _TURN_LINE.match(line.strip())
        if match and match.group(4).strip():
            rows.append((int(match.group(1)) * 60 + int(match.group(2)), match.group(4).strip()))
    origin = quote.strip()
    if not rows:
        return origin
    for index, (at, text) in enumerate(rows):
        if origin not in text:
            continue
        parts: list[str] = []
        for later_at, later_text in rows[index:]:
            if later_at - at > 60:
                break
            parts.append(later_text)
            if len(parts) >= 6:
                break
        return "\n".join(parts)
    return ""


def elder_text(texts: dict[str, str]) -> str:
    """写书优先用听悟的全量稿。没有时才用挂断后的旁路，再退回通话当时的听写。"""
    full = (texts.get("full") or "").strip()
    side = (texts.get("side") or "").strip()
    spoken = (texts.get("realtime_user") or "").strip()
    return full or side or spoken


def transcripts_disagree(texts: dict[str, str]) -> bool:
    side = (texts.get("side") or "").strip()
    spoken = (texts.get("realtime_user") or "").strip()
    return bool(side and spoken and side != spoken)


def keep_grounded(text: str, quote: str, source: str) -> bool:
    body = text.strip()
    origin = quote.strip()
    if not body or len(origin) < 2:
        return False
    return any(origin in turn for turn in spoken_turns(source))
