from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.modules.audit.repository import AuditRepository
from src.modules.platform.router import attention_items


def test_attention_reads_a_dumped_dashboard():
    items = attention_items(
        {"attention": [{"code": "suspended", "message": "1 school is suspended", "severity": "warn"}]}
    )
    assert items[0]["code"] == "suspended"


def test_attention_still_reads_a_model():
    class Item:
        def model_dump(self):
            return {"code": "trial", "message": "trial ending", "severity": "info"}

    class Dashboard:
        attention = [Item()]

    assert attention_items(Dashboard())[0]["code"] == "trial"


class _Db:
    def __init__(self, role: str):
        self.info = {"rls_tenant": {"role": role, "bypass": "off", "school_id": ""}}
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_platform_audit_write_opens_the_school(monkeypatch):
    opened = []

    async def open_platform_school(db, school_id):
        opened.append(school_id)

    monkeypatch.setattr("src.modules.fees.tenant.open_platform_school", open_platform_school)
    school_id = uuid4()
    await AuditRepository(_Db("PLATFORM_ADMIN")).write(action="platform.school.suspend", school_id=school_id)
    assert opened == [school_id]


@pytest.mark.asyncio
async def test_school_audit_write_does_not_open_platform_scope(monkeypatch):
    opened = AsyncMock()
    monkeypatch.setattr("src.modules.fees.tenant.open_platform_school", opened)
    await AuditRepository(_Db("SCHOOL_ADMIN")).write(action="school.profile.update", school_id=uuid4())
    opened.assert_not_called()
