"""Private storage for HR files: local disk or S3, chosen by ``HR_FILES_S3_BUCKET``.

Bytes never sit under ``media/`` (served publicly by the static mount). Downloads go through
short-lived links: an S3 presigned URL, or for local disk a signed token that the API exchanges
for exactly one file before it expires.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

from src.core.config import get_settings
from src.core.security import decode_token, encode_token
from src.shared.utils import utcnow

logger = logging.getLogger(__name__)

STORAGE_ROOT = Path(__file__).resolve().parents[3] / "private_media" / "hr-files"
TOKEN_TYPE = "hr_file"


class FileBackend(Protocol):
    name: str

    def put(self, key: str, data: bytes, content_type: str | None) -> None: ...
    def exists(self, key: str) -> bool: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def presigned_url(self, key: str, filename: str, content_type: str | None, ttl: int) -> str | None: ...


def _resolve(key: str) -> Path:
    root = STORAGE_ROOT.resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise ValueError("Invalid storage key")
    return path


class LocalBackend:
    name = "local"

    def put(self, key: str, data: bytes, content_type: str | None) -> None:
        path = _resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def exists(self, key: str) -> bool:
        try:
            return _resolve(key).is_file()
        except ValueError:
            return False

    def read(self, key: str) -> bytes:
        return _resolve(key).read_bytes()

    def delete(self, key: str) -> None:
        try:
            _resolve(key).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    def presigned_url(self, key: str, filename: str, content_type: str | None, ttl: int) -> str | None:
        return None


class S3Backend:
    name = "s3"

    def __init__(self, bucket: str, region: str, prefix: str):
        import boto3

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client("s3", region_name=region or None)

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes, content_type: str | None) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._k(key),
            Body=data,
            ContentType=content_type or "application/octet-stream",
            ServerSideEncryption="AES256",
        )

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except ClientError:
            return False

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()

    def delete(self, key: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._k(key))
        except (BotoCoreError, ClientError):
            logger.warning("Could not delete HR file object %s", key, exc_info=True)

    def presigned_url(self, key: str, filename: str, content_type: str | None, ttl: int) -> str | None:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": self._k(key),
                "ResponseContentDisposition": f"attachment; filename*=UTF-8''{quote(filename)}",
                "ResponseContentType": content_type or "application/octet-stream",
                "ResponseCacheControl": "private, no-store",
            },
            ExpiresIn=ttl,
        )


@lru_cache
def _s3(bucket: str, region: str, prefix: str) -> S3Backend:
    return S3Backend(bucket, region, prefix)


def backend() -> FileBackend:
    """S3 when a bucket is configured. Local disk is development only.

    HR_FILES_S3_BUCKET wins. Otherwise the existing S3_BUCKET is used so HR files
    share the school's object storage. Production startup already refuses an empty
    HR_FILES_S3_BUCKET; this also refuses a local-disk fallback if that check was skipped.
    """
    s = get_settings()
    bucket = (s.hr_files_s3_bucket or "").strip() or (s.s3_bucket or "").strip()
    if bucket:
        region = (s.hr_files_s3_region or s.s3_region or s.aws_region or "").strip()
        return _s3(bucket, region, s.hr_files_s3_prefix)
    if (s.app_env or "").lower() == "production":
        raise RuntimeError("HR_FILES_S3_BUCKET is required in production")
    return LocalBackend()


def save(school_id: UUID, data: bytes, ext: str, content_type: str | None = None) -> str:
    key = f"{school_id}/{uuid4().hex}{ext}"
    backend().put(key, data, content_type)
    return key


def exists(key: str) -> bool:
    return backend().exists(key)


def read(key: str) -> bytes:
    return backend().read(key)


def path_for(key: str) -> Path:
    """Local-disk path of a key (local backend only)."""
    return _resolve(key)


def remove(key: str) -> None:
    backend().delete(key)


def link_ttl() -> int:
    return get_settings().hr_files_link_ttl_seconds


def presigned_url(key: str, filename: str, content_type: str | None) -> str | None:
    return backend().presigned_url(key, filename, content_type, link_ttl())


def issue_download_token(*, doc_id: UUID, school_id: UUID, user_id: UUID, as_admin: bool) -> str:
    """Signed, short-lived token that unlocks one document (used when there is no S3)."""
    now = utcnow()
    return encode_token(
        {
            "type": TOKEN_TYPE,
            "doc": str(doc_id),
            "school_id": str(school_id),
            "sub": str(user_id),
            "adm": as_admin,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=link_ttl())).timestamp()),
        }
    )


def read_download_token(token: str) -> dict:
    claims = decode_token(token)
    if claims.get("type") != TOKEN_TYPE or not claims.get("doc") or not claims.get("school_id"):
        raise ValueError("Invalid token")
    return claims
