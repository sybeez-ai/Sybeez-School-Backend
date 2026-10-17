import json
import os
from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _secret_map() -> dict[str, str]:
    arn = (os.environ.get("ALERTS_SECRETS_ARN") or os.environ.get("WHATSAPP_SECRETS_ARN") or "").strip()
    if not arn:
        return {}
    try:
        import boto3

        raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): "" if value is None else str(value) for key, value in data.items()}


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


    whatsapp_provider: str = "mock"  # mock | meta_cloud | gupshup | twilio
    whatsapp_cloud_access_token: str = Field(
        default="",
        validation_alias=AliasChoices("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_CLOUD_ACCESS_TOKEN"),
    )
    whatsapp_cloud_phone_number_id: str = Field(
        default="",
        validation_alias=AliasChoices("WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_CLOUD_PHONE_NUMBER_ID"),
    )
    whatsapp_business_account_id: str = Field(
        default="",
        validation_alias=AliasChoices("WHATSAPP_BUSINESS_ACCOUNT_ID"),
    )
    whatsapp_app_secret: str = Field(default="", validation_alias=AliasChoices("WHATSAPP_APP_SECRET"))
    whatsapp_verify_token: str = Field(default="", validation_alias=AliasChoices("WHATSAPP_VERIFY_TOKEN"))
    whatsapp_rate_limit_per_minute: int = 60

    @model_validator(mode="after")
    def _overlay_secrets(self) -> "Settings":
        secrets = _secret_map()
        mapping = {
            "whatsapp_cloud_access_token": ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_CLOUD_ACCESS_TOKEN"),
            "whatsapp_cloud_phone_number_id": ("WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_CLOUD_PHONE_NUMBER_ID"),
            "whatsapp_business_account_id": ("WHATSAPP_BUSINESS_ACCOUNT_ID",),
            "whatsapp_app_secret": ("WHATSAPP_APP_SECRET",),
            "whatsapp_verify_token": ("WHATSAPP_VERIFY_TOKEN",),
        }
        for field, keys in mapping.items():
            if getattr(self, field):
                continue
            for key in keys:
                if secrets.get(key):
                    setattr(self, field, secrets[key])
                    break
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
