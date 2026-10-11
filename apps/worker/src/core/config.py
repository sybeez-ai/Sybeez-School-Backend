from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = ""
    # sybeez_app uses an RDS IAM token. The migrate task does not set this.
    db_iam_auth: bool = Field(
        default=False,
        validation_alias=AliasChoices("DB_IAM_AUTH", "DATABASE_IAM_AUTH"),
    )
    database_host: str = ""
    database_port: int | None = None
    database_user: str = ""
    database_name: str = ""
    database_ssl_mode: str = ""
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
