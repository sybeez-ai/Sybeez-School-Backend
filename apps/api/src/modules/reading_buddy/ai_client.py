"""Core -> AI service: mint a Gemini Live token locked to one passage (voice only)."""

from __future__ import annotations

import httpx

from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.core.exceptions import AppError, UpstreamError
from src.core.security import create_access_token

PATH = "/api/v1/ai/reading/gemini/session"


async def mint_reading_voice(user: CurrentUser, *, passage: str, class_level: int) -> dict:
    settings = get_settings()
    token = create_access_token(
        subject=user.id,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        school_id=user.school_id,
        membership_id=user.membership_id,
    )
    headers = {"Authorization": f"Bearer {token}", "X-Internal-Token": settings.core_internal_token}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            resp = await client.post(
                settings.ai_service_url.rstrip("/") + PATH,
                json={"passage": passage, "class_level": class_level},
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise UpstreamError(
            "The AI teacher cannot listen right now. Please try again soon.", code="voice_unreachable"
        ) from exc
    if resp.status_code in (429,):
        raise AppError("Please wait a moment and try again.", code="rate_limited", status_code=429)
    if resp.status_code >= 400:
        raise AppError(
            "The AI teacher cannot listen right now. Please try again soon.", code="voice_unavailable", status_code=503
        )
    return resp.json()
