from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import DateTime

from src.core.exceptions import ConflictError
from src.db.models import NotificationDelivery
from src.modules.platform import service as platform_service
from src.modules.platform.schemas import SchoolCreate


def test_scheduled_for_is_timezone_aware():
    col = NotificationDelivery.__table__.c.scheduled_for
    assert isinstance(col.type, DateTime) and col.type.timezone is True


@pytest.mark.parametrize(
    "a,b", [("St marry", "st. marry"), ("St. Marry's", "st marrys"), ("ABC  School", "abc-school")]
)
def test_name_key_matches_variants(a, b):
    assert platform_service._name_key(a) == platform_service._name_key(b)


def _payload(**extra):
    return SchoolCreate(
        name="st. marry",
        admin={"full_name": "Test Admin", "email": "test-admin@example.com"},
        **extra,
    )


@pytest.mark.asyncio
async def test_duplicate_school_name_needs_confirmation():
    svc = platform_service.PlatformService.__new__(platform_service.PlatformService)
    svc.db = MagicMock()
    svc.repo = MagicMock()
    svc.repo.get_by_slug = AsyncMock(return_value=None)
    svc._similar_school = AsyncMock(return_value=SimpleNamespace(name="St marry", slug="st-marry-2"))
    with pytest.raises(ConflictError, match="already exists"):
        await svc.create_school(SimpleNamespace(id=None), _payload())


@pytest.mark.asyncio
async def test_taken_slug_is_rejected():
    svc = platform_service.PlatformService.__new__(platform_service.PlatformService)
    svc.db = MagicMock()
    svc.repo = MagicMock()
    svc.repo.get_by_slug = AsyncMock(return_value=object())
    with pytest.raises(ConflictError, match="slug"):
        await svc.create_school(SimpleNamespace(id=None), _payload(slug="st-marry-2"))


def test_admin_password_optional():
    assert _payload().admin.password is None
