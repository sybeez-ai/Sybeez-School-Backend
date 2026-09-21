from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.router import api_router
from src.core.logging import setup_logging


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="Sybeez AI Teacher", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/ai/v1")
    return app


app = create_app()
