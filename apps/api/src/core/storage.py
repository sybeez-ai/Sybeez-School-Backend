"""Object storage. The database stores the object key, never file bytes.

When S3_BUCKET is set, bytes go to that bucket. In development without a bucket,
bytes are mirrored under apps/api/media so local runs still work, and the stored
value is still the key (for example schools/{id}/payslips/{id}.pdf).
"""

from __future__ import annotations

from pathlib import Path

from src.core.config import get_settings

_MEDIA_ROOT = Path(__file__).resolve().parents[2] / "media"


def put_object(*, key: str, data: bytes, content_type: str | None = None) -> str:
    """Upload bytes and return the object key to persist."""
    cleaned = key.lstrip("/")
    if not cleaned or ".." in cleaned.split("/"):
        raise ValueError("Invalid object key")
    settings = get_settings()
    bucket = (settings.s3_bucket or "").strip()
    if bucket:
        import boto3

        client = boto3.client("s3", region_name=settings.s3_region or settings.aws_region or None)
        client.put_object(
            Bucket=bucket,
            Key=cleaned,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )
        return cleaned
    if (settings.app_env or "").lower() == "production":
        raise RuntimeError("S3_BUCKET is required in production")
    path = _MEDIA_ROOT / cleaned
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return cleaned


def read_object(key: str) -> bytes:
    """Read bytes previously stored with put_object."""
    cleaned = key.lstrip("/")
    if not cleaned or ".." in cleaned.split("/"):
        raise ValueError("Invalid object key")
    settings = get_settings()
    bucket = (settings.s3_bucket or "").strip()
    if bucket:
        import boto3

        client = boto3.client("s3", region_name=settings.s3_region or None)
        try:
            response = client.get_object(Bucket=bucket, Key=cleaned)
        except Exception as exc:  # noqa: BLE001
            raise FileNotFoundError(cleaned) from exc
        return response["Body"].read()
    path = _MEDIA_ROOT / cleaned
    if not path.is_file():
        raise FileNotFoundError(cleaned)
    return path.read_bytes()


def public_url(key: str | None) -> str | None:
    """HTTPS URL when a bucket or public base is configured. Otherwise the key."""
    if not key:
        return None
    if key.startswith(("http://", "https://", "/")):
        return key
    settings = get_settings()
    base = (settings.s3_public_base_url or "").strip()
    if base:
        return f"{base.rstrip('/')}/{key}"
    bucket = (settings.s3_bucket or "").strip()
    if bucket:
        region = settings.s3_region or settings.aws_region or "us-east-1"
        return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    return key
