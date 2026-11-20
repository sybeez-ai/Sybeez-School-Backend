"""Virus scanning for uploads, behind VIRUS_SCAN_PROVIDER (default "none" = off).

"clamav" talks to a clamd daemon over TCP (INSTREAM), so any small ClamAV container works (ECS task, sidecar or
a dev machine). No extra Python dependency. Infected files are refused with a 422 and the event is logged.
"""

from __future__ import annotations

import logging
import socket
import struct

from src.core.config import get_settings
from src.core.exceptions import AppError, ValidationError

logger = logging.getLogger(__name__)
_CHUNK = 64 * 1024


class ScannerUnavailableError(AppError):
    def __init__(self) -> None:
        super().__init__("Uploads are temporarily unavailable. Please try again shortly.", code="scan_unavailable", status_code=503)


def parse_clamd_reply(reply: str) -> tuple[bool, str | None]:
    """(clean, signature) from a clamd reply such as 'stream: OK' or 'stream: Eicar-Test-Signature FOUND'."""
    text = reply.strip().strip("\x00")
    if text.endswith("OK"):
        return True, None
    if text.endswith("FOUND"):
        body = text.split(":", 1)[-1].strip()
        return False, body[: -len("FOUND")].strip() or "unknown"
    raise ValueError(f"unexpected clamd reply: {text[:80]}")


def _clamd_scan(data: bytes, host: str, port: int, timeout: int) -> tuple[bool, str | None]:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(b"zINSTREAM\x00")
        for i in range(0, len(data), _CHUNK):
            chunk = data[i : i + _CHUNK]
            s.sendall(struct.pack("!I", len(chunk)) + chunk)
        s.sendall(struct.pack("!I", 0))
        reply = b""
        while not reply.endswith(b"\x00") and len(reply) < 1024:
            part = s.recv(1024)
            if not part:
                break
            reply += part
    return parse_clamd_reply(reply.decode("utf-8", "replace"))


def scan_bytes(data: bytes, *, name: str = "upload") -> None:
    """Raise if the bytes are infected (or, when fail-closed, if scanning could not run). No-op when off."""
    settings = get_settings()
    provider = (settings.virus_scan_provider or "none").strip().lower()
    if provider in ("", "none", "off"):
        return
    if provider != "clamav" or not settings.clamav_host:
        logger.error("Virus scanning misconfigured (provider=%s)", provider)
        if settings.virus_scan_fail_closed:
            raise ScannerUnavailableError()
        return
    try:
        clean, signature = _clamd_scan(data, settings.clamav_host, settings.clamav_port, settings.clamav_timeout_seconds)
    except (OSError, ValueError):
        logger.error("Virus scanner unreachable while scanning %s", name, exc_info=True)
        if settings.virus_scan_fail_closed:
            raise ScannerUnavailableError() from None
        return
    if not clean:
        logger.warning("Infected upload refused: %s (%s)", name, signature)
        raise ValidationError("This file was blocked because it may contain a virus.", code="virus_detected")
