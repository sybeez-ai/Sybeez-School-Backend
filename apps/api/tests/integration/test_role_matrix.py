"""Each role can call only its endpoints, and Postgres hides the other rows."""

import uuid
from datetime import date

import psycopg2
import pytest
from fastapi.testclient import TestClient

from src.core.config import get_settings
from src.core.security import hash_password
from src.main import app
from src.modules.access.catalog import permissions_for
from src.shared.enums import UserRole

DSN = "postgresql://sybeez:sybeez@127.0.0.1:5432/sybeez_school"
PASSWORD = "Testpass1"


def _auth(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def _ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    body = response.json()
    items = body["items"] if isinstance(body, dict) else body
    return [item["id"] for item in items]


def _count(response) -> int:
    assert response.status_code == 200, response.text
    body = response.json()
    if isinstance(body, list):
        return len(body)
    return int(body["total"])


def _rls_count(
    table: str,
    *,
    user_id: str,
    school_id: str,
    role: str,
    membership_id: str | None,
    portals: str = "",
) -> int:
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
        cur.execute("SELECT set_config('app.school_id', %s, true)", (school_id,))
        cur.execute("SELECT set_config('app.role', %s, true)", (role,))
        cur.execute("SELECT set_config('app.membership_id', %s, true)", (membership_id or "",))
        cur.execute("SELECT set_config('app.lookup_email', '', true)")
        cur.execute("SELECT set_config('app.portals', %s, true)", (portals,))
        cur.execute("SET LOCAL ROLE sybeez_app")
        cur.execute(f"SELECT count(*) FROM {table}")
        return int(cur.fetchone()[0])
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture(scope="module")
def client():
    with TestClient(app, headers={"Origin": "http://testserver"}) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def world(client: TestClient):
    suffix = uuid.uuid4().hex[:8]
    platform_email = get_settings().superadmin_email
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET password_hash = %s, full_name = 'Platform Admin', status = 'ACTIVE',
            is_active = true, is_platform_admin = true
        WHERE email = %s
        """,
        (hash_password(PASSWORD), platform_email),
    )
    if cur.rowcount == 0:
        cur.execute(
            """
            INSERT INTO users (id, email, password_hash, full_name, status, is_active, is_platform_admin)
            VALUES (%s, %s, %s, 'Platform Admin', 'ACTIVE', true, true)
            """,
            (str(uuid.uuid4()), platform_email, hash_password(PASSWORD)),
        )
    conn.close()

    platform = _auth(client, platform_email)
    schools = {}
    for slug, admin_name in (("north", "North Admin"), ("south", "South Admin")):
        created = client.post(
            "/api/v1/platform/schools",
            headers=platform,
            json={
                "name": f"{slug.title()} School {suffix}",
                "slug": f"{slug}-{suffix}",
                "admin": {
                    "full_name": admin_name,
                    "email": f"{slug}-admin-{suffix}@example.com",
                    "password": PASSWORD,
                },
            },
        )
        assert created.status_code == 201, created.text
        schools[slug] = created.json()

    admin = _auth(client, f"north-admin-{suffix}@example.com")
    year = client.post(
        "/api/v1/academic-years",
        headers=admin,
        json={"name": "2026", "starts_on": "2026-01-01", "ends_on": "2026-12-31", "is_current": True},
    )
    assert year.status_code == 201, year.text
    grade = client.post("/api/v1/grades", headers=admin, json={"name": "Grade 6", "level": 6})
    assert grade.status_code == 201, grade.text
    classroom = client.post(
        "/api/v1/classes",
        headers=admin,
        json={
            "name": "6A",
            "grade_id": grade.json()["id"],
            "academic_year_id": year.json()["id"],
        },
    )
    assert classroom.status_code == 201, classroom.text
    subject = client.post("/api/v1/subjects", headers=admin, json={"name": "Mathematics", "code": f"MATH-{suffix}"})
    assert subject.status_code == 201, subject.text

    teacher = client.post(
        "/api/v1/teachers",
        headers=admin,
        json={"full_name": "Ada Teacher", "email": f"teacher-{suffix}@example.com", "password": PASSWORD},
    )
    assert teacher.status_code == 201, teacher.text
    assigned = client.post(
        "/api/v1/teacher-assignments",
        headers=admin,
        json={
            "teacher_id": teacher.json()["id"],
            "subject_id": subject.json()["id"],
            "class_id": classroom.json()["id"],
            "academic_year_id": year.json()["id"],
        },
    )
    assert assigned.status_code == 201, assigned.text

    student_a = client.post(
        "/api/v1/students",
        headers=admin,
        json={
            "first_name": "Student",
            "last_name": "A",
            "email": f"student-a-{suffix}@example.com",
            "password": PASSWORD,
            "class_id": classroom.json()["id"],
            "academic_year_id": year.json()["id"],
        },
    )
    assert student_a.status_code == 201, student_a.text
    student_b = client.post(
        "/api/v1/students",
        headers=admin,
        json={
            "first_name": "Student",
            "last_name": "B",
            "email": f"student-b-{suffix}@example.com",
            "password": PASSWORD,
        },
    )
    assert student_b.status_code == 201, student_b.text
    parent = client.post(
        "/api/v1/parents",
        headers=admin,
        json={
            "full_name": "Parent A",
            "email": f"parent-{suffix}@example.com",
            "password": PASSWORD,
            "student_id": student_a.json()["id"],
        },
    )
    assert parent.status_code == 201, parent.text

    staff = {}
    for role, name in (("FINANCE", "Fin"), ("HR", "Hari"), ("PAYROLL_APPROVER", "Pay")):
        granted = client.post(
            "/api/v1/access/role-grants",
            headers=admin,
            json={
                "email": f"{role.lower()}-{suffix}@example.com",
                "full_name": name,
                "password": PASSWORD,
                "role": role,
            },
        )
        assert granted.status_code == 201, granted.text
        staff[role] = granted.json()

    south_student = client.post(
        "/api/v1/students",
        headers=_auth(client, f"south-admin-{suffix}@example.com"),
        json={
            "first_name": "South",
            "last_name": "Student",
            "email": f"south-student-{suffix}@example.com",
            "password": PASSWORD,
        },
    )
    assert south_student.status_code == 201, south_student.text

    teacher_headers = _auth(client, f"teacher-{suffix}@example.com")
    homework = client.post(
        "/api/v1/homework",
        headers=teacher_headers,
        json={
            "class_id": classroom.json()["id"],
            "subject_id": subject.json()["id"],
            "teacher_id": teacher.json()["id"],
            "title": "Fractions",
            "status": "PUBLISHED",
            "allow_resubmission": False,
        },
    )
    assert homework.status_code == 201, homework.text
    exam = client.post(
        "/api/v1/exams",
        headers=admin,
        json={
            "title": "Midterm",
            "academic_year_id": year.json()["id"],
            "class_ids": [classroom.json()["id"]],
        },
    )
    assert exam.status_code == 201, exam.text
    slot = client.post(
        "/api/v1/timetable",
        headers=admin,
        json={
            "class_id": classroom.json()["id"],
            "subject_id": subject.json()["id"],
            "teacher_id": teacher.json()["id"],
            "day_of_week": 1,
            "starts_at": "09:00:00",
            "ends_at": "09:45:00",
        },
    )
    assert slot.status_code == 201, slot.text
    announcement = client.post(
        "/api/v1/announcements",
        headers=admin,
        json={"title": "Welcome", "body": "Term starts Monday", "is_published": True},
    )
    assert announcement.status_code == 201, announcement.text
    class_note = client.post(
        "/api/v1/announcements",
        headers=admin,
        json={
            "title": "6A only",
            "body": "Bring a calculator",
            "class_id": classroom.json()["id"],
            "is_published": True,
        },
    )
    assert class_note.status_code == 201, class_note.text

    marked = client.post(
        "/api/v1/attendance/mark",
        headers=teacher_headers,
        json={
            "class_id": classroom.json()["id"],
            "attended_on": date.today().isoformat(),
            "marks": [{"student_id": student_a.json()["id"], "status": "PRESENT"}],
        },
    )
    assert marked.status_code == 200, marked.text
    result = client.post(
        "/api/v1/results",
        headers=teacher_headers,
        json={"exam_id": exam.json()["id"], "student_id": student_a.json()["id"], "score": 95},
    )
    assert result.status_code == 201, result.text
    assert result.json()["grade_label"] == "A"

    roles = {
        "PLATFORM_ADMIN": platform,
        "SCHOOL_ADMIN": admin,
        "TEACHER": teacher_headers,
        "STUDENT": _auth(client, f"student-a-{suffix}@example.com"),
        "STUDENT_B": _auth(client, f"student-b-{suffix}@example.com"),
        "PARENT": _auth(client, f"parent-{suffix}@example.com"),
        "FINANCE": _auth(client, f"finance-{suffix}@example.com"),
        "HR": _auth(client, f"hr-{suffix}@example.com"),
        "PAYROLL_APPROVER": _auth(client, f"payroll_approver-{suffix}@example.com"),
        "SOUTH_ADMIN": _auth(client, f"south-admin-{suffix}@example.com"),
    }
    client.cookies.clear()
    return {
        "suffix": suffix,
        "schools": schools,
        "year": year.json(),
        "grade": grade.json(),
        "classroom": classroom.json(),
        "subject": subject.json(),
        "teacher": teacher.json(),
        "student_a": student_a.json(),
        "student_b": student_b.json(),
        "parent": parent.json(),
        "staff": staff,
        "homework": homework.json(),
        "exam": exam.json(),
        "result": result.json(),
        "attendance": marked.json()[0],
        "roles": roles,
    }


def test_live_permissions_match_the_catalog(client: TestClient, world):
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    for role in (
        UserRole.SCHOOL_ADMIN,
        UserRole.TEACHER,
        UserRole.STUDENT,
        UserRole.PARENT,
        UserRole.FINANCE,
        UserRole.HR,
        UserRole.PAYROLL_APPROVER,
    ):
        me = client.get("/api/v1/access/me", headers=world["roles"][role.value])
        assert me.status_code == 200, me.text
        assert set(me.json()["permissions"]) == set(permissions_for(role))
        cur.execute("SELECT permission_code FROM role_permissions WHERE role = %s", (role.value,))
        assert {row[0] for row in cur.fetchall()} == set(permissions_for(role))
    conn.close()
    platform = client.get("/api/v1/access/me", headers=world["roles"]["PLATFORM_ADMIN"])
    assert platform.status_code == 200
    assert "platform.manage" in platform.json()["permissions"]


@pytest.mark.parametrize(
    ("role", "path", "expected"),
    [
        ("SCHOOL_ADMIN", "/api/v1/students", 200),
        ("TEACHER", "/api/v1/students", 200),
        ("STUDENT", "/api/v1/students", 200),
        ("PARENT", "/api/v1/students", 200),
        ("FINANCE", "/api/v1/students", 200),
        ("HR", "/api/v1/students", 403),
        ("PAYROLL_APPROVER", "/api/v1/students", 403),
        ("FINANCE", "/api/v1/teachers", 403),
        ("HR", "/api/v1/teachers", 200),
        ("PAYROLL_APPROVER", "/api/v1/teachers", 200),
        ("TEACHER", "/api/v1/attendance", 200),
        ("STUDENT", "/api/v1/attendance", 200),
        ("PARENT", "/api/v1/attendance", 200),
        ("FINANCE", "/api/v1/attendance", 403),
        ("HR", "/api/v1/attendance", 403),
        ("PAYROLL_APPROVER", "/api/v1/results", 403),
        ("FINANCE", "/api/v1/results", 403),
        ("TEACHER", "/api/v1/results", 200),
        ("STUDENT", "/api/v1/homework", 200),
        ("FINANCE", "/api/v1/homework", 403),
        ("HR", "/api/v1/exams", 403),
        ("TEACHER", "/api/v1/exams", 200),
        ("PARENT", "/api/v1/timetable", 200),
        ("PAYROLL_APPROVER", "/api/v1/timetable", 403),
        ("FINANCE", "/api/v1/announcements", 200),
        ("TEACHER", "/api/v1/audit", 403),
        ("SCHOOL_ADMIN", "/api/v1/audit", 200),
        ("SCHOOL_ADMIN", "/api/v1/platform/schools", 403),
        ("PLATFORM_ADMIN", "/api/v1/platform/schools", 200),
        ("STUDENT", "/api/v1/access/matrix", 403),
        ("SCHOOL_ADMIN", "/api/v1/access/matrix", 200),
    ],
)
def test_role_http_matrix(client: TestClient, world, role, path, expected):
    response = client.get(path, headers=world["roles"][role])
    assert response.status_code == expected, response.text


def test_anonymous_requests_are_rejected(client: TestClient):
    client.cookies.clear()
    assert client.get("/api/v1/students").status_code == 401
    assert client.get("/health").status_code == 200


def test_visibility_follows_child_self_and_class(client: TestClient, world):
    student_a = world["student_a"]["id"]
    student_b = world["student_b"]["id"]

    own = client.get("/api/v1/students", headers=world["roles"]["STUDENT"])
    assert _ids(own) == [student_a]
    hidden = client.get(f"/api/v1/students/{student_b}", headers=world["roles"]["STUDENT"])
    assert hidden.status_code == 404

    other = client.get("/api/v1/students", headers=world["roles"]["STUDENT_B"])
    assert _ids(other) == [student_b]
    assert _count(client.get("/api/v1/homework", headers=world["roles"]["STUDENT_B"])) == 0
    assert _count(client.get("/api/v1/timetable", headers=world["roles"]["STUDENT_B"])) == 0
    assert _count(client.get("/api/v1/attendance", headers=world["roles"]["STUDENT_B"])) == 0

    children = client.get("/api/v1/parents/me/children", headers=world["roles"]["PARENT"])
    assert children.status_code == 200, children.text
    assert [row["student_id"] for row in children.json()] == [student_a]
    assert _ids(client.get("/api/v1/students", headers=world["roles"]["TEACHER"])) == [student_a]
    assert set(_ids(client.get("/api/v1/students", headers=world["roles"]["FINANCE"]))) == {student_a, student_b}
    assert _ids(client.get("/api/v1/attendance", headers=world["roles"]["PARENT"])) == [world["attendance"]["id"]]
    assert client.get("/api/v1/results", headers=world["roles"]["PARENT"]).json()["items"][0]["grade_label"] == "A"

    notes = client.get("/api/v1/announcements", headers=world["roles"]["FINANCE"])
    titles = {item["title"] for item in notes.json()["items"]}
    assert "Welcome" in titles
    assert "6A only" not in titles
    class_notes = client.get("/api/v1/announcements", headers=world["roles"]["STUDENT"])
    assert {"Welcome", "6A only"} <= {item["title"] for item in class_notes.json()["items"]}


def test_postgres_rls_row_counts(world):
    school_id = world["schools"]["north"]["id"]
    profiles = {
        "STUDENT": ("STUDENT", world["student_a"]["user_id"]),
        "PARENT": ("PARENT", world["parent"]["user_id"]),
        "TEACHER": ("TEACHER", world["teacher"]["user_id"]),
        "FINANCE": ("FINANCE", world["staff"]["FINANCE"]["user_id"]),
        "SCHOOL_ADMIN": ("SCHOOL_ADMIN", world["schools"]["north"]["admin_user_id"]),
    }
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    memberships = {}
    for role, user_id in ((role, user_id) for role, (_, user_id) in profiles.items()):
        cur.execute(
            """
            SELECT id::text FROM school_memberships
            WHERE user_id = %s AND school_id = %s AND role = %s
            """,
            (user_id, school_id, profiles[role][0]),
        )
        memberships[role] = cur.fetchone()[0]
    conn.close()

    assert _rls_count("students", user_id=profiles["STUDENT"][1], school_id=school_id, role="STUDENT", membership_id=memberships["STUDENT"]) == 1
    assert _rls_count("students", user_id=profiles["PARENT"][1], school_id=school_id, role="PARENT", membership_id=memberships["PARENT"]) == 1
    assert _rls_count("students", user_id=profiles["TEACHER"][1], school_id=school_id, role="TEACHER", membership_id=memberships["TEACHER"]) == 1
    assert _rls_count("students", user_id=profiles["FINANCE"][1], school_id=school_id, role="FINANCE", membership_id=memberships["FINANCE"]) == 2
    assert _rls_count("students", user_id=profiles["SCHOOL_ADMIN"][1], school_id=school_id, role="SCHOOL_ADMIN", membership_id=memberships["SCHOOL_ADMIN"]) == 2
    assert _rls_count("results", user_id=profiles["FINANCE"][1], school_id=school_id, role="FINANCE", membership_id=memberships["FINANCE"]) == 0
    assert _rls_count("results", user_id=profiles["STUDENT"][1], school_id=school_id, role="STUDENT", membership_id=memberships["STUDENT"]) == 1
    south = world["schools"]["south"]["id"]
    assert _rls_count("students", user_id=profiles["STUDENT"][1], school_id=south, role="STUDENT", membership_id=memberships["STUDENT"]) == 0


def test_cross_tenant_header_is_rejected(client: TestClient, world):
    response = client.get(
        "/api/v1/students",
        headers={**world["roles"]["SCHOOL_ADMIN"], "X-School-Id": world["schools"]["south"]["id"]},
    )
    assert response.status_code == 403, response.text


def test_student_cannot_mark_attendance_or_submit_twice(client: TestClient, world):
    denied = client.post(
        "/api/v1/attendance/mark",
        headers=world["roles"]["STUDENT"],
        json={
            "class_id": world["classroom"]["id"],
            "attended_on": date.today().isoformat(),
            "marks": [{"student_id": world["student_a"]["id"], "status": "ABSENT"}],
        },
    )
    assert denied.status_code == 403
    bad = client.post(
        "/api/v1/attendance/mark",
        headers=world["roles"]["TEACHER"],
        json={
            "class_id": world["classroom"]["id"],
            "attended_on": date.today().isoformat(),
            "marks": [{"student_id": world["student_a"]["id"], "status": "NOPE"}],
        },
    )
    assert bad.status_code == 422
    duplicate = client.post(
        "/api/v1/attendance/mark",
        headers=world["roles"]["TEACHER"],
        json={
            "class_id": world["classroom"]["id"],
            "attended_on": date.today().isoformat(),
            "marks": [{"student_id": world["student_a"]["id"], "status": "LATE"}],
        },
    )
    assert duplicate.status_code == 200, duplicate.text

    submitted = client.post(
        f"/api/v1/homework/{world['homework']['id']}/submission",
        headers=world["roles"]["STUDENT"],
        json={"content": "1/2 + 1/4 = 3/4"},
    )
    assert submitted.status_code == 201, submitted.text
    again = client.post(
        f"/api/v1/homework/{world['homework']['id']}/submission",
        headers=world["roles"]["STUDENT"],
        json={"content": "again"},
    )
    assert again.status_code == 409


def test_validation_and_cross_school_message(client: TestClient, world):
    bad_time = client.post(
        "/api/v1/timetable",
        headers=world["roles"]["SCHOOL_ADMIN"],
        json={
            "class_id": world["classroom"]["id"],
            "subject_id": world["subject"]["id"],
            "day_of_week": 2,
            "starts_at": "10:00:00",
            "ends_at": "09:00:00",
        },
    )
    assert bad_time.status_code == 422
    too_high = client.post(
        "/api/v1/results",
        headers=world["roles"]["TEACHER"],
        json={"exam_id": world["exam"]["id"], "student_id": world["student_a"]["id"], "score": 150},
    )
    assert too_high.status_code == 422
    foreign = client.post(
        "/api/v1/messaging",
        headers=world["roles"]["STUDENT"],
        json={"recipient_id": world["schools"]["south"]["admin_user_id"], "body": "hello other school"},
    )
    assert foreign.status_code == 404
    sent = client.post(
        "/api/v1/messaging",
        headers=world["roles"]["STUDENT"],
        json={"recipient_id": world["teacher"]["user_id"], "body": "I finished the homework"},
    )
    assert sent.status_code == 201, sent.text
    inbox = client.get("/api/v1/messaging?box=inbox", headers=world["roles"]["TEACHER"])
    assert sent.json()["id"] in _ids(inbox)
    stranger = client.get(f"/api/v1/messaging/{sent.json()['id']}", headers=world["roles"]["STUDENT_B"])
    assert stranger.status_code == 404


def test_grants_revokes_and_self_lockout_are_audited(client: TestClient, world):
    admin = world["roles"]["SCHOOL_ADMIN"]
    memberships = client.get("/api/v1/access/memberships?page_size=100", headers=admin)
    assert memberships.status_code == 200, memberships.text
    admin_membership = next(item for item in memberships.json()["items"] if item["role"] == "SCHOOL_ADMIN")
    teacher_membership = next(item for item in memberships.json()["items"] if item["user_id"] == world["teacher"]["user_id"])

    self_change = client.post(
        f"/api/v1/access/memberships/{admin_membership['id']}/role",
        headers=admin,
        json={"role": "TEACHER"},
    )
    assert self_change.status_code == 403
    platform_role = client.post(
        "/api/v1/access/role-grants",
        headers=admin,
        json={
            "email": f"boss-{world['suffix']}@example.com",
            "full_name": "Boss",
            "password": PASSWORD,
            "role": "PLATFORM_ADMIN",
        },
    )
    assert platform_role.status_code == 403
    platform_perm = client.put(
        f"/api/v1/access/memberships/{teacher_membership['id']}/permissions",
        headers=admin,
        json={"permission": "platform.manage", "effect": "GRANT"},
    )
    assert platform_perm.status_code == 403

    invited = client.post(
        "/api/v1/access/invitations",
        headers=admin,
        json={"email": f"invite-{world['suffix']}@example.com", "role": "TEACHER"},
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["invite_token"]
    revoked = client.post(f"/api/v1/access/invitations/{invited.json()['id']}/revoke", headers=admin)
    assert revoked.status_code == 200, revoked.text

    granted = client.put(
        f"/api/v1/access/memberships/{teacher_membership['id']}/permissions",
        headers=admin,
        json={"permission": "reports.read", "effect": "GRANT"},
    )
    assert granted.status_code == 200, granted.text
    teacher_perms = client.get("/api/v1/access/me", headers=world["roles"]["TEACHER"])
    assert "reports.read" in teacher_perms.json()["permissions"]
    cleared = client.delete(
        f"/api/v1/access/memberships/{teacher_membership['id']}/permissions/reports.read",
        headers=admin,
    )
    assert cleared.status_code == 204, cleared.text
    teacher_perms = client.get("/api/v1/access/me", headers=world["roles"]["TEACHER"])
    assert "reports.read" not in teacher_perms.json()["permissions"]

    audit = client.get("/api/v1/audit?page_size=100", headers=admin)
    actions = {item["action"] for item in audit.json()["items"]}
    assert {"role.grant", "role.revoke", "permission.grant", "permission.revoke"} <= actions


def _cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def _csrf(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("sybeez_csrf")
    if not token:
        return {}
    return {"X-CSRF-Token": token}


def test_login_sets_httponly_samesite_cookies(client: TestClient, world):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": f"teacher-{world['suffix']}@example.com", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    headers = _cookie_headers(response)
    blob = "\n".join(headers)
    assert any(part.startswith("sybeez_access=") for part in headers)
    assert any(part.startswith("sybeez_refresh=") for part in headers)
    assert "HttpOnly" in blob
    assert "SameSite=lax" in blob
    assert "Path=/api/v1/auth" in blob
    for header in headers:
        attributes = [piece.strip().lower() for piece in header.split(";")]
        assert "secure" not in attributes


def test_refresh_cookie_rotates_and_daily_cap_does_not_extend(client: TestClient, world):
    teacher = client.post(
        "/api/v1/auth/login",
        json={"email": f"teacher-{world['suffix']}@example.com", "password": PASSWORD},
    )
    assert teacher.status_code == 200, teacher.text
    teacher_body = teacher.json()
    assert teacher_body["refresh_expires_in"] > 29 * 24 * 3600
    rotated = client.post("/api/v1/auth/refresh", json={}, headers=_csrf(client))
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["refresh_token"] != teacher_body["refresh_token"]
    assert rotated.json()["refresh_expires_in"] > 29 * 24 * 3600

    admin_email = f"north-admin-{world['suffix']}@example.com"
    admin = client.post("/api/v1/auth/login", json={"email": admin_email, "password": PASSWORD})
    assert admin.status_code == 200, admin.text
    admin_body = admin.json()
    assert admin_body["refresh_expires_in"] <= 24 * 3600
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT family_expires_at FROM refresh_tokens
        WHERE user_id = %s AND revoked = false
        ORDER BY created_at DESC LIMIT 1
        """,
        (admin_body["user_id"],),
    )
    original_family = cur.fetchone()[0]
    conn.close()

    kept = client.post("/api/v1/auth/refresh", json={}, headers=_csrf(client))
    assert kept.status_code == 200, kept.text
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT family_expires_at FROM refresh_tokens
        WHERE user_id = %s AND revoked = false
        ORDER BY created_at DESC LIMIT 1
        """,
        (admin_body["user_id"],),
    )
    assert cur.fetchone()[0] == original_family
    cur.execute(
        """
        UPDATE refresh_tokens
        SET family_expires_at = now() - interval '1 minute'
        WHERE user_id = %s AND revoked = false
        """,
        (admin_body["user_id"],),
    )
    conn.commit()
    conn.close()
    expired = client.post("/api/v1/auth/refresh", json={}, headers=_csrf(client))
    assert expired.status_code == 401, expired.text
    assert "Sign in again" in expired.json()["error"]["message"]


def test_logout_revokes_the_refresh_cookie(client: TestClient, world):
    logged_in = client.post(
        "/api/v1/auth/login",
        json={"email": f"parent-{world['suffix']}@example.com", "password": PASSWORD},
    )
    assert logged_in.status_code == 200, logged_in.text
    gone = client.post("/api/v1/auth/logout", json={}, headers=_csrf(client))
    assert gone.status_code == 200, gone.text
    assert client.post("/api/v1/auth/refresh", json={}, headers=_csrf(client)).status_code == 401


def test_super_admin_staff_portals_are_audited_and_school_scoped(client: TestClient, world):
    platform = world["roles"]["PLATFORM_ADMIN"]
    suffix = world["suffix"]
    rejected = client.post(
        "/api/v1/platform/staff",
        headers=platform,
        json={
            "email": f"denied-{suffix}@example.com",
            "full_name": "Denied Staff",
            "password": PASSWORD,
            "portals": ["finance"],
            "permissions": ["platform.manage"],
        },
    )
    assert rejected.status_code == 403, rejected.text

    created = client.post(
        "/api/v1/platform/staff",
        headers=platform,
        json={
            "email": f"staff-{suffix}@example.com",
            "full_name": "Finance Staff",
            "password": PASSWORD,
            "portals": ["finance"],
            "permissions": ["reports.read"],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["portals"] == ["finance"]
    assert body["permissions"] == ["reports.read"]

    staff = _auth(client, f"staff-{suffix}@example.com")
    assert client.get("/api/v1/platform/schools", headers=staff).status_code == 403
    assert client.get("/api/v1/students", headers=staff).status_code == 403
    school_id = world["schools"]["north"]["id"]
    scoped = {**staff, "X-School-Id": school_id}
    students = client.get("/api/v1/students", headers=scoped)
    assert students.status_code == 200, students.text
    assert set(_ids(students)) == {world["student_a"]["id"], world["student_b"]["id"]}
    assert client.get("/api/v1/results", headers=scoped).status_code == 403
    health = client.get("/api/v1/reports/health", headers=scoped)
    assert health.status_code == 200, health.text

    assert (
        _rls_count(
            "students",
            user_id=body["user_id"],
            school_id=school_id,
            role="PLATFORM_STAFF",
            membership_id=None,
            portals="finance",
        )
        == 2
    )
    assert (
        _rls_count(
            "results",
            user_id=body["user_id"],
            school_id=school_id,
            role="PLATFORM_STAFF",
            membership_id=None,
            portals="finance",
        )
        == 0
    )
    assert (
        _rls_count(
            "students",
            user_id=body["user_id"],
            school_id="",
            role="PLATFORM_STAFF",
            membership_id=None,
            portals="finance",
        )
        == 0
    )

    audit = client.get("/api/v1/platform/audit", headers=platform, params={"action": "staff.grant"})
    assert audit.status_code == 200, audit.text
    assert any(item["action"] == "staff.grant" for item in audit.json()["items"])

    invited = client.post(
        "/api/v1/platform/staff-invitations",
        headers=platform,
        json={"email": f"invite-staff-{suffix}@example.com", "portals": ["hrm"], "permissions": []},
    )
    assert invited.status_code == 201, invited.text
    accepted = client.post(
        "/api/v1/auth/accept-staff-invitation",
        json={
            "token": invited.json()["invite_token"],
            "password": PASSWORD,
            "full_name": "HR Staff",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "PLATFORM_STAFF"


def test_only_the_bootstrapped_email_can_call_platform(client: TestClient, world):
    suffix = world["suffix"]
    email = f"ops-{suffix}@example.com"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (id, email, password_hash, full_name, status, is_active, is_platform_admin)
        VALUES (%s, %s, %s, 'Other Admin', 'ACTIVE', true, true)
        """,
        (str(uuid.uuid4()), email, hash_password(PASSWORD)),
    )
    conn.close()
    other = _auth(client, email)
    assert client.get("/api/v1/platform/schools", headers=other).status_code == 403


def test_school_admin_role_change_applies_before_the_token_expires(client: TestClient, world):
    suffix = world["suffix"]
    admin = world["roles"]["SCHOOL_ADMIN"]
    email = f"clerk-{suffix}@example.com"
    granted = client.post(
        "/api/v1/access/role-grants",
        headers=admin,
        json={"email": email, "full_name": "Clerk", "password": PASSWORD, "role": "STUDENT"},
    )
    assert granted.status_code == 201, granted.text
    clerk = _auth(client, email)
    assert client.get("/api/v1/results", headers=clerk).status_code == 200
    changed = client.post(
        f"/api/v1/access/memberships/{granted.json()['id']}/role",
        headers=admin,
        json={"role": "FINANCE"},
    )
    assert changed.status_code == 200, changed.text
    assert client.get("/api/v1/results", headers=clerk).status_code == 401
    clerk = _auth(client, email)
    assert client.get("/api/v1/results", headers=clerk).status_code == 403
    students = client.get("/api/v1/students", headers=clerk)
    assert students.status_code == 200, students.text
    assert world["student_a"]["id"] in _ids(students)
