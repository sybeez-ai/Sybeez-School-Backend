"""CSRF checks for cookie-authenticated unsafe requests.

Bearer clients skip the check. Cookie clients must either echo the double-submit
token or send an Origin/Referer whose host is the request host or a configured
CORS origin. The web app is same-origin with /api through CloudFront.
"""

from urllib.parse import urlparse

from fastapi import Request
from starlette.responses import JSONResponse

from src.core.config import get_settings

_SAFE = {"GET", "HEAD", "OPTIONS", "TRACE"}
_ENTRY = {
    "/api/v1/auth/login",
    "/api/v1/auth/complete-new-password",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/confirm-forgot-password",
    "/api/v1/auth/accept-invitation",
    "/api/v1/auth/accept-staff-invitation",
    "/api/v1/auth/cognito/callback",
    "/api/v1/auth/cognito/google/start",
    "/api/v1/auth/cognito/result",
    "/api/v1/webhooks/whatsapp/meta",
    "/api/v1/webhooks/sms/msg91",
}


def _host(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    return (parsed.hostname or "").lower()


def _allowed_hosts(request: Request) -> set[str]:
    settings = get_settings()
    hosts = {_host(origin) for origin in settings.cors_origins}
    hosts.add((request.url.hostname or "").lower())
    hosts.discard("")
    return hosts


def cookie_credential(request: Request) -> bool:
    """True when the caller is authenticating with a cookie and not a bearer token."""
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer ") and header.split(" ", 1)[1].strip():
        return False
    settings = get_settings()
    if request.cookies.get(settings.access_cookie_name):
        return True
    path = request.url.path.rstrip("/") or "/"
    if path in {"/api/v1/auth/refresh", "/api/v1/auth/logout"}:
        return bool(request.cookies.get(settings.refresh_cookie_name))
    return False


def csrf_ok(request: Request) -> bool:
    settings = get_settings()
    cookie = request.cookies.get(settings.csrf_cookie_name) or ""
    header = request.headers.get(settings.csrf_header_name) or ""
    if cookie and header and header == cookie:
        return True
    origin = _host(request.headers.get("origin"))
    referer = _host(request.headers.get("referer"))
    allowed = _allowed_hosts(request)
    return bool(origin and origin in allowed) or bool(referer and referer in allowed)


def csrf_response(request: Request) -> JSONResponse | None:
    if request.method.upper() in _SAFE:
        return None
    path = request.url.path.rstrip("/") or "/"
    if path in _ENTRY:
        return None
    if not cookie_credential(request):
        return None
    if csrf_ok(request):
        return None
    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "code": "csrf",
                "message": "CSRF check failed. Send X-CSRF-Token or a same-origin Origin.",
            }
        },
    )
