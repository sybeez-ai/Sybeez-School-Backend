"""Frontend audit gaps: lists, report files, Form 16 PDF, payslip inbox, branches, HRM login."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from src.api.dependencies import CurrentUser
from src.core.exceptions import ForbiddenError
from src.main import app
from src.modules.auth.service import AuthService
from src.modules.payroll.pdf import build_form16_pdf, build_payslip_pdf
from src.modules.payroll.reports import distinct_branches, professional_tax_csv, tds_24q_csv
from src.modules.payroll.service import PayrollService
from src.shared.enums import MembershipStatus, UserRole

client = TestClient(app)


def test_openapi_includes_frontend_gap_routes():
    paths = client.get("/openapi.json").json()["paths"]
    expected = [
        "/api/v1/payroll/salary-assignments",
        "/api/v1/payroll/arrears",
        "/api/v1/payroll/reimbursements",
        "/api/v1/payroll/declarations",
        "/api/v1/payroll/me/declaration",
        "/api/v1/payroll/runs/{run_id}/pt-report",
        "/api/v1/payroll/runs/{run_id}/tds-24q",
        "/api/v1/payroll/form16/me/pdf",
        "/api/v1/payroll/form16/{employee_id}/pdf",
        "/api/v1/payroll/notifications",
        "/api/v1/schools/me/branches",
    ]
    for path in expected:
        assert path in paths, path
    assert "get" in paths["/api/v1/payroll/salary-assignments"]
    assert "get" in paths["/api/v1/payroll/arrears"]
    assert "get" in paths["/api/v1/payroll/reimbursements"]
    assert "get" in paths["/api/v1/payroll/declarations"]
    assert "get" in paths["/api/v1/payroll/me/declaration"]


def test_report_files_and_form16_pdf():
    pt = professional_tax_csv(
        [{"employee_id": "e1", "employee_name": "=bad", "employee_code": "T1", "state": "KA", "gender": "MALE", "pt_amount": "200"}]
    )
    assert pt.splitlines()[0].startswith("employee_id,")
    assert "'=bad" in pt
    tds = tds_24q_csv(
        [{"employee_id": "e1", "employee_name": "Ada", "pan": "ABCDE1234F", "gross_amount": "30000", "tds_amount": "0", "tax_regime": "NEW"}]
    )
    assert "192" in tds
    assert "ABCDE1234F" in tds
    pdf = build_form16_pdf(
        {
            "financial_year": "2026-2027",
            "gross": "360000",
            "tds_deducted": "0",
            "payslip_count": 1,
            "snapshot": {"employee_name": "Ada", "pan": "ABCDE1234F", "regime": "NEW", "total_tax": "0"},
        }
    )
    assert pdf.startswith(b"%PDF")
    assert build_payslip_pdf({"school_name": "Sybeez School", "template_code": "CLASSIC"}).startswith(b"%PDF")


def test_branch_names_are_unique_and_skip_blanks():
    assert distinct_branches(["", "North", " north ", None, "South"]) == ["North", "South"]


def test_hrm_login_allows_hr_manager_and_payroll_approver():
    class Membership:
        def __init__(self, role: str):
            self.role = role
            self.status = MembershipStatus.ACTIVE.value

    class Repo:
        def __init__(self, role: str):
            self.role = role

        async def get_membership_for_role(self, user_id, role, school_id):
            if role == self.role:
                return Membership(self.role)
            return None

        async def get_membership(self, user_id, school_id):
            return Membership(self.role)

    async def check(role: str):
        service = AuthService(None)
        service.repo = Repo(role)
        user = SimpleNamespace(id=uuid4(), is_platform_admin=False)
        await service._assert_portal_role(user, "hrm")
        try:
            await service._assert_portal_role(user, "teacher")
        except ForbiddenError as exc:
            assert "hrm" in exc.message
        else:
            raise AssertionError("teacher portal should reject this role")

    asyncio.run(check(UserRole.HR_MANAGER.value))
    asyncio.run(check(UserRole.PAYROLL_APPROVER.value))


def test_payslip_notification_query_is_school_and_user_scoped():
    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

        def first(self):
            return None

        def __iter__(self):
            return iter(())

    seen = []

    class DB:
        def __init__(self):
            self.info = {}

        async def execute(self, statement, params=None):
            seen.append(str(statement.compile(dialect=postgresql.dialect())))
            return Result()

    user = CurrentUser(
        id=uuid4(),
        email="ada@school.test",
        full_name="Ada",
        role=UserRole.TEACHER,
        school_id=uuid4(),
        membership_id=None,
        is_platform_admin=False,
    )

    async def run():
        await PayrollService(DB(), user).list_payslip_notifications()

    asyncio.run(run())
    listing = [sql for sql in seen if "FROM notifications" in sql]
    assert listing
    assert "notifications.school_id" in listing[-1]
    assert "notifications.user_id" in listing[-1]
    assert "notifications.category" in listing[-1]
