"""Phase 3 student management — schema, OpenAPI, and isolation contract tests."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app
from src.modules.students.schemas import (
    BulkImportRow,
    ParentLinkInput,
    StudentCreate,
    StudentUpdate,
)


client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200


def test_students_openapi_paths_present():
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    for p in (
        "/api/v1/students",
        "/api/v1/students/import/validate",
        "/api/v1/students/import/confirm",
        "/api/v1/students/{student_id}",
        "/api/v1/students/{student_id}/enroll",
        "/api/v1/students/{student_id}/transfer",
        "/api/v1/students/{student_id}/withdraw",
        "/api/v1/students/{student_id}/photo",
        "/api/v1/students/{student_id}/status",
        "/api/v1/parents",
        "/api/v1/parents/by-student/{student_id}/link",
        "/api/v1/parents/by-student/{student_id}/unlink",
    ):
        assert p in paths, f"missing OpenAPI path {p}"


def test_students_endpoints_require_auth():
    assert client.get("/api/v1/students").status_code == 401
    assert client.post("/api/v1/students", json={}).status_code == 401
    sid = str(uuid4())
    assert client.get(f"/api/v1/students/{sid}").status_code == 401
    assert client.post(f"/api/v1/students/{sid}/transfer", json={"class_id": sid}).status_code == 401
    assert client.post("/api/v1/parents/by-student/" + sid + "/unlink", json={"parent_id": sid}).status_code == 401


def test_student_create_schema_requires_name_parts():
    # first/last required — full_name alone is no longer accepted
    try:
        StudentCreate(email="a@b.com", first_name="", last_name="Kumar")  # type: ignore[arg-type]
        raised = False
    except Exception:
        raised = True
    assert raised


def test_student_create_schema_accepts_parent_link_by_id():
    body = StudentCreate(
        first_name="Rahul",
        last_name="Kumar",
        email="rahul@school.edu",
        parents=[ParentLinkInput(parent_id=uuid4(), relationship="father", is_primary=True)],
    )
    assert body.parents[0].parent_id is not None
    assert body.first_name == "Rahul"


def test_student_update_does_not_accept_class_id():
    """Master update must not silently move enrollment placement."""
    fields = set(StudentUpdate.model_fields.keys())
    assert "class_id" not in fields
    assert "academic_year_id" not in fields


def test_bulk_import_row_requires_core_fields():
    row = BulkImportRow(
        row=1,
        first_name="Anu",
        last_name="Sharma",
        email="anu@school.edu",
        date_of_birth="2015-01-02",
        academic_year="2026-27",
        grade="Class 7",
        section="A",
    )
    assert row.section == "A"
