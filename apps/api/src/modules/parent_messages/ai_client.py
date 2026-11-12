"""Core -> AI service call for notice translation (Qwen). Core owns caching, review and audit."""

from __future__ import annotations

import httpx

from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.core.exceptions import AppError, UpstreamError
from src.core.security import create_access_token

TRANSLATE_PATH = "/api/v1/ai/translate/notice"


async def translate_notice(user: CurrentUser, *, title: str, body: str, languages: list[str]) -> dict:
    settings = get_settings()
    token = create_access_token(
        subject=user.id,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        school_id=user.school_id,
        membership_id=user.membership_id,
    )
    url = settings.ai_service_url.rstrip("/") + TRANSLATE_PATH
    headers = {"Authorization": f"Bearer {token}", "X-Internal-Token": settings.core_internal_token}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0)) as client:
            resp = await client.post(url, json={"title": title, "body": body, "languages": languages}, headers=headers)
    except httpx.HTTPError as exc:
        raise UpstreamError("Translation is not reachable right now.", code="ai_unreachable") from exc
    if resp.status_code == 503:
        raise AppError("Translation is not available right now.", code="ai_not_configured", status_code=503)
    if resp.status_code >= 400:
        raise UpstreamError("The translation could not be produced.", code="ai_failed")
    return resp.json()
