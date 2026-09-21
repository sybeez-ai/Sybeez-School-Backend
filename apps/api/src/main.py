from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.router import api_router
from src.core.config import get_settings
from src.core.constants import APP_NAME, APP_VERSION
from src.core.exceptions import AppError
from src.core.logging import setup_logging
from src.core.middleware import RequestIdMiddleware, TimingMiddleware
from src.core.redis import close_redis


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    yield
    await close_redis()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(TimingMiddleware)

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(api_router)
    return app


app = create_app()
