"""RDS IAM authentication for the API and the worker.

`sybeez_app` has `rds_iam`, so Postgres rejects a password. When DB_IAM_AUTH
is true, each new connection presents a token from `generate_db_auth_token`.
Tokens last 15 minutes. The cache mints a new one after 10 minutes, and the
pool recycles connections on the same interval so a checkout never reuses a
token past its expiry. SSL is required.

Host, port, user, and database come from DATABASE_URL unless DATABASE_HOST,
DATABASE_PORT, DATABASE_USER, or DATABASE_NAME is set. Region is AWS_REGION.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

TOKEN_LIFETIME_SECONDS = 15 * 60
REFRESH_AFTER_SECONDS = 10 * 60
POOL_RECYCLE_SECONDS = 10 * 60


@dataclass(frozen=True)
class IamEndpoint:
    host: str
    port: int
    user: str
    database: str
    region: str


def resolve_endpoint(
    *,
    database_url: str,
    database_host: str = "",
    database_port: int | None = None,
    database_user: str = "",
    database_name: str = "",
    aws_region: str = "",
) -> IamEndpoint:
    raw = (database_url or "").strip()
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql+psycopg://"):
        if raw.startswith(prefix):
            raw = "postgresql://" + raw[len(prefix) :]
            break
    parsed = urlparse(raw) if raw else None
    host = (database_host or (parsed.hostname if parsed else "") or "").strip()
    user = (database_user or (unquote(parsed.username) if parsed and parsed.username else "") or "").strip()
    database = (database_name or (parsed.path.lstrip("/") if parsed and parsed.path else "") or "").strip()
    if database_port is not None:
        port = int(database_port)
    elif parsed is not None and parsed.port:
        port = int(parsed.port)
    else:
        port = 5432
    region = (aws_region or "").strip()
    missing = [
        name
        for name, value in (
            ("host", host),
            ("user", user),
            ("database", database),
            ("region", region),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "DB_IAM_AUTH requires host, user, database, and region from DATABASE_URL or env. "
            f"Missing: {', '.join(missing)}."
        )
    return IamEndpoint(host=host, port=port, user=user, database=database, region=region)


def generate_auth_token(endpoint: IamEndpoint, *, client=None) -> str:
    rds = client if client is not None else _rds_client(endpoint.region)
    return rds.generate_db_auth_token(
        DBHostname=endpoint.host,
        Port=int(endpoint.port),
        DBUsername=endpoint.user,
        Region=endpoint.region,
    )


class TokenCache:
    """Mint a new RDS token before the 15-minute lifetime ends."""

    def __init__(self, mint, *, refresh_after: int = REFRESH_AFTER_SECONDS, clock=time.monotonic):
        self._mint = mint
        self._refresh_after = refresh_after
        self._clock = clock
        self._token: str | None = None
        self._issued = 0.0

    def get(self) -> str:
        now = self._clock()
        if self._token is None or now - self._issued >= self._refresh_after:
            self._token = self._mint()
            self._issued = now
        return self._token


def attach_iam_auth(engine: AsyncEngine, cache: TokenCache):
    """Register a do_connect listener that supplies a fresh token and requires SSL."""

    def provide_token(dialect, conn_rec, cargs, cparams):
        cparams["password"] = cache.get()
        cparams["ssl"] = "require"
        return None

    event.listen(engine.sync_engine, "do_connect", provide_token)
    return provide_token


def create_iam_async_engine(
    *,
    database_url: str,
    database_host: str = "",
    database_port: int | None = None,
    database_user: str = "",
    database_name: str = "",
    aws_region: str = "",
    echo: bool = False,
    client=None,
) -> AsyncEngine:
    endpoint = resolve_endpoint(
        database_url=database_url,
        database_host=database_host,
        database_port=database_port,
        database_user=database_user,
        database_name=database_name,
        aws_region=aws_region,
    )
    url = (
        f"postgresql+asyncpg://{quote(endpoint.user)}@{endpoint.host}:{endpoint.port}/"
        f"{quote(endpoint.database)}"
    )
    engine = create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=POOL_RECYCLE_SECONDS,
        connect_args={"ssl": "require"},
    )
    cache = TokenCache(lambda: generate_auth_token(endpoint, client=client))
    attach_iam_auth(engine, cache)
    return engine


def _rds_client(region: str):
    import boto3

    return boto3.client("rds", region_name=region)
