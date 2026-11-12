"""Core -> AI service call for MCQ draft generation. Core owns the cap and the audit; the AI
service only drafts. The caller's identity is forwarded as a short-lived access token."""

from __future__ import annotations

import httpx

from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.core.exceptions import AppError, UpstreamError
from src.core.security import create_access_token

GENERATE_PATH = "/api/v1/ai/mcq/generate"


async def generate_mcq_drafts(user: CurrentUser, payload: dict) -> dict:
    settings = get_settings()
    token = create_access_token(
        subject=user.id,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        school_id=user.school_id,
        membership_id=user.membership_id,
    )
    url = settings.ai_service_url.rstrip("/") + GENERATE_PATH
    headers = {"Authorization": f"Bearer {token}", "X-Internal-Token": settings.core_internal_token}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise UpstreamError("The AI question generator is not reachable. Try again in a minute.", code="ai_unreachable") from exc
    if resp.status_code == 503:
        raise AppError("AI question generation is not available right now.", code="ai_not_configured", status_code=503)
    if resp.status_code >= 400:
        raise UpstreamError("The AI could not produce usable questions. Try a narrower topic or fewer questions.", code="ai_failed")
    return resp.json()
