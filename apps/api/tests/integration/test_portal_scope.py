"""Teacher, student, and parent portals only see their own classes and children."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import AsyncSessionLocal
from src.core.security import create_access_token
from src.db.models import (
    AcademicYear,
    ClassRoom,
    Enrollment,
    Exam,
    ExamSection,
    Grade,
    Parent,
    School,
    SchoolMembership,
    Student,
    StudentParent,
    Subject,
    Teacher,
    TeacherAssignment,
    User,
)
from src.shared.enums import UserRole

API = "/api/v1"


@dataclass
class Actor:
    user_id: UUID
    token: str
    teacher_id: UUID | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class Campus:
    school_id: UUID
    teacher_a: Actor
    teacher_b: Actor
    student_a: Actor
    student_b: Actor
    parent: Actor
    class_a: UUID
    class_b: UUID
    subject_id: UUID
    student_a_id: UUID
    student_b_id: UUID
    exam_b: UUID


async def _user(db: AsyncSession, school: School, role: UserRole, *, teacher: bool = False) -> tuple[Actor, User]:
    user = User(id=uuid4(), email=f"portal-{uuid4().hex[:12]}@example.test", full_name=f"Portal {role.value}")
    db.add(user)
    await db.flush()
    db.add(SchoolMembership(user_id=user.id, school_id=school.id, role=role.value))
    teacher_id = None
    if teacher:
        row = Teacher(id=uuid4(), school_id=school.id, user_id=user.id, teacher_code=f"P-{uuid4().hex[:6]}")
        db.add(row)
        await db.flush()
        teacher_id = row.id
    token = create_access_token(subject=user.id, role=role.value, school_id=school.id)
    return Actor(user_id=user.id, token=token, teacher_id=teacher_id), user


@pytest.fixture
async def campus(db_session: AsyncSession) -> Campus:
    tag = uuid4().hex[:8]
    school = School(id=uuid4(), name=f"Portal {tag}", slug=f"portal-{tag}")
    db_session.add(school)
    await db_session.flush()
    year = AcademicYear(
        id=uuid4(),
        school_id=school.id,
        name=f"2026-27-{tag}",
        starts_on=date(2026, 4, 1),
        ends_on=date(2027, 3, 31),
        is_current=True,
    )
    grade = Grade(id=uuid4(), school_id=school.id, name=f"Class 5 {tag}", level=5)
    db_session.add_all([year, grade])
    await db_session.flush()
    class_a = ClassRoom(
        id=uuid4(), school_id=school.id, grade_id=grade.id, academic_year_id=year.id, name=f"5-A-{tag}", section="A"
    )
    class_b = ClassRoom(
        id=uuid4(), school_id=school.id, grade_id=grade.id, academic_year_id=year.id, name=f"5-B-{tag}", section="B"
    )
    subject = Subject(id=uuid4(), school_id=school.id, name="Maths", code=f"M{tag}")
    db_session.add_all([class_a, class_b, subject])
    await db_session.flush()

    teacher_a, _ = await _user(db_session, school, UserRole.TEACHER, teacher=True)
    teacher_b, _ = await _user(db_session, school, UserRole.TEACHER, teacher=True)
    student_a, student_a_user = await _user(db_session, school, UserRole.STUDENT)
    student_b, student_b_user = await _user(db_session, school, UserRole.STUDENT)
    parent, parent_user = await _user(db_session, school, UserRole.PARENT)

    pupil_a = Student(id=uuid4(), school_id=school.id, user_id=student_a_user.id, status="ACTIVE")
    pupil_b = Student(id=uuid4(), school_id=school.id, user_id=student_b_user.id, status="ACTIVE")
    parent_row = Parent(id=uuid4(), school_id=school.id, user_id=parent_user.id, status="ACTIVE")
    db_session.add_all([pupil_a, pupil_b, parent_row])
    await db_session.flush()
    db_session.add(
        StudentParent(
            id=uuid4(),
            school_id=school.id,
            student_id=pupil_a.id,
            parent_id=parent_row.id,
            relation_type="mother",
            is_primary=True,
        )
    )
    for pupil, classroom, roll in ((pupil_a, class_a, "1"), (pupil_b, class_b, "1")):
        db_session.add(
            Enrollment(
                id=uuid4(),
                school_id=school.id,
                student_id=pupil.id,
                academic_year_id=year.id,
                grade_id=grade.id,
                class_id=classroom.id,
                roll_number=roll,
                status="ACTIVE",
            )
        )
    db_session.add(
        TeacherAssignment(
            id=uuid4(),
            school_id=school.id,
            academic_year_id=year.id,
            teacher_id=teacher_a.teacher_id,
            subject_id=subject.id,
            class_id=class_a.id,
        )
    )
    db_session.add(
        TeacherAssignment(
            id=uuid4(),
            school_id=school.id,
            academic_year_id=year.id,
            teacher_id=teacher_b.teacher_id,
            subject_id=subject.id,
            class_id=class_b.id,
        )
    )
    exam = Exam(id=uuid4(), school_id=school.id, academic_year_id=year.id, grade_id=grade.id, title="Term 1 B")
    db_session.add(exam)
    await db_session.flush()
    db_session.add(ExamSection(id=uuid4(), school_id=school.id, exam_id=exam.id, class_id=class_b.id))
    await db_session.flush()
    return Campus(
        school_id=school.id,
        teacher_a=teacher_a,
        teacher_b=teacher_b,
        student_a=student_a,
        student_b=student_b,
        parent=parent,
        class_a=class_a.id,
        class_b=class_b.id,
        subject_id=subject.id,
        student_a_id=pupil_a.id,
        student_b_id=pupil_b.id,
        exam_b=exam.id,
    )


def _day() -> str:
    return date.today().isoformat()


async def test_teacher_attendance_and_timetable_stay_in_own_classes(api, campus: Campus):
    own = await api.get(
        f"{API}/attendance/roster",
        params={"class_id": str(campus.class_a), "attended_on": _day()},
        headers=campus.teacher_a.headers,
    )
    other = await api.get(
        f"{API}/attendance/roster",
        params={"class_id": str(campus.class_b), "attended_on": _day()},
        headers=campus.teacher_a.headers,
    )
    assert own.status_code == 200, own.text
    assert other.status_code == 403, other.text

    own_history = await api.get(
        f"{API}/attendance/student/{campus.student_a_id}", headers=campus.teacher_a.headers
    )
    other_history = await api.get(
        f"{API}/attendance/student/{campus.student_b_id}", headers=campus.teacher_a.headers
    )
    assert own_history.status_code == 200, own_history.text
    assert other_history.status_code == 403, other_history.text

    own_grid = await api.get(
        f"{API}/timetable/grid", params={"class_id": str(campus.class_a)}, headers=campus.teacher_a.headers
    )
    other_grid = await api.get(
        f"{API}/timetable/grid", params={"class_id": str(campus.class_b)}, headers=campus.teacher_a.headers
    )
    assert own_grid.status_code == 200, own_grid.text
    assert other_grid.status_code == 403, other_grid.text


async def test_teacher_exams_and_results_hide_other_classes(api, campus: Campus):
    listed = await api.get(f"{API}/exams", headers=campus.teacher_a.headers)
    assert listed.status_code == 200, listed.text
    assert str(campus.exam_b) not in {row["id"] for row in listed.json()}

    hidden = await api.get(f"{API}/exams/{campus.exam_b}", headers=campus.teacher_a.headers)
    schedule = await api.get(f"{API}/exams/{campus.exam_b}/schedule", headers=campus.teacher_a.headers)
    visible = await api.get(f"{API}/exams/{campus.exam_b}", headers=campus.teacher_b.headers)
    assert hidden.status_code == 404, hidden.text
    assert schedule.status_code == 404, schedule.text
    assert visible.status_code == 200, visible.text

    other_results = await api.get(
        f"{API}/results/student/{campus.student_b_id}", headers=campus.teacher_a.headers
    )
    own_results = await api.get(
        f"{API}/results/student/{campus.student_a_id}", headers=campus.teacher_a.headers
    )
    other_card = await api.get(
        f"{API}/results/{campus.exam_b}/report-card",
        params={"student_id": str(campus.student_b_id)},
        headers=campus.teacher_a.headers,
    )
    assert other_results.status_code == 403, other_results.text
    assert own_results.status_code == 200, own_results.text
    assert other_card.status_code == 403, other_card.text


async def test_student_and_parent_cannot_read_another_child(api, campus: Campus):
    student = await api.get(
        f"{API}/attendance/student/{campus.student_b_id}", headers=campus.student_a.headers
    )
    parent = await api.get(
        f"{API}/attendance/student/{campus.student_b_id}", headers=campus.parent.headers
    )
    own = await api.get(
        f"{API}/attendance/student/{campus.student_a_id}", headers=campus.parent.headers
    )
    fees_other = await api.get(f"{API}/fees/me/children/{campus.student_b_id}", headers=campus.parent.headers)
    fees_own = await api.get(f"{API}/fees/me/children/{campus.student_a_id}", headers=campus.parent.headers)
    assert student.status_code == 403, student.text
    assert parent.status_code == 403, parent.text
    assert own.status_code == 200, own.text
    assert fees_other.status_code == 403, fees_other.text
    assert fees_own.status_code == 200, fees_own.text
    assert fees_own.json()["student_id"] == str(campus.student_a_id)


async def test_messages_follow_class_relationships(api, campus: Campus):
    classmate = await api.post(
        f"{API}/messaging",
        json={"recipient_id": str(campus.student_b.user_id), "body": "hello"},
        headers=campus.student_a.headers,
    )
    other_teacher = await api.post(
        f"{API}/messaging",
        json={"recipient_id": str(campus.teacher_b.user_id), "body": "hello"},
        headers=campus.student_a.headers,
    )
    own_teacher = await api.post(
        f"{API}/messaging",
        json={"recipient_id": str(campus.teacher_a.user_id), "body": "hello"},
        headers=campus.student_a.headers,
    )
    assert classmate.status_code == 403, classmate.text
    assert other_teacher.status_code == 403, other_teacher.text
    assert own_teacher.status_code == 201, own_teacher.text


async def test_learning_sessions_are_student_self_and_class_teacher(api, campus: Campus):
    started = await api.post(
        f"{API}/learning-sessions",
        json={"subject_id": str(campus.subject_id), "mode": "board"},
        headers=campus.student_a.headers,
    )
    assert started.status_code == 201, started.text
    session_id = started.json()["id"]
    assert started.json()["student_id"] == str(campus.student_a_id)

    parent = await api.post(
        f"{API}/learning-sessions", json={"mode": "board"}, headers=campus.parent.headers
    )
    assert parent.status_code == 403, parent.text

    mine = await api.get(f"{API}/learning-sessions/me", headers=campus.student_a.headers)
    other_student = await api.get(f"{API}/learning-sessions", headers=campus.student_b.headers)
    teacher = await api.get(f"{API}/learning-sessions", headers=campus.teacher_a.headers)
    other_teacher = await api.get(f"{API}/learning-sessions", headers=campus.teacher_b.headers)
    assert mine.status_code == 200 and session_id in {row["id"] for row in mine.json()}
    assert other_student.status_code == 200 and session_id not in {row["id"] for row in other_student.json()}
    assert teacher.status_code == 200 and session_id in {row["id"] for row in teacher.json()}
    assert other_teacher.status_code == 200 and session_id not in {row["id"] for row in other_teacher.json()}


async def test_assignments_are_typed_and_class_scoped(api, campus: Campus):
    created = await api.post(
        f"{API}/assignments",
        json={
            "class_id": str(campus.class_a),
            "subject_id": str(campus.subject_id),
            "title": "Fractions worksheet",
            "status": "PUBLISHED",
            "homework_type": "HOMEWORK",
        },
        headers=campus.teacher_a.headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["homework_type"] == "ASSIGNMENT"
    assignment_id = body["id"]

    wrong_class = await api.post(
        f"{API}/assignments",
        json={
            "class_id": str(campus.class_b),
            "subject_id": str(campus.subject_id),
            "title": "Not my class",
            "status": "PUBLISHED",
        },
        headers=campus.teacher_a.headers,
    )
    assert wrong_class.status_code == 403, wrong_class.text

    mine = await api.get(f"{API}/assignments/mine", headers=campus.student_a.headers)
    other = await api.get(f"{API}/assignments/mine", headers=campus.student_b.headers)
    child = await api.get(f"{API}/assignments/children/{campus.student_a_id}", headers=campus.parent.headers)
    other_child = await api.get(
        f"{API}/assignments/children/{campus.student_b_id}", headers=campus.parent.headers
    )
    teacher_b = await api.get(f"{API}/assignments/{assignment_id}", headers=campus.teacher_b.headers)
    assert mine.status_code == 200 and assignment_id in {row["id"] for row in mine.json()}
    assert other.status_code == 200 and assignment_id not in {row["id"] for row in other.json()}
    assert child.status_code == 200 and assignment_id in {row["id"] for row in child.json()}
    assert other_child.status_code == 403, other_child.text
    assert teacher_b.status_code == 403, teacher_b.text


async def test_announcements_stay_inside_the_teachers_class(api, campus: Campus):
    denied = await api.post(
        f"{API}/announcements",
        json={"title": "Wrong room", "body": "This is not your class", "class_id": str(campus.class_b)},
        headers=campus.teacher_a.headers,
    )
    assert denied.status_code == 403, denied.text

    created = await api.post(
        f"{API}/announcements",
        json={"title": "Bring compass", "body": "Geometry tomorrow", "class_id": str(campus.class_a)},
        headers=campus.teacher_a.headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["is_published"] is True
    assert created.json()["status"] == "PUBLISHED"
    announcement_id = created.json()["id"]

    student_a = await api.get(f"{API}/announcements/{announcement_id}", headers=campus.student_a.headers)
    student_b = await api.get(f"{API}/announcements/{announcement_id}", headers=campus.student_b.headers)
    parent = await api.get(f"{API}/announcements/{announcement_id}", headers=campus.parent.headers)
    assert student_a.status_code == 200, student_a.text
    assert student_b.status_code == 404, student_b.text
    assert parent.status_code == 200, parent.text

    wrong = await api.post(
        f"{API}/communication/announcements/teacher",
        json={"class_id": str(campus.class_b), "title": "Nope", "content": "Not this section"},
        headers=campus.teacher_a.headers,
    )
    posted = await api.post(
        f"{API}/communication/announcements/teacher",
        json={"class_id": str(campus.class_a), "title": "PTM", "content": "Friday 4pm"},
        headers=campus.teacher_a.headers,
    )
    assert wrong.status_code == 403, wrong.text
    assert posted.status_code == 201, posted.text
    notice_id = posted.json()["id"]
    seen = await api.get(f"{API}/communication/announcements/{notice_id}", headers=campus.student_a.headers)
    hidden = await api.get(f"{API}/communication/announcements/{notice_id}", headers=campus.student_b.headers)
    parent_seen = await api.get(
        f"{API}/communication/announcements/{notice_id}", headers=campus.parent.headers
    )
    assert seen.status_code == 200, seen.text
    assert hidden.status_code == 404, hidden.text
    assert parent_seen.status_code == 200, parent_seen.text


async def test_teacher_can_open_own_payslips_and_attendance(api, campus: Campus):
    slips = await api.get(f"{API}/payroll/payslips/me", headers=campus.teacher_a.headers)
    sessions = await api.get(
        f"{API}/hr/attendance/sessions/me",
        params={"date_from": "2026-04-01", "date_to": "2026-04-30"},
        headers=campus.teacher_a.headers,
    )
    assert slips.status_code == 200, slips.text
    assert slips.json() == []
    assert sessions.status_code == 200, sessions.text
    assert sessions.json() == []


@pytest.fixture
async def _dispose_app_pool():
    yield
    from src.core.database import engine

    await engine.dispose()


async def test_teacher_rls_sees_only_own_employee_row(_dispose_app_pool):
    """Under sybeez_app, a teacher can insert and read only their own staff row."""
    school_ids: list = []
    user_ids: list = []
    try:
        async with AsyncSessionLocal() as db:
            try:
                await db.execute(text("SELECT 1"))
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"PostgreSQL not reachable: {exc}")
            role = (await db.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_app'"))).scalar()
            if role is None:
                pytest.skip("sybeez_app role is not installed")
            school = School(id=uuid4(), name="RLS Staff", slug=f"rls-staff-{uuid4().hex[:10]}")
            owner = User(id=uuid4(), email=f"{uuid4().hex}@rls.test", full_name="Teacher A", is_active=True)
            other = User(id=uuid4(), email=f"{uuid4().hex}@rls.test", full_name="Teacher B", is_active=True)
            db.add_all([school, owner, other])
            await db.flush()
            school_ids = [school.id]
            user_ids = [owner.id, other.id]
            teacher = Teacher(id=uuid4(), school_id=school.id, user_id=owner.id, status="ACTIVE")
            colleague = Teacher(id=uuid4(), school_id=school.id, user_id=other.id, status="ACTIVE")
            db.add_all([teacher, colleague])
            await db.flush()
            colleague_employee = uuid4()
            await db.execute(
                text(
                    "INSERT INTO employees (id, school_id, staff_type, teacher_id, user_id, status) "
                    "VALUES (:id, :sid, 'TEACHING', :tid, :uid, 'ACTIVE')"
                ),
                {"id": colleague_employee, "sid": school.id, "tid": colleague.id, "uid": other.id},
            )
            await db.commit()
            ids = {"school": school.id, "owner": owner.id, "teacher": teacher.id, "colleague_user": other.id}

        async with AsyncSessionLocal() as db:
            await db.execute(text("SET ROLE sybeez_app"))
            await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
            await db.execute(text("SELECT set_config('app.school_id', :v, false)"), {"v": str(ids["school"])})
            await db.execute(text("SELECT set_config('app.user_id', :v, false)"), {"v": str(ids["owner"])})
            await db.execute(text("SELECT set_config('app.role', 'TEACHER', false)"))
            own_employee = uuid4()
            await db.execute(
                text(
                    "INSERT INTO employees (id, school_id, staff_type, teacher_id, user_id, status) "
                    "VALUES (:id, :sid, 'TEACHING', :tid, :uid, 'ACTIVE')"
                ),
                {"id": own_employee, "sid": ids["school"], "tid": ids["teacher"], "uid": ids["owner"]},
            )
            visible = set((await db.execute(text("SELECT user_id FROM employees"))).scalars().all())
            assert visible == {ids["owner"]}
            with pytest.raises(DBAPIError, match="row-level security"):
                await db.execute(
                    text(
                        "INSERT INTO employees (id, school_id, staff_type, user_id, full_name, status) "
                        "VALUES (:id, :sid, 'NON_TEACHING', :uid, 'Someone else', 'ACTIVE')"
                    ),
                    {"id": uuid4(), "sid": ids["school"], "uid": ids["colleague_user"]},
                )
            await db.rollback()
            await db.execute(text("RESET ROLE"))
            await db.commit()
    finally:
        if school_ids or user_ids:
            async with AsyncSessionLocal() as db:
                await db.execute(text("RESET ROLE"))
                await db.execute(text("SET session_replication_role = replica"))
                for school_id in school_ids:
                    await db.execute(text("DELETE FROM schools WHERE id = :id"), {"id": school_id})
                for user_id in user_ids:
                    await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
                await db.execute(text("SET session_replication_role = origin"))
                await db.commit()
