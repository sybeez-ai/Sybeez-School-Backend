"""Optional BFF: forward authenticated chatbot traffic to the AI Teacher service.

Frontend may call AI directly (NEXT_PUBLIC_AI_API_URL) or via Core:
  POST /api/v1/ai/chat  →  AI POST /api/v1/ai/chat
"""

import httpx
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.core.exceptions import UpstreamError
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/ai", tags=["ai-gateway"])

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


def _forward_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in HOP_BY_HOP:
            continue
        headers[key] = value
    return headers


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_to_ai(
    path: str,
    request: Request,
    _: Annotated[CurrentUser, Depends(require_permission("ai.use"))],
) -> Response:
    settings = get_settings()
    target = f"{settings.ai_service_url.rstrip('/')}/api/v1/ai/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    body = await request.body()
    headers = _forward_headers(request)

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
