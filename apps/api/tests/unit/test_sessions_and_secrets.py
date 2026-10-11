"""Session lifetime, cookie flags, secret parsing, Cognito bootstrap, and the demo-seed gate."""

import asyncio
from datetime import timedelta

import pytest
from fastapi import Response

from src.core.cognito import ensure_cognito_user
from src.core.config import get_settings
from src.core.cookies import set_auth_cookies
from src.core.secrets import password_from_secret_string
from src.core.sessions import session_lifetime
from src.db.seed import seed
from src.shared.enums import UserRole


def test_daily_roles_are_absolute_and_school_roles_slide():
    admin_ttl, admin_absolute = session_lifetime(UserRole.PLATFORM_ADMIN)
    school_ttl, school_absolute = session_lifetime(UserRole.SCHOOL_ADMIN)
    finance_ttl, finance_absolute = session_lifetime(UserRole.FINANCE)
    hr_ttl, hr_absolute = session_lifetime(UserRole.HR)
    teacher_ttl, teacher_absolute = session_lifetime(UserRole.TEACHER)
    student_ttl, student_absolute = session_lifetime(UserRole.STUDENT)
    parent_ttl, parent_absolute = session_lifetime(UserRole.PARENT)
    assert admin_ttl == school_ttl == finance_ttl == hr_ttl == timedelta(hours=24)
    assert admin_absolute and school_absolute and finance_absolute and hr_absolute
    assert teacher_ttl == student_ttl == parent_ttl == timedelta(hours=720)
    assert not teacher_absolute and not student_absolute and not parent_absolute


def test_session_hours_are_configurable_without_dropping_the_absolute_cap(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "session_ttl_hours", "SCHOOL_ADMIN:12,TEACHER:48")
    school_ttl, school_absolute = session_lifetime(UserRole.SCHOOL_ADMIN)
    teacher_ttl, teacher_absolute = session_lifetime(UserRole.TEACHER)
    assert school_ttl == timedelta(hours=12)
    assert school_absolute is True
    assert teacher_ttl == timedelta(hours=48)
    assert teacher_absolute is False


def test_password_from_secret_string_accepts_raw_and_json():
    assert password_from_secret_string("  raw-secret  ") == "raw-secret"
    assert password_from_secret_string('{"SUPERADMIN_INITIAL_PASSWORD":"from-key"}') == "from-key"
    assert password_from_secret_string('{"password":"from-password"}') == "from-password"
    assert password_from_secret_string('{"other":"nope"}') is None
    assert password_from_secret_string("") is None
    assert password_from_secret_string('"quoted"') == "quoted"


def test_production_cookies_are_secure(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "cookie_secure", "auto")
    response = Response()
    set_auth_cookies(
        response,
        access_token="access",
        refresh_token="refresh",
        access_max_age=60,
        refresh_max_age=120,
    )
    headers = response.headers.getlist("set-cookie")
    assert len(headers) == 2
    for header in headers:
        attributes = [piece.strip().lower() for piece in header.split(";")]
        assert "httponly" in attributes
        assert "secure" in attributes
        assert "samesite=lax" in attributes


class _Cognito:
    def __init__(self):
        self.exists = False
        self.created = 0
        self.passwords = 0

    def admin_get_user(self, **_kwargs):
        if not self.exists:
            raise type("UserNotFoundException", (Exception,), {})("missing")
        return {"Username": "admin@sybeez.ai"}

    def admin_create_user(self, **_kwargs):
        self.created += 1
        self.exists = True

    def admin_set_user_password(self, **_kwargs):
        self.passwords += 1


def test_cognito_bootstrap_does_not_reset_an_existing_password(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "cognito_user_pool_id", "pool")
    monkeypatch.setattr(settings, "cognito_client_id", "client")
    client = _Cognito()
    ensure_cognito_user("admin@sybeez.ai", "first-secret", client=client)
    ensure_cognito_user("admin@sybeez.ai", "second-secret", client=client)
    assert client.created == 1
    assert client.passwords == 1


def test_seed_refuses_without_the_demo_flag(monkeypatch):
    monkeypatch.delenv("SEED_DEMO_DATA", raising=False)
    with pytest.raises(SystemExit, match="SEED_DEMO_DATA"):
        asyncio.run(seed())


def test_seed_requires_demo_passwords_from_the_environment(monkeypatch):
    monkeypatch.setenv("SEED_DEMO_DATA", "true")
    monkeypatch.delenv("DEMO_PLATFORM_PASSWORD", raising=False)
    monkeypatch.delenv("DEMO_SCHOOL_ADMIN_PASSWORD", raising=False)
    with pytest.raises(SystemExit, match="DEMO_PLATFORM_PASSWORD"):
        asyncio.run(seed())
