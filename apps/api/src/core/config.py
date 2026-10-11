from functools import lru_cache

from pydantic import AliasChoices, Field
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
        "PAYROLL_APPROVER:24,TEACHER:720,STUDENT:720,PARENT:720"
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

    # Outbound communication providers (Phase 12)
    sms_provider: str = "mock"  # mock | msg91
    msg91_auth_key: str = ""
    msg91_sender_id: str = ""
    msg91_template_id: str = ""  # optional; if blank, send plain text when supported

    whatsapp_provider: str = "mock"  # mock | meta
    whatsapp_cloud_access_token: str = ""
    whatsapp_cloud_phone_number_id: str = ""

    # Object storage. Empty bucket keeps files on local disk outside production.
    # Set the bucket name from the environment or from Secrets Manager. Never commit a key.
    s3_bucket: str = ""
    s3_region: str = ""
    s3_public_base_url: str = ""
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

        # If Cognito is enabled, required identifiers must be present.
        auth_mode = (self.auth_mode or "").lower().strip()
        if auth_mode in {"cognito", "both"}:
            missing: list[str] = []
            if not self.cognito_region:
                missing.append("COGNITO_REGION")
            if not self.cognito_user_pool_id:
                missing.append("COGNITO_USER_POOL_ID")
            if not self.cognito_app_client_id:
                missing.append("COGNITO_APP_CLIENT_ID")
            if not self.cognito_domain:
                missing.append("COGNITO_DOMAIN")
            if not self.cognito_oauth_redirect_uri:
                missing.append("COGNITO_OAUTH_REDIRECT_URI")
            if not self.web_app_url:
                missing.append("WEB_APP_URL")
            if missing:
                problems.append(f"Missing Cognito settings: {', '.join(missing)}")

        if problems:
            raise RuntimeError(
                "Unsafe production configuration. Fix environment variables: "
                + "; ".join(problems)
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
