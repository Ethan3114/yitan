"""银行卡号不进文字。录音还在，数字从转写、记忆和章节里拿掉。"""

import re

_CANDIDATE = re.compile(r"(?:\d[ \-]?){13,19}")


def _luhn(number: str) -> bool:
    if not 13 <= len(number) <= 19 or not number.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(number)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def redact_sensitive(text: str) -> str:
    """只删能通过校验的卡号。普通年份和电话短号不动。"""

    def replace(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if _luhn(digits):
            return ""
        return match.group(0)

    cleaned = _CANDIDATE.sub(replace, text or "")
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()
