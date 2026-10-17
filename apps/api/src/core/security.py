from datetime import timedelta
from typing import Any
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from src.core.config import get_settings
from src.shared.utils import utcnow

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(
    *,
    subject: str | UUID,
    role: str,
    school_id: str | UUID | None = None,
    membership_id: str | UUID | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    now = utcnow()
    payload: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    }
    if school_id is not None:
        payload["school_id"] = str(school_id)
    if membership_id is not None:
        payload["membership_id"] = str(membership_id)
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token_claims(
    *,
    subject: str | UUID,
    jti: str,
    expires_at,
    role: str | None = None,
    school_id: str | UUID | None = None,
    membership_id: str | UUID | None = None,
) -> dict[str, Any]:
    now = utcnow()
    claims: dict[str, Any] = {
        "sub": str(subject),
        "type": "refresh",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if role is not None:
        claims["role"] = role
    if school_id is not None:
        claims["school_id"] = str(school_id)
    if membership_id is not None:
        claims["membership_id"] = str(membership_id)
    return claims


def encode_token(claims: dict[str, Any]) -> str:
    settings = get_settings()
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("Invalid token") from exc
