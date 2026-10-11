"""Super-admin provision sets a permanent password and retires the old account."""

from pathlib import Path

from src.core.cognito import cognito_admin_denied, retire_cognito_user, set_cognito_password
from src.core.config import get_settings
from src.db.models import User
from src.modules.platform.provision_superadmin import (
    RETIRED_SUPERADMIN_EMAIL,
    apply_retirement,
    apply_superadmin,
)


class _Cognito:
    def __init__(self, *, exists: bool = False, username: str = "admin@sybeez.ai"):
        self.exists = exists
        self.username = username
        self.created = 0
        self.passwords: list[dict] = []
        self.enabled = 0
        self.disabled = 0
        self.deleted = 0

    def admin_get_user(self, **_kwargs):
        if not self.exists:
            error = type("UserNotFoundException", (Exception,), {})
            raise error("missing")
        return {"Username": self.username}

    def list_users(self, **_kwargs):
        if not self.exists:
            return {"Users": []}
        return {"Users": [{"Username": self.username}]}

    def admin_create_user(self, **_kwargs):
        self.created += 1
        self.exists = True

    def admin_set_user_password(self, **kwargs):
        self.passwords.append(kwargs)

    def admin_enable_user(self, **_kwargs):
        self.enabled += 1

    def admin_disable_user(self, **_kwargs):
        self.disabled += 1

    def admin_delete_user(self, **_kwargs):
        self.deleted += 1
        self.exists = False


def test_set_cognito_password_creates_and_resets(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "cognito_user_pool_id", "pool")
    client = _Cognito()
    assert set_cognito_password("Admin@Sybeez.ai", "first-secret", client=client) == "created"
    assert set_cognito_password("admin@sybeez.ai", "second-secret", client=client) == "reset"
    assert client.created == 1
    assert len(client.passwords) == 2
    assert all(call["Permanent"] is True for call in client.passwords)
    assert client.passwords[-1]["Password"] == "second-secret"
    assert client.enabled == 2


def test_cognito_admin_denied_is_access_denied_only():
    class Denied(Exception):
        response = {"Error": {"Code": "AccessDeniedException", "Message": "hidden"}}

    class Invalid(Exception):
        response = {"Error": {"Code": "InvalidPasswordException", "Message": "hidden"}}

    assert cognito_admin_denied(Denied()) is True
    assert cognito_admin_denied(Invalid()) is False
    assert cognito_admin_denied(RuntimeError("no")) is False


def test_set_cognito_password_skips_without_a_pool(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "cognito_user_pool_id", "")
    client = _Cognito()
    assert set_cognito_password("admin@sybeez.ai", "secret", client=client) == "skipped"
    assert client.passwords == []


def test_retire_cognito_user_deletes_and_is_idempotent(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "cognito_user_pool_id", "pool")
    client = _Cognito(exists=True, username="old-sub")
    assert retire_cognito_user("admin@cybis.ai", client=client) == "deleted"
    assert client.disabled == 1
    assert client.deleted == 1
    assert retire_cognito_user("admin@cybis.ai", client=client) == "missing"


def test_retire_cognito_user_skips_without_a_pool(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "cognito_user_pool_id", "")
    client = _Cognito(exists=True)
    assert retire_cognito_user("admin@cybis.ai", client=client) == "skipped"
    assert client.deleted == 0


def test_apply_superadmin_clears_cognito_sub_and_sets_the_hash():
    user = User(email="admin@sybeez.ai", full_name="Existing", cognito_sub="old-sub", is_active=False)
    apply_superadmin(user, password_hash="hashed")
    assert user.password_hash == "hashed"
    assert user.cognito_sub is None
    assert user.is_platform_admin is True
    assert user.is_active is True
    assert user.status == "ACTIVE"
    assert user.platform_role == "SUPER_ADMIN"


def test_apply_retirement_removes_platform_access():
    user = User(
        email=RETIRED_SUPERADMIN_EMAIL,
        full_name="Old Admin",
        password_hash="hashed",
        cognito_sub="old-sub",
        is_platform_admin=True,
        is_active=True,
        platform_role="SUPER_ADMIN",
        status="ACTIVE",
    )
    apply_retirement(user)
    assert user.is_active is False
    assert user.is_platform_admin is False
    assert user.platform_role is None
    assert user.password_hash is None
    assert user.cognito_sub is None
    assert user.status == "DISABLED"


def test_seed_and_config_do_not_hardcode_the_superadmin_password():
    root = Path(__file__).resolve().parents[4]
    seed = (root / "apps/api/src/db/seed.py").read_text()
    config = (root / "apps/api/src/core/config.py").read_text()
    assert "Silver@" not in seed
    assert "admin@cybis.ai" not in seed
    assert "Silver@" not in config
    assert RETIRED_SUPERADMIN_EMAIL not in config
