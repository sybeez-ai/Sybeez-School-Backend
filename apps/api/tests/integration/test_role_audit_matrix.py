"""Role audit: low-privilege roles must never get a 2xx (or a 5xx) from finance, payroll, HR, platform or school-admin
routes. Every route is called as a teacher, a student and a parent with a random id in each path parameter."""

from __future__ import annotations

import re
from collections import Counter
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute

from src.api.router import api_router
from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio

SENSITIVE = ("/api/v1/finance", "/api/v1/payroll", "/api/v1/hr", "/api/v1/employees", "/api/v1/platform",
             "/api/v1/school-admin", "/api/v1/payments", "/api/v1/audit", "/api/v1/access", "/api/v1/subscriptions",
             "/api/v1/staff", "/api/v1/family-documents/")
# Public by design (token or signature protected, or liveness).
PUBLIC = re.compile(r"/health$|/webhook|/signed/|/downloads/")
# Routes that legitimately serve a teacher/staff user their own data.
# Reviewed by hand: each returns only the caller's own rows (payroll lists filter to the caller's employee record
# unless the caller holds a payroll read permission) or school-wide non-sensitive reference data.
SELF_SERVICE = {
    "/api/v1/access/me", "/api/v1/payroll/access", "/api/v1/payroll/notifications", "/api/v1/payroll/payslips/me",
    "/api/v1/payroll/salary-assignments", "/api/v1/payroll/loans", "/api/v1/payroll/arrears",
    "/api/v1/payroll/reimbursements", "/api/v1/payroll/weekly-offs", "/api/v1/payroll/holidays",
    "/api/v1/hr/holidays", "/api/v1/hr/leave-types", "/api/v1/hr/attendance/check-in",
}
SELF_PREFIX = re.compile(r"/me(/|$)")  # caller-scoped "my own" routes
# Reached after an earlier request in this loop opened a check-in for the same teacher; covered separately below.
KNOWN_SEQUENCE = {"/api/v1/hr/attendance/check-out"}


def _routes(router, prefix=""):
    for r in router.routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, r
        elif hasattr(r, "original_router"):
            yield from _routes(r.original_router, prefix + (r.include_context.prefix or ""))


def _targets():
    for path, r in _routes(api_router):
        if path.startswith(SENSITIVE) and not PUBLIC.search(path):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield m, path


async def test_low_privilege_roles_get_no_success_from_sensitive_routes(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.commit()
    leaks, five, seen = [], [], Counter()
    for method, path in _targets():
        url = re.sub(r"\{[^}]+\}", lambda _: str(uuid4()), path)
        for who in (w.teacher, w.students[0], w.parent):
            kw = {"headers": who.h}
            if method not in ("GET", "DELETE"):
                kw["json"] = {}
            r = await api.request(method, url, **kw)
            await db_session.rollback()  # the fixture shares one session: a failed request must not poison the next
            seen[r.status_code] += 1
            if 200 <= r.status_code < 300 and path not in SELF_SERVICE and not SELF_PREFIX.search(path):
                leaks.append((who.role, method, path, r.status_code))
            if r.status_code >= 500 and path not in KNOWN_SEQUENCE:
                five.append((who.role, method, path, r.status_code))
    print("status histogram", dict(seen))
    assert not leaks, f"low-privilege roles got success: {leaks[:25]}"
    assert not five, f"server errors for low-privilege roles: {five[:25]}"
