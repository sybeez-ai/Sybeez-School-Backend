from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    database_url: str = "postgresql+asyncpg://sybeez:sybeez@localhost:5432/sybeez_school"
    database_url_sync: str = "postgresql://sybeez:sybeez@localhost:5432/sybeez_school"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 14

    # AI Teacher service (FE → AI chatbot; Core ↔ AI internal context)
    ai_service_url: str = "http://localhost:8001"
    ai_service_token: str = "dev-internal-service-token"
    # Alias accepted so Core and AI can share one INTERNAL_SERVICE_TOKEN / CORE_INTERNAL_TOKEN
    core_internal_token: str = "dev-internal-service-token"
    embedding_dim: int = 1536

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def internal_service_tokens(self) -> set[str]:
        """Tokens AI may present via X-Internal-Token."""
        return {t for t in (self.ai_service_token, self.core_internal_token) if t}


@lru_cache
def get_settings() -> Settings:
    return Settings()
