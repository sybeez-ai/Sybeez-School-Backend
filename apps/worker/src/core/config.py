from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    redis_url: str = "redis://localhost:6379/0"
    worker_concurrency: int = 2
    core_api_url: str = "http://localhost:8000"
    core_internal_token: str = "dev-internal-service-token"
    aws_region: str = "ap-south-1"
    # redis (default) or sqs. SQS uses SQS_QUEUE_URL.
    jobs_backend: str = "redis"
    sqs_queue_url: str = ""

    sms_provider: str = "mock"  # mock | msg91
    msg91_auth_key: str = ""
    msg91_sender_id: str = ""
    msg91_template_id: str = ""

    whatsapp_provider: str = "mock"  # mock | meta
    whatsapp_cloud_access_token: str = ""
    whatsapp_cloud_phone_number_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
