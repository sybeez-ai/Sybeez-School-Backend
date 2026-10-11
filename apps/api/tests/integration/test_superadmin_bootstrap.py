"""Super admin bootstrap is idempotent and does not rewrite an existing password."""

import uuid

import pytest
from sqlalchemy import delete

from src.core.database import AsyncSessionLocal, engine
from src.db.models import User
from src.modules.platform.bootstrap import bootstrap_superadmin


@pytest.fixture
async def db():
    # The HTTP suite keeps pooled connections on another loop. Drop them first.
    await engine.dispose()
    async with AsyncSessionLocal() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_local_bootstrap_is_idempotent_and_does_not_clobber_the_hash(db):
    email = f"bootstrap-{uuid.uuid4().hex[:8]}@example.com"
    first = await bootstrap_superadmin(db, email=email, password="Bootstrap1")
    await db.commit()
    assert first is not None
    assert first.is_platform_admin is True
    assert first.password_hash
    original = first.password_hash

    second = await bootstrap_superadmin(db, email=email, password="Different1")
    await db.commit()
    assert second is not None
    assert second.id == first.id
    assert second.password_hash == original

    await db.execute(delete(User).where(User.email == email))
    await db.commit()


@pytest.mark.asyncio
async def test_cognito_bootstrap_stores_no_local_password(db, monkeypatch):
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "auth_provider", "cognito")
    monkeypatch.setattr("src.modules.platform.bootstrap.ensure_cognito_user", lambda email, password: None)
    email = f"cognito-{uuid.uuid4().hex[:8]}@example.com"
    user = await bootstrap_superadmin(db, email=email, password="CognitoPass1")
    await db.commit()
    assert user is not None
    assert user.password_hash is None
    assert user.is_platform_admin is True
    again = await bootstrap_superadmin(db, email=email, password="CognitoPass1")
    await db.commit()
    assert again is not None
    assert again.password_hash is None
    await db.execute(delete(User).where(User.email == email))
    await db.commit()


@pytest.mark.asyncio
async def test_bootstrap_skips_when_no_password_is_configured(db, monkeypatch):
    monkeypatch.setattr("src.modules.platform.bootstrap.resolve_superadmin_password", lambda: None)
    assert await bootstrap_superadmin(db, email=f"missing-{uuid.uuid4().hex[:8]}@example.com") is None
