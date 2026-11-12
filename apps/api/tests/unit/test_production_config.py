"""Production boot must not exit when Cognito is not configured."""

import pytest

from src.core.config import Settings


def _production(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "jwt_secret": "p" * 32,
        "ai_service_token": "a" * 32,
        "core_internal_token": "c" * 32,
        "database_ssl_mode": "require",
        "database_iam_auth": False,
        "s3_bucket": "sybeez-school",
        "auth_mode": "both",
        "cognito_region": "us-east-1",
        "cognito_user_pool_id": "",
        "cognito_app_client_id": "",
        "cognito_domain": "",
        "jobs_backend": "redis",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_starts_with_the_school_jwt_when_cognito_is_unset():
    _production().validate_runtime()


def test_production_still_requires_a_complete_pool_when_cognito_is_partial():
    settings = _production(cognito_user_pool_id="us-east-1_example")
    with pytest.raises(RuntimeError, match="COGNITO_APP_CLIENT_ID"):
        settings.validate_runtime()


def test_production_starts_when_the_pool_has_no_hosted_ui_domain():
    settings = _production(
        cognito_region="ap-south-1",
        cognito_user_pool_id="ap-south-1_pool",
        cognito_app_client_id="client",
        cognito_oauth_redirect_uri="https://api.example/callback",
        web_app_url="https://app.example",
        cognito_domain="",
        database_iam_auth=True,
        database_url="postgresql+asyncpg://sybeez_app@db.example.com:5432/sybeez_school",
        aws_region="ap-south-1",
    )
    settings.validate_runtime()


def test_production_requires_cognito_when_auth_mode_is_cognito():
    settings = _production(auth_mode="cognito")
    with pytest.raises(RuntimeError, match="COGNITO_USER_POOL_ID"):
        settings.validate_runtime()
