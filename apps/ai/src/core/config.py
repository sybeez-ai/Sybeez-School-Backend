from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ai_host: str = "0.0.0.0"
    ai_port: int = 8001
    openai_api_key: str = ""
    ai_embedding_dim: int = 1536
    ai_service_token: str = "dev-ai-service-token"
    core_api_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
