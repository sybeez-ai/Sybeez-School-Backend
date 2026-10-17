import logging
import re
import time
import uuid

from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


def internal_error_response(request: Request) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception("Unhandled exception", extra={"request_id": request_id, "path": str(request.url.path)})
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
        except Exception:  # noqa: BLE001
            return internal_error_response(request)


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
