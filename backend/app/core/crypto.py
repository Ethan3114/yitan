"""录音和照片加密后落盘。密钥来自环境变量。"""

from cryptography.fernet import Fernet, InvalidToken


def build_fernet(key: str) -> Fernet:
    raw = key.strip().encode("utf-8")
    return Fernet(raw)


def encrypt_bytes(fernet: Fernet, payload: bytes) -> bytes:
    return fernet.encrypt(payload)


def decrypt_bytes(fernet: Fernet, payload: bytes) -> bytes:
    try:
        return fernet.decrypt(payload)
    except InvalidToken as exc:
        raise ValueError("stored file cannot be decrypted") from exc
