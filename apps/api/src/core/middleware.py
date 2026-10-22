import logging
import re
import time
import uuid

from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


def _log_app_error(request: Request, exc: BaseException | None, request_id: str | None) -> None:
    """Record the crash in audit_logs so the platform Logs view can show it (never raises)."""
    try:
        from src.core.database import tenant_from_request
        from src.core.event_log import client_ip, record_event_soon

        tenant = tenant_from_request(request)
        name = type(exc).__name__ if exc is not None else "Exception"
        detail = str(exc).splitlines()[0][:300] if exc is not None and str(exc) else ""
        record_event_soon(
            "app.error",
            message=f"{request.method} {request.url.path} failed: {name}{(': ' + detail) if detail else ''}"[:500],
            school_id=tenant.get("school_id") or None,
            actor_id=tenant.get("user_id") or None,
            resource_type="api",
            resource_id=str(request.url.path)[:64],
            ip_address=client_ip(request),
            details={
                "source": "api",
                "method": request.method,
                "path": str(request.url.path),
                "request_id": request_id,
                "error": name,
                "detail": detail,
                "role": tenant.get("role") or None,
                "status": 500,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def internal_error_response(request: Request, exc: BaseException | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception("Unhandled exception", extra={"request_id": request_id, "path": str(request.url.path)})
    _log_app_error(request, exc, request_id)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Internal server error",
                "request_id": request_id,
            }
        },
    )


def database_error_response(request: Request, exc: Exception) -> JSONResponse:
    """A constraint the service did not pre-check: a client-caused conflict, not a server fault."""
    request_id = getattr(request.state, "request_id", None)
    logger.warning(
        "Database rejected a write: %s",
        type(getattr(exc, "orig", exc)).__name__,
        extra={"request_id": request_id, "path": str(request.url.path)},
    )
    if isinstance(exc, IntegrityError):
        status, code, message = 409, "conflict", "This change conflicts with existing data."
    else:
        status, code, message = 422, "invalid_value", "One or more values are out of range or in the wrong format."
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
    )


class UnhandledErrorMiddleware(BaseHTTPMiddleware):
    """Must be registered inside CORSMiddleware.

    Starlette's ``Exception`` handler runs outside every user middleware, so a 500 built
    there carries no CORS headers and browsers surface it as a network failure.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001
            return internal_error_response(request, exc)


_CLIENT_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if _CLIENT_REQUEST_ID.fullmatch(supplied) else str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time"] = f"{(time.perf_counter() - start) * 1000:.2f}ms"
        return response
