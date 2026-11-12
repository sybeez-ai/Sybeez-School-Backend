"""Optional BFF: forward authenticated chatbot traffic to the AI Teacher service.

Frontend may call AI directly (NEXT_PUBLIC_AI_API_URL) or via Core:
  POST /api/v1/ai/chat  →  AI POST /api/v1/ai/chat

The browser authenticates to Core with the httpOnly access cookie. The AI service
requires Authorization: Bearer, so this gateway injects the session token when
proxying (cookie and/or existing Bearer).
"""

import httpx
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.core.exceptions import UpstreamError
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/ai", tags=["ai-gateway"])
_bearer = HTTPBearer(auto_error=False)

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _forward_headers(request: Request, access_token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in HOP_BY_HOP:
            continue
        headers[key] = value
    # AI service auth is Bearer-only; map the school session into Authorization.
    if access_token:
        headers["authorization"] = f"Bearer {access_token}"
    return headers


def _session_access_token(
    request: Request,
    creds: HTTPAuthorizationCredentials | None,
) -> str | None:
    settings = get_settings()
    if creds is not None and creds.credentials:
        return creds.credentials
    cookie = request.cookies.get(settings.access_cookie_name)
    return cookie or None


async def proxy_to_ai(
    path: str,
    request: Request,
    _: Annotated[CurrentUser, Depends(require_permission("ai.use"))],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Response:
    settings = get_settings()
    target = f"{settings.ai_service_url.rstrip('/')}/api/v1/ai/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    body = await request.body()
    headers = _forward_headers(request, _session_access_token(request, creds))

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
            upstream = await client.request(
                method=request.method,
                url=target,
                headers=headers,
                content=body,
            )
    except httpx.HTTPError as exc:
        raise UpstreamError(f"AI service unreachable: {exc}") from exc

    content_type = upstream.headers.get("content-type", "")
    passthrough = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() != "content-length"
    }

    if "text/event-stream" in content_type:

        async def stream():
            yield upstream.content

        return StreamingResponse(
            stream(),
            status_code=upstream.status_code,
            media_type="text/event-stream",
            headers=passthrough,
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=content_type or None,
        headers=passthrough,
    )


# One route per method so each has its own OpenAPI operationId (typed clients need unique ids).
for _method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
    router.add_api_route(
        "/{path:path}", proxy_to_ai, methods=[_method], operation_id=f"proxy_to_ai_{_method.lower()}"
    )
