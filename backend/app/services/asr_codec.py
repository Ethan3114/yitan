"""豆包流式识别用的二进制帧。整数用大端。"""

import gzip
import json
import struct


def _header(message_type: int, flags: int, serialization: int, compression: int) -> bytes:
    return bytes([(1 << 4) | 1, (message_type << 4) | flags, (serialization << 4) | compression, 0])


def encode_full_client(payload: dict) -> bytes:
    body = gzip.compress(json.dumps(payload).encode("utf-8"))
    return _header(0x1, 0x0, 0x1, 0x1) + struct.pack(">I", len(body)) + body


def encode_audio(pcm: bytes, last: bool) -> bytes:
    body = gzip.compress(pcm)
    flags = 0x2 if last else 0x0
    return _header(0x2, flags, 0x0, 0x1) + struct.pack(">I", len(body)) + body


def decode_frame(data: bytes) -> dict:
    if len(data) < 4:
        raise ValueError("short frame")
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    compression = data[2] & 0x0F
    offset = 4
    sequence = None
    if flags & 0x01:
        sequence = struct.unpack(">i", data[offset : offset + 4])[0]
        offset += 4
    if message_type == 0xF:
        code = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        message = data[offset : offset + size].decode("utf-8", errors="replace")
        return {"message_type": "error", "code": code, "message": message, "sequence": sequence}
    size = struct.unpack(">I", data[offset : offset + 4])[0]
    offset += 4
    payload = data[offset : offset + size]
    if compression == 1:
        payload = gzip.decompress(payload)
    parsed: dict | str
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except Exception:
        parsed = ""
    return {"message_type": "response", "flags": flags, "sequence": sequence, "payload": parsed}
