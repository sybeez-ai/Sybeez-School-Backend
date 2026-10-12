"""Students read fee settings without creating them (RLS forbids the insert)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.core.exceptions import ForbiddenError
from src.modules.fees.service import FeeService


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDb:
    def __init__(self, *values):
        self._values = list(values)
        self.added = []

    async def execute(self, _stmt):
        return _Result(self._values.pop(0))

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        raise AssertionError("student fee lookup must not write")


@pytest.mark.asyncio
async def test_student_without_fee_settings_is_forbidden_without_insert():
    db = _FakeDb(None)
    with pytest.raises(ForbiddenError, match="disabled"):
        await FeeService(db)._linked_student_ids(uuid4(), uuid4(), "STUDENT")
    assert db.added == []


@pytest.mark.asyncio
async def test_student_with_fee_viewing_enabled_sees_only_self():
    me = uuid4()
    db = _FakeDb(SimpleNamespace(allow_student_view_fees=True), SimpleNamespace(id=me))
    assert await FeeService(db)._linked_student_ids(uuid4(), uuid4(), "STUDENT") == [me]
