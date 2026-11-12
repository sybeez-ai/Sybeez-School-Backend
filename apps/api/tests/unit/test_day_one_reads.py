"""Day-one read paths: finance settings race, India dates, progress privacy, parent chat hold."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from src.core.exceptions import ForbiddenError, NotFoundError
from src.modules.communication.parent_chat import PARENT_CHAT_ON_HOLD, reject_parent_chat
from src.modules.fees.dates import paid_on
from src.modules.fees.open_decisions import SECTION_80C_RULES, TALLY_LEDGER_MAPPING
from src.modules.fees.service import FeeService
from src.modules.online_tests.service import OnlineTestService
from src.modules.progress.service import ProgressService
from src.shared.enums import UserRole


class _Scalars:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _Result:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return _Scalars(self._value)

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SettingsDb:
    def __init__(self, found):
        self.found = list(found)
        self.flushed = 0
        self.expunged = []

    def begin_nested(self):
        return _Nested()

    async def execute(self, _stmt):
        return _Result(self.found.pop(0))

    def add(self, _row):
        return None

    async def flush(self):
        self.flushed += 1
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    def expunge(self, row):
        self.expunged.append(row)


def test_paid_on_uses_india_not_utc_date():
    # 19:30 UTC is 01:00 the next day in India.
    moment = datetime(2026, 11, 9, 19, 30, tzinfo=timezone.utc)
    assert paid_on(moment).isoformat() == "2026-11-10"
    assert paid_on(None) is None


@pytest.mark.asyncio
async def test_fee_settings_create_race_returns_the_existing_row():
    school_id = uuid4()
    existing = SimpleNamespace(school_id=school_id, currency="INR")
    db = _SettingsDb([None, existing])
    row = await FeeService(db).get_or_create_settings(school_id)
    assert row is existing
    assert db.flushed == 1
    assert db.expunged


def test_online_test_settings_ignore_a_bad_blob():
    broken = SimpleNamespace(settings={"max_violations": "lots", "shuffle_questions": False})
    settings = OnlineTestService._settings(broken)
    assert settings.max_violations == 3
    assert settings.shuffle_questions is True
    kept = OnlineTestService._settings(SimpleNamespace(settings={"shuffle_questions": False}))
    assert kept.shuffle_questions is False


def test_parent_chat_is_on_hold():
    with pytest.raises(ForbiddenError, match="WhatsApp") as caught:
        reject_parent_chat(UserRole.PARENT)
    assert caught.value.code == "parent_chat_on_hold"
    assert PARENT_CHAT_ON_HOLD in str(caught.value)
    reject_parent_chat(UserRole.TEACHER)


def test_tally_and_80c_stay_undecided():
    assert "Tally" in TALLY_LEDGER_MAPPING
    assert "80C" in SECTION_80C_RULES


@pytest.mark.asyncio
async def test_teacher_progress_unknown_student_is_not_found():
    school_id = uuid4()

    class _Db:
        async def get(self, _model, _id):
            return None

        async def execute(self, _stmt):
            raise AssertionError("must not query after a missing student")

    user_id = uuid4()
    with pytest.raises(NotFoundError):
        await ProgressService(_Db())._assert_teacher_can_view(
            school_id=school_id, user_id=user_id, student_id=uuid4()
        )


@pytest.mark.asyncio
async def test_teacher_progress_without_enrollment_is_forbidden():
    school_id = uuid4()
    calls = {"n": 0}

    class _Db:
        async def get(self, _model, sid):
            return SimpleNamespace(id=sid, school_id=school_id)

        async def execute(self, _stmt):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Result(SimpleNamespace(id=uuid4()))
            return _Result(None)

    with pytest.raises(ForbiddenError, match="not in one of your classes"):
        await ProgressService(_Db())._assert_teacher_can_view(
            school_id=school_id, user_id=uuid4(), student_id=uuid4()
        )

