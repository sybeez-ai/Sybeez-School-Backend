from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    redis_url: str = "redis://localhost:6379/0"
    worker_concurrency: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
