"""Worker uses the same RDS IAM engine as the API."""

from src.core.config import Settings, get_settings
from src.core.database import get_engine
from src.core.rds_iam import REFRESH_AFTER_SECONDS, TokenCache


class _Rds:
    def generate_db_auth_token(self, **kwargs):
        self.kwargs = kwargs
        return "worker-token"


def test_worker_do_connect_uses_iam_token(monkeypatch):
    monkeypatch.setenv("DB_IAM_AUTH", "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://sybeez_app@db.example:5432/sybeez_school",
    )
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    get_settings.cache_clear()
    import src.core.database as database

    database._engine = None
    monkeypatch.setattr(
        "src.core.rds_iam._rds_client",
        lambda region: _Rds(),
    )
    engine = get_engine()
    try:
        cparams = {}
        for fn in engine.sync_engine.dialect.dispatch.do_connect:
            fn(engine.sync_engine.dialect, None, [], cparams)
        assert cparams["password"] == "worker-token"
        assert cparams["ssl"] == "require"
    finally:
        engine.sync_engine.dispose()
        database._engine = None
        get_settings.cache_clear()


def test_worker_token_cache_refreshes():
    clock = {"t": 0.0}
    cache = TokenCache(lambda: "a", refresh_after=REFRESH_AFTER_SECONDS, clock=lambda: clock["t"])
    assert cache.get() == "a"
    clock["t"] = REFRESH_AFTER_SECONDS
    cache._mint = lambda: "b"
    assert cache.get() == "b"


def test_worker_settings_accept_db_iam_auth(monkeypatch):
    monkeypatch.delenv("DB_IAM_AUTH", raising=False)
    monkeypatch.setenv("DATABASE_IAM_AUTH", "true")
    get_settings.cache_clear()
    assert Settings(_env_file=None).db_iam_auth is True
    get_settings.cache_clear()
