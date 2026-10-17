import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import DataError, IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.router import api_router
from src.core.config import get_settings
from src.core.csrf import csrf_response
from src.core.database import AsyncSessionLocal
from src.core.constants import APP_NAME, APP_VERSION
from src.core.exceptions import AppError, http_error_body, validation_error_body
from src.core.logging import setup_logging
from src.core.middleware import (
    RequestIdMiddleware,
    TimingMiddleware,
    UnhandledErrorMiddleware,
    database_error_response,
    internal_error_response,
)
from src.core.redis import close_redis
from src.modules.platform.bootstrap import bootstrap_superadmin

logger = logging.getLogger(__name__)


async def _bootstrap_superadmin() -> None:
    """Create the platform admin without blocking the process from listening."""
    try:
        async with AsyncSessionLocal() as session:
            try:
                await bootstrap_superadmin(session)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception:
        # A bootstrap or database failure must not keep the process from
        # listening. The load balancer only needs /health to succeed.
        logger.exception("Super admin bootstrap failed; continuing startup")


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    bootstrap = asyncio.create_task(_bootstrap_superadmin())
    yield
    if not bootstrap.done():
        bootstrap.cancel()
    try:
        await bootstrap
    except asyncio.CancelledError:
        pass
    await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

    # Dev/mobile WebView often loads the Next app via LAN IP (not localhost).
    # Without matching CORS, browser/WebView login fetch is blocked.
    lan_origin_regex = (
        r"https?://("
        r"localhost|127\.0\.0\.1|"
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3}|"
        r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
        r")(:\d+)?"
        if settings.app_env == "development"
        else None
    )
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=lan_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "X-Request-ID", "X-Total-Count"],
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(TimingMiddleware)

    @app.middleware("http")
    async def csrf_middleware(request: Request, call_next):
        blocked = csrf_response(request)
        if blocked is not None:
            return blocked
        return await call_next(request)

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content=validation_error_body(list(exc.errors())))

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=http_error_body(exc.status_code, exc.detail),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(IntegrityError)
    @app.exception_handler(DataError)
    async def database_error_handler(request: Request, exc: Exception):
        return database_error_response(request, exc)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        return internal_error_response(request)

    media_dir = Path(__file__).resolve().parent.parent / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")

    app.include_router(api_router)
    return app


app = create_app()
