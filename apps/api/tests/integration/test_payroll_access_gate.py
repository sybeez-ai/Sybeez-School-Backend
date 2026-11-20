"""Payroll: Finance/HR portal roles do not read payroll without a payroll grant; non-payroll callers get 403, not 422."""

import pytest

from tests.integration.online_tests_world import _person, build_world
from tests.integration.test_online_tests_api import api  # noqa: F401
from src.shared.enums import UserRole

pytestmark = pytest.mark.asyncio
P = "/api/v1/payroll"


async def test_portal_roles_and_write_gate(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    acc = await _person(db_session, w.school_id, UserRole.ACCOUNTANT, "Gate Accountant")
    hr = await _person(db_session, w.school_id, UserRole.HR_MANAGER, "Gate HR")
    await db_session.commit()
    for who in (acc, hr):
        for path in ("/dashboard", "/runs", "/audit"):
            assert (await api.get(P + path, headers=who.h)).status_code == 403, (who.role, path)
    # non-payroll callers: 403 before body validation
    for who in (w.students[0], w.parent, w.teacher, acc):
        for path in ("/runs/generate", "/salary-assignments"):
            assert (await api.post(P + path, json={}, headers=who.h)).status_code == 403, (who.role, path)
    assert (await api.get(P + "/dashboard", headers=w.admin.h)).status_code == 200
    assert (await api.post(P + "/runs/generate", json={}, headers=w.admin.h)).status_code == 422  # admin reaches validation
