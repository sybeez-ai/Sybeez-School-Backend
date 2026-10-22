"""RDS IAM tokens refresh before the 15-minute expiry and require SSL."""

from pathlib import Path

from src.core.config import Settings
from src.core.rds_iam import (
    POOL_RECYCLE_SECONDS,
    REFRESH_AFTER_SECONDS,
    TOKEN_LIFETIME_SECONDS,
    TokenCache,
    create_iam_async_engine,
    generate_auth_token,
    resolve_endpoint,
)

API_RDS = Path(__file__).resolve().parents[2] / "src" / "core" / "rds_iam.py"
WORKER_RDS = Path(__file__).resolve().parents[4] / "apps" / "worker" / "src" / "core" / "rds_iam.py"


class _Rds:
    def __init__(self):
        self.calls = []

    def generate_db_auth_token(self, **kwargs):
        self.calls.append(kwargs)
        return f"token-{len(self.calls)}"


def test_worker_module_matches_api():
    assert API_RDS.read_text() == WORKER_RDS.read_text()


def test_endpoint_comes_from_database_url():
    endpoint = resolve_endpoint(
        database_url="postgresql+asyncpg://sybeez_app:ignored@db.xxxx.ap-south-1.rds.amazonaws.com:5432/sybeez_school",
        aws_region="ap-south-1",
    )
    assert endpoint.host == "db.xxxx.ap-south-1.rds.amazonaws.com"
    assert endpoint.port == 5432
    assert endpoint.user == "sybeez_app"
    assert endpoint.database == "sybeez_school"
    assert endpoint.region == "ap-south-1"


def test_env_overrides_database_url():
    endpoint = resolve_endpoint(
        database_url="postgresql+asyncpg://other:pw@old.example:5432/olddb",
        database_host="db.example",
        database_port=5433,
        database_user="sybeez_app",
        database_name="sybeez_school",
        aws_region="ap-south-1",
    )
    assert endpoint.host == "db.example"
    assert endpoint.port == 5433
    assert endpoint.user == "sybeez_app"
    assert endpoint.database == "sybeez_school"


def test_generate_db_auth_token_arguments():
    endpoint = resolve_endpoint(
        database_url="postgresql+asyncpg://sybeez_app@db.example:5432/sybeez_school",
        aws_region="ap-south-1",
    )
    client = _Rds()
    assert generate_auth_token(endpoint, client=client) == "token-1"
    assert client.calls == [
        {
            "DBHostname": "db.example",
            "Port": 5432,
            "DBUsername": "sybeez_app",
            "Region": "ap-south-1",
        }
    ]


def test_token_refreshes_before_fifteen_minute_expiry():
    assert REFRESH_AFTER_SECONDS < TOKEN_LIFETIME_SECONDS
    clock = {"t": 0.0}
    mints = {"n": 0}

    def mint():
        mints["n"] += 1
        return f"t{mints['n']}"

    cache = TokenCache(mint, refresh_after=REFRESH_AFTER_SECONDS, clock=lambda: clock["t"])
    assert cache.get() == "t1"
    clock["t"] = REFRESH_AFTER_SECONDS - 1
    assert cache.get() == "t1"
    clock["t"] = REFRESH_AFTER_SECONDS
    assert cache.get() == "t2"


def test_do_connect_sets_password_and_requires_ssl():
    client = _Rds()
    engine = create_iam_async_engine(
        database_url="postgresql+asyncpg://sybeez_app@db.example:5432/sybeez_school",
        aws_region="ap-south-1",
        client=client,
    )
    try:
        assert engine.sync_engine.pool._recycle == POOL_RECYCLE_SECONDS
        assert engine.url.password is None
        cparams = {}
        for fn in engine.sync_engine.dialect.dispatch.do_connect:
            fn(engine.sync_engine.dialect, None, [], cparams)
        assert cparams["password"] == "token-1"
        assert cparams["ssl"] == "require"
    finally:
        engine.sync_engine.dispose()


def test_db_iam_auth_env_alias(monkeypatch):
    monkeypatch.setenv("DB_IAM_AUTH", "true")
    settings = Settings(_env_file=None)
    assert settings.database_iam_auth is True


def test_production_starts_without_cognito_hosted_ui_domain():
    settings = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="x" * 40,
        ai_service_token="y" * 40,
        core_internal_token="z" * 40,
        database_iam_auth=True,
        database_url="postgresql+asyncpg://sybeez_app@db.example.com:5432/sybeez_school",
        aws_region="ap-south-1",
        s3_bucket="sybeez-bucket",
        auth_mode="both",
        cognito_region="ap-south-1",
        cognito_user_pool_id="ap-south-1_pool",
        cognito_app_client_id="client",
        cognito_domain="",
        cognito_oauth_redirect_uri="https://api.example/callback",
        web_app_url="https://app.example",
    )
    settings.validate_runtime()
