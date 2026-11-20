"""Core -> AI service for teacher drafts and grading suggestions. Core owns scope, storage and audit."""

from __future__ import annotations

import httpx

from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.core.exceptions import AppError, UpstreamError
from src.core.security import create_access_token


async def call(user: CurrentUser, path: str, payload: dict) -> dict:
    settings = get_settings()
    token = create_access_token(
        subject=user.id,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        school_id=user.school_id,
        membership_id=user.membership_id,
    )
    headers = {"Authorization": f"Bearer {token}", "X-Internal-Token": settings.core_internal_token}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0)) as client:
            resp = await client.post(settings.ai_service_url.rstrip("/") + path, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise UpstreamError("The AI assistant is not reachable. Try again in a minute.", code="ai_unreachable") from exc
    if resp.status_code == 503:
        raise AppError("The AI assistant is not available right now.", code="ai_not_configured", status_code=503)
    if resp.status_code == 429:
        raise AppError("Please wait a moment and try again.", code="rate_limited", status_code=429)
    if resp.status_code >= 400:
        raise UpstreamError("The AI could not produce a usable result. Try again or adjust the input.", code="ai_failed")
    return resp.json()
