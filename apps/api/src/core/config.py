from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULTS: set[str] = {
    "",
    "change-me-in-production",
    "change-me-in-production-use-long-random-string",
    "dev-internal-service-token",
    "dev-only-change-me",
}


def _is_insecure_secret(value: str) -> bool:
    value = (value or "").strip()
    if value in _INSECURE_DEFAULTS:
        return True
    # Keep this simple: anything short is too easy to guess / leak.
    return len(value) < 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    database_url: str = "postgresql+asyncpg://sybeez:sybeez@localhost:5432/sybeez_school"
    database_url_sync: str = "postgresql://sybeez:sybeez@localhost:5432/sybeez_school"
    # sybeez_app has rds_iam, so a password is rejected. DB_IAM_AUTH mints a token.
    database_iam_auth: bool = Field(
        default=False,
        validation_alias=AliasChoices("DB_IAM_AUTH", "DATABASE_IAM_AUTH"),
    )
    # Empty values are taken from DATABASE_URL. Set them to override the URL.
    database_host: str = ""
    database_port: int | None = None
    database_name: str = ""
    database_user: str = ""
    database_ssl_mode: str = "disable"
    aws_region: str = "us-east-1"
    redis_url: str = "redis://localhost:6379/0"
    # Job queue. redis is the default. sqs uses SQS_QUEUE_URL (API enqueue and the worker).
    jobs_backend: str = "redis"
    sqs_queue_url: str = ""

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    refresh_token_expire_days: int = 14
    # Per-role refresh lifetime in hours. Daily roles are an absolute cap.
    # Teacher, student, and parent default to 30 days (720h) and rotate.
    session_ttl_hours: str = (
        "PLATFORM_ADMIN:24,PLATFORM_STAFF:24,SCHOOL_ADMIN:24,FINANCE:24,HR:24,"
        "HR_MANAGER:24,ACCOUNTANT:24,CASHIER:24,AUDITOR:24,PAYROLL_ADMIN:24,"
        "PAYROLL_APPROVER:24,TEACHER:720,STUDENT:720,PARENT:720,STAFF:720"
    )

    superadmin_email: str = "admin@sybeez.ai"
    superadmin_secret_id: str = "sybeez-school/superadmin"
    auth_provider: str = "local"  # local | cognito
    cognito_client_id: str = ""

    cookie_secure: str = "auto"  # auto | true | false
    cookie_samesite: str = "lax"
    cookie_domain: str = ""
    access_cookie_name: str = "sybeez_access"
    refresh_cookie_name: str = "sybeez_refresh"
    csrf_cookie_name: str = "sybeez_csrf"
    csrf_header_name: str = "X-CSRF-Token"

    seed_demo_data: bool = False

    # Auth: local | cognito | both (Cognito password check, then Core JWT)
    auth_mode: str = "both"
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""
    # Confidential client secret (optional; only if using secret client)
    cognito_app_client_secret: str = ""
    # Cognito Hosted UI domain, e.g. "https://example.auth.us-east-1.amazoncognito.com"
    cognito_domain: str = ""
    # Where Cognito Hosted UI redirects back (must be allowlisted on app client)
    cognito_oauth_redirect_uri: str = "http://localhost:8000/api/v1/auth/cognito/callback"
    # Frontend base URL for final redirect after OAuth completes
    web_app_url: str = "http://localhost:3000"

    # AI Teacher service (FE → AI chatbot; Core ↔ AI internal context)
    ai_service_url: str = "http://localhost:8001"
    ai_service_token: str = "dev-internal-service-token"
    # Alias accepted so Core and AI can share one INTERNAL_SERVICE_TOKEN / CORE_INTERNAL_TOKEN
    core_internal_token: str = "dev-internal-service-token"
    embedding_dim: int = 1536

    # Outbound communication providers (Phase 12).
    # Production WhatsApp uses meta_cloud. mock stays the unset default so dev
    # does not call Meta. Secrets may also arrive from Secrets Manager.

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

    # Object storage. Empty bucket keeps files on local disk outside production.
    # Set the bucket name from the environment or from Secrets Manager. Never commit a key.
    s3_bucket: str = ""
    s3_region: str = ""
    s3_public_base_url: str = ""
    # Virus scanning of every stored upload. "none" (default, off) or "clamav" (clamd reachable at host:port).
    virus_scan_provider: str = "none"
    clamav_host: str = ""
    clamav_port: int = 3310
    clamav_timeout_seconds: int = 20
    # When scanning is on and the scanner is unreachable: refuse the upload (true) or store it unscanned (false).
    virus_scan_fail_closed: bool = True
    payroll_rate_limit_per_minute: int = 120
    # Payroll assistant. local answers from the payroll tools without an outbound model.
    # openai uses an OpenAI-compatible chat API. ai_service reuses AI_SERVICE_URL.
    # PAYROLL_ASSISTANT_SECRET_ID may hold {"provider","api_key","model","base_url"} in Secrets Manager.
    payroll_assistant_provider: str = "local"
    payroll_assistant_model: str = "gpt-4o-mini"
    payroll_assistant_api_key: str = ""
    payroll_assistant_base_url: str = "https://api.openai.com/v1"
    payroll_assistant_secret_id: str = ""
    payroll_assistant_rate_limit_per_minute: int = 20
    # Private HR files. Production refuses to start when this is empty (no local-disk fallback).
    # Region falls back to the existing S3_REGION / AWS_REGION settings.
    hr_files_s3_bucket: str = ""
    hr_files_s3_region: str = ""
    hr_files_s3_prefix: str = "hr-files"
    hr_files_link_ttl_seconds: int = Field(default=120, ge=10, le=3600)

    @model_validator(mode="after")
    def _overlay_secrets(self) -> "Settings":
        # Imported here so secrets.py can import get_settings without a cycle.
        from src.core.secrets import load_secret_map

        secrets = load_secret_map()
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

    def validate_runtime(self) -> None:
        """Fail closed in production for unsafe defaults."""
        if (self.app_env or "").lower() != "production":
            return

        problems: list[str] = []

        if _is_insecure_secret(self.jwt_secret):
            problems.append("JWT_SECRET")
        if _is_insecure_secret(self.ai_service_token):
            problems.append("AI_SERVICE_TOKEN")
        if _is_insecure_secret(self.core_internal_token):
            problems.append("CORE_INTERNAL_TOKEN")
        if (self.database_ssl_mode or "").lower() != "require" and not self.database_iam_auth:
            problems.append("DATABASE_SSL_MODE=require (or DB_IAM_AUTH=true)")
        if self.database_iam_auth:
            from src.core.rds_iam import resolve_endpoint

            try:
                resolve_endpoint(
                    database_url=self.database_url,
                    database_host=self.database_host,
                    database_port=self.database_port,
                    database_user=self.database_user,
                    database_name=self.database_name,
                    aws_region=self.aws_region,
                )
            except ValueError as exc:
                problems.append(str(exc))
        if not (self.s3_bucket or "").strip():
            problems.append("S3_BUCKET")
        backend = (self.jobs_backend or "redis").strip().lower()
        if backend not in {"redis", "sqs"}:
            problems.append("JOBS_BACKEND must be redis or sqs")
        if backend == "sqs" and not (self.sqs_queue_url or "").strip():
            problems.append("SQS_QUEUE_URL")
        assistant = (self.payroll_assistant_provider or "local").lower()
        if assistant == "openai" and not (
            (self.payroll_assistant_api_key or "").strip() or (self.payroll_assistant_secret_id or "").strip()
        ):
            problems.append("PAYROLL_ASSISTANT_API_KEY or PAYROLL_ASSISTANT_SECRET_ID")

        # School session tokens are HS256. Cognito is optional unless the task
        # asks for it. The default auth mode is "both", and the region default
        # is us-east-1, so neither of those means a pool was configured.
        # Requiring the pool here exits before uvicorn binds, and the load
        # balancer then has nothing to route to.
        if self._cognito_required():
            missing: list[str] = []
            if not (self.cognito_region or "").strip():
                missing.append("COGNITO_REGION")
            if not (self.cognito_user_pool_id or "").strip():
                missing.append("COGNITO_USER_POOL_ID")
            if not (self.cognito_app_client_id or "").strip():
                missing.append("COGNITO_APP_CLIENT_ID")
            # COGNITO_DOMAIN is only the Hosted UI host. Password login does not
            # need it, and those routes already return a conflict when it is blank.
            if not (self.cognito_oauth_redirect_uri or "").strip():
                missing.append("COGNITO_OAUTH_REDIRECT_URI")
            if not (self.web_app_url or "").strip():
                missing.append("WEB_APP_URL")
            if missing:
                problems.append(f"Missing Cognito settings: {', '.join(missing)}")

        # HR files use HR_FILES_S3_BUCKET when it is set, and the school S3_BUCKET
        # otherwise. S3_BUCKET is already required above, so a second bucket is optional.
        if not (self.hr_files_s3_bucket or "").strip() and not (self.s3_bucket or "").strip():
            problems.append("HR_FILES_S3_BUCKET")

        if problems:
            raise RuntimeError(
                "Unsafe production configuration. Fix environment variables: "
                + "; ".join(problems)
            )

    def _cognito_required(self) -> bool:
        mode = (self.auth_mode or "").lower().strip()
        if mode == "cognito":
            return True
        return bool(
            (self.cognito_user_pool_id or "").strip()
            or (self.cognito_app_client_id or "").strip()
            or (self.cognito_domain or "").strip()
        )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def session_ttl_by_role(self) -> dict[str, int]:
        parsed: dict[str, int] = {}
        for piece in (self.session_ttl_hours or "").split(","):
            item = piece.strip()
            if not item or ":" not in item:
                continue
            role, hours = item.split(":", 1)
            try:
                parsed[role.strip()] = int(hours.strip())
            except ValueError:
                continue
        return parsed

    @property
    def use_secure_cookies(self) -> bool:
        flag = (self.cookie_secure or "auto").strip().lower()
        if flag in {"1", "true", "yes"}:
            return True
        if flag in {"0", "false", "no"}:
            return False
        return (self.app_env or "").lower() == "production"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def database_connect_args(self) -> dict:
        if self.database_iam_auth or (self.database_ssl_mode or "").lower() == "require":
            return {"ssl": "require"}
        return {}

    @property
    def internal_service_tokens(self) -> set[str]:
        """Tokens AI may present via X-Internal-Token."""
        return {t for t in (self.ai_service_token, self.core_internal_token) if t}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_runtime()
    return settings
