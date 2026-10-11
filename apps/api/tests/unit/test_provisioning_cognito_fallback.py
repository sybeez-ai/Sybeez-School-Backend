from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.auth import cognito as cognito_svc
from src.modules.schools import provisioning


def _db_without_user():
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    return db


def _deny(**_kwargs):
    raise cognito_svc.CognitoAccessDeniedError("Cognito create user failed: AccessDeniedException")


@pytest.mark.asyncio
async def test_access_denied_falls_back_to_local_password(monkeypatch):
    monkeypatch.setattr(cognito_svc, "cognito_configured", lambda: True)
    monkeypatch.setattr(cognito_svc, "admin_create_user", _deny)
    monkeypatch.setattr(provisioning, "get_settings", lambda: SimpleNamespace(auth_mode="both"))
    user, temp = await provisioning.get_or_create_user(
        _db_without_user(), email="Ashok@Marry.edu", full_name="Ashok", password="Temp#Pass123"
    )
    assert temp == "Temp#Pass123"
    assert user.cognito_sub is None
    assert user.password_hash and user.password_hash != "Temp#Pass123"


@pytest.mark.asyncio
async def test_access_denied_still_fails_in_cognito_only_mode(monkeypatch):
    monkeypatch.setattr(cognito_svc, "cognito_configured", lambda: True)
    monkeypatch.setattr(cognito_svc, "admin_create_user", _deny)
    monkeypatch.setattr(provisioning, "get_settings", lambda: SimpleNamespace(auth_mode="cognito"))
    with pytest.raises(cognito_svc.CognitoAccessDeniedError):
        await provisioning.get_or_create_user(
            _db_without_user(), email="a@b.edu", full_name="A", password="Temp#Pass123"
        )
