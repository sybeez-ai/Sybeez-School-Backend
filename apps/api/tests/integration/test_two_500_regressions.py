"""Regressions: admin Messages contacts (Parent has no full_name/email) and the weekly AI report context query (date params)."""

from datetime import date, timedelta

import pytest

from src.modules.ai_reports.service import AiReportsService
from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio


async def test_message_contacts_for_admin_and_teacher(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.commit()
    for who in (w.admin, w.teacher, w.parent):
        r = await api.get("/api/v1/communication/contacts", headers=who.h)
        assert r.status_code == 200, (who.role, r.text)
    roles = {c["role"] for c in (await api.get("/api/v1/communication/contacts", headers=w.admin.h)).json()}
    assert "PARENT" in roles and "TEACHER" in roles


async def test_report_context_query_accepts_dates(db_session):
    w = await build_world(db_session)
    await db_session.commit()
    end = date.today()
    ctx = await AiReportsService(db_session)._gather_context(w.school_id, w.students[0].profile_id, end - timedelta(days=7), end)
    assert ctx["period_end"] == end.isoformat()
