"""Today's timetable uses the school's timezone, not the UTC host clock."""

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from src.modules.timetable.service import TimetableService


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDb:
    def __init__(self, value):
        self.value = value

    async def execute(self, _stmt):
        return _Result(self.value)


@pytest.mark.asyncio
async def test_school_now_uses_school_timezone():
    now = await TimetableService(_FakeDb("Asia/Kolkata"))._school_now(uuid4())
    assert now.utcoffset() == datetime.now(ZoneInfo("Asia/Kolkata")).utcoffset()


@pytest.mark.asyncio
@pytest.mark.parametrize("tz", [None, "Not/AZone"])
async def test_school_now_falls_back_to_utc(tz):
    now = await TimetableService(_FakeDb(tz))._school_now(uuid4())
    assert now.utcoffset().total_seconds() == 0
