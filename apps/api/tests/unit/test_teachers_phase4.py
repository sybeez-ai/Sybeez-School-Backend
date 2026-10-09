"""Phase 4 teacher / subject / assignment contract tests."""

from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app
from src.modules.teachers.schemas import TeacherCreate, TeacherUpdate
from src.modules.subjects.schemas import SubjectCreate, SubjectUpdate
from src.modules.teacher_assignments.schemas import TeacherAssignmentCreate

client = TestClient(app)


def test_teacher_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/teachers",
        "/api/v1/teachers/me",
        "/api/v1/teachers/me/dashboard",
        "/api/v1/teachers/{teacher_id}",
        "/api/v1/teachers/{teacher_id}/photo",
        "/api/v1/teachers/{teacher_id}/status",
        "/api/v1/subjects",
        "/api/v1/subjects/{subject_id}",
        "/api/v1/teacher-assignments",
        "/api/v1/teacher-assignments/mine",
        "/api/v1/teacher-assignments/{assignment_id}",
    ):
        assert p in paths, f"missing {p}"


def test_teacher_endpoints_require_auth():
    assert client.get("/api/v1/teachers").status_code == 401
    assert client.get("/api/v1/teachers/me").status_code == 401
    assert client.get("/api/v1/teachers/me/dashboard").status_code == 401
    tid = str(uuid4())
    assert client.get(f"/api/v1/teachers/{tid}").status_code == 401
    assert client.patch(f"/api/v1/teachers/{tid}", json={"phone": "1"}).status_code == 401
    assert client.get("/api/v1/teacher-assignments/mine").status_code == 401
    assert client.delete(f"/api/v1/teacher-assignments/{tid}").status_code == 401


def test_teacher_create_schema():
    body = TeacherCreate(
        full_name="Arun Kumar",
        email="arun@school.edu",
        employee_code="T-101",
        phone="9876543210",
        qualification="M.Sc. Mathematics",
    )
    assert body.full_name.startswith("Arun")
    assert "class_id" in TeacherCreate.model_fields


def test_teacher_update_has_master_fields_not_assignment():
    fields = set(TeacherUpdate.model_fields.keys())
    assert "phone" in fields
    assert "qualification" in fields
    assert "joining_date" in fields
    assert "class_id" not in fields
    assert "subject_id" not in fields


def test_subject_update_and_archive_schema():
    SubjectCreate(name="Mathematics", code="MATH")
    SubjectUpdate(status="ARCHIVED")


def test_assignment_create_requires_triad():
    body = TeacherAssignmentCreate(
        teacher_id=uuid4(),
        subject_id=uuid4(),
        class_id=uuid4(),
    )
    assert body.academic_year_id is None
