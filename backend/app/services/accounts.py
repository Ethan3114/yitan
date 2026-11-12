import hashlib
import hmac
import secrets
import threading
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Account, AuthAttempt, SessionRow, utcnow
from app.services.store import new_id

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
HASH_LIMIT = threading.BoundedSemaphore(2)
UNKNOWN_HASH = HASHER.hash(secrets.token_urlsafe(32))


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(value: str) -> str:
    with HASH_LIMIT:
        return HASHER.hash(value)


def password_matches(encoded: str, value: str) -> bool:
    with HASH_LIMIT:
        try:
            return HASHER.verify(encoded, value)
        except VerifyMismatchError:
            return False


def admit_attempt(db: Session, pepper: str, address: str, username: str) -> bool:
    now = utcnow()
    keys = [hmac.new(pepper.encode(), text.encode(), hashlib.sha256).hexdigest() for text in ("ip:" + address, "account:" + username)]
    limits = [30, 10]
    for key, limit in zip(keys, limits):
        count = db.scalar(select(func.count()).select_from(AuthAttempt).where(AuthAttempt.subject_hash == key, AuthAttempt.created_at > now - timedelta(minutes=15)))
        if count >= limit:
            return False
    for key in keys:
        db.add(AuthAttempt(id=new_id(), subject_hash=key))
    db.execute(delete(AuthAttempt).where(AuthAttempt.created_at < now - timedelta(days=1)))
    db.commit()
    return True


def create_session(db: Session, account: Account, days: int) -> SessionRow:
    row = SessionRow(id=secrets.token_urlsafe(27), owner_id=account.owner_id, expires_at=utcnow() + timedelta(days=days))
    db.add(row)
    return row
