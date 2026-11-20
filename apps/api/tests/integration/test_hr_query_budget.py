"""HRM list endpoints must not issue one query per row (N+1).

Two schools are seeded with the same shape of HR data, one three times larger. Every list
endpoint is called as each school's admin and the SQL statements are counted. The count must
not grow with the number of rows.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event

from src.db.models import (
    Department,
    Employee,
    EmployeeDocument,
    HrLetter,
    HrRequest,
    HrRequestEvent,
    HrWorkflow,
    HrWorkflowTask,
    Notification,
    PayrollPayslip,
    PayrollRun,
    StaffAttendance,
    StaffLeaveRequest,
)
from src.shared.enums import UserRole
from tests.integration.conftest import _actor, _school

TODAY = date.today()
MONTH = TODAY.strftime("%Y-%m")

ENDPOINTS = [
    "/api/v1/hr/employees",
    "/api/v1/hr/departments",
    "/api/v1/hr/designations",
    f"/api/v1/hr/attendance/daily?date={TODAY.isoformat()}",
    f"/api/v1/hr/attendance?month={MONTH}",
    f"/api/v1/hr/attendance/summary?month={MONTH}",
    "/api/v1/hr/leave/requests",
    "/api/v1/hr/letters",
    "/api/v1/hr/files",
    "/api/v1/hr/files/checklist",
    "/api/v1/hr/requests",
    "/api/v1/hr/workflows",
    "/api/v1/hr/payroll/runs",
    "/api/v1/hr/payroll/payslips",
    f"/api/v1/hr/dashboard?as_of={TODAY.isoformat()}",
    "/api/v1/communication/notifications?scope=hr",
]

# Fixed per-request overhead (auth, settings, sync) may differ slightly; growth may not.
SLACK = 2


async def _seed(db, label: str, size: int):
    school = await _school(db, label)
    admin = await _actor(db, school, UserRole.SCHOOL_ADMIN)
    dept = Department(id=uuid4(), school_id=school.id, name=f"Dept {label}")
    db.add(dept)
    await db.flush()
    run = PayrollRun(
        id=uuid4(),
        school_id=school.id,
        label=f"Run {label}",
        period_start=TODAY.replace(day=1),
        period_end=TODAY.replace(day=1) + timedelta(days=27),
        created_by=admin.user_id,
    )
    db.add(run)
    await db.flush()

    for i in range(size):
        teacher = await _actor(db, school, UserRole.TEACHER, teacher=True)
        emp = Employee(
            id=uuid4(),
            school_id=school.id,
            staff_type="TEACHING",
            teacher_id=teacher.teacher_id,
            user_id=teacher.user_id,
            department_id=dept.id,
        )
        staff = Employee(
            id=uuid4(),
            school_id=school.id,
            staff_type="NON_TEACHING",
            full_name=f"Staff {label} {i}",
            employee_code=f"NT-{label}-{i}",
            department_id=dept.id,
        )
        db.add_all([emp, staff])
        await db.flush()
        for e in (emp, staff):
            db.add(StaffAttendance(school_id=school.id, employee_id=e.id, attendance_date=TODAY, status="PRESENT"))
            db.add(
                HrLetter(
                    school_id=school.id, employee_id=e.id, letter_type="OTHER", title="Letter", created_by=admin.user_id
                )
            )
            db.add(
                EmployeeDocument(
                    school_id=school.id,
                    employee_id=e.id,
                    category="ID_PROOF",
                    title="ID",
                    file_name="id.pdf",
                    file_size=10,
                    storage_key=f"qb/{uuid4().hex}",
                    uploaded_by=admin.user_id,
                )
            )
            wf = HrWorkflow(
                id=uuid4(),
                school_id=school.id,
                employee_id=e.id,
                workflow_type="ONBOARDING",
                start_date=TODAY,
                owner_user_id=admin.user_id,
                created_by=admin.user_id,
            )
            req = HrRequest(
                id=uuid4(), school_id=school.id, employee_id=e.id, request_type="HR_QUERY", subject="Question"
            )
            db.add_all([wf, req])
            await db.flush()
            db.add_all(
                [
                    HrWorkflowTask(school_id=school.id, workflow_id=wf.id, title="Task 1", sort_order=0),
                    HrWorkflowTask(
                        school_id=school.id, workflow_id=wf.id, title="Task 2", sort_order=1, assignee_user_id=admin.user_id
                    ),
                    HrRequestEvent(
                        school_id=school.id, request_id=req.id, event_type="SUBMITTED", actor_user_id=admin.user_id
                    ),
                ]
            )
        db.add(
            StaffLeaveRequest(
                school_id=school.id,
                user_id=teacher.user_id,
                start_date=TODAY + timedelta(days=10 + i),
                end_date=TODAY + timedelta(days=10 + i),
                reason="Personal",
                decided_by=admin.user_id if i % 2 else None,
                status="APPROVED" if i % 2 else "PENDING",
            )
        )
        db.add(
            PayrollPayslip(
                school_id=school.id,
                payroll_run_id=run.id,
                teacher_id=teacher.teacher_id,
                gross_amount=Decimal("1000.00"),
                net_amount=Decimal("1000.00"),
            )
        )
        db.add(
            Notification(
                school_id=school.id,
                user_id=admin.user_id,
                title="Leave",
                body="Leave submitted",
                category="LEAVE_SUBMITTED",
                entity_type="hr_leave",
            )
        )
        await db.flush()
    return admin


async def _count(api, db, path: str, headers: dict[str, str]) -> int:
    statements: list[str] = []

    def _record(conn, cursor, statement, *args):
        statements.append(statement)

    engine = db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        r = await api.get(path, headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    assert r.status_code == 200, (path, r.status_code, r.text[:300])
    return len(statements)


@pytest.mark.parametrize("path", ENDPOINTS)
async def test_list_query_count_does_not_grow_with_rows(api, db_session, path):
    small = await _seed(db_session, "QS", 2)
    large = await _seed(db_session, "QL", 6)

    # Warm-up: the first call may lazily create per-school rows (employee sync) once.
    await _count(api, db_session, path, small.headers)
    await _count(api, db_session, path, large.headers)

    n_small = await _count(api, db_session, path, small.headers)
    n_large = await _count(api, db_session, path, large.headers)
    assert n_large <= n_small + SLACK, f"{path}: {n_small} queries for 2 teachers vs {n_large} for 6"
