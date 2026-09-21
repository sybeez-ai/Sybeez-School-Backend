from datetime import UTC, datetime
from secrets import token_urlsafe


def utcnow() -> datetime:
    return datetime.now(UTC)


def generate_token(nbytes: int = 32) -> str:
    return token_urlsafe(nbytes)
