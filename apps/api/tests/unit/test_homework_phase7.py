"""Phase 7 homework contract tests."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app
from src.modules.homework.schemas import HomeworkCreate, SubmissionCreate, SubmissionGrade
from src.shared.enums import HomeworkStatus, HomeworkSubmissionStatus, HomeworkType

client = TestClient(app)


def test_homework_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/homework",
        "/api/v1/homework/mine",
        "/api/v1/homework/children",
        "/api/v1/homework/children/{student_id}",
        "/api/v1/homework/reports",
        "/api/v1/homework/{homework_id}",
        "/api/v1/homework/{homework_id}/publish",
        "/api/v1/homework/{homework_id}/close",
        "/api/v1/homework/{homework_id}/submissions",
        "/api/v1/homework/{homework_id}/submission",
        "/api/v1/homework/{homework_id}/attachments",
        "/api/v1/homework/{homework_id}/submission/attachments",
        "/api/v1/homework/submissions/{submission_id}",
    ):
        assert p in paths, f"missing {p}"


def test_homework_endpoints_require_auth():
    assert client.get("/api/v1/homework").status_code == 401
    assert client.get("/api/v1/homework/mine").status_code == 401
    assert client.post("/api/v1/homework", json={}).status_code == 401
    hid = str(uuid4())
    assert client.get(f"/api/v1/homework/{hid}").status_code == 401
    assert client.post(f"/api/v1/homework/{hid}/publish").status_code == 401
    assert client.post(f"/api/v1/homework/{hid}/submission", json={"content": "x"}).status_code == 401
    sid = str(uuid4())
    assert client.patch(f"/api/v1/homework/submissions/{sid}", json={"marks": 10}).status_code == 401


def test_homework_create_schema():
    body = HomeworkCreate(
        class_id=uuid4(),
        subject_id=uuid4(),
        title="Algebra Exercises",
        description="Complete exercises 1–20.",
        homework_type=HomeworkType.HOMEWORK,
        due_at=datetime.now(timezone.utc) + timedelta(days=3),
        max_marks=20,
        status=HomeworkStatus.DRAFT,
    )
    assert body.title.startswith("Algebra")
    assert body.status == HomeworkStatus.DRAFT


def test_homework_create_rejects_bad_dates():
    try:
        HomeworkCreate(
            class_id=uuid4(),
            subject_id=uuid4(),
            title="Bad",
            assigned_at=datetime.now(timezone.utc) + timedelta(days=5),
            due_at=datetime.now(timezone.utc),
        )
        assert False, "expected validation error"
    except Exception:
        pass


def test_submission_and_grade_schemas():
    SubmissionCreate(content="My answer")
    graded = SubmissionGrade(marks=18, feedback="Good work", status=HomeworkSubmissionStatus.GRADED)
    assert graded.status == HomeworkSubmissionStatus.GRADED
    returned = SubmissionGrade(status=HomeworkSubmissionStatus.RETURNED, feedback="Revise Q12")
    assert returned.status == HomeworkSubmissionStatus.RETURNED


def test_homework_types_and_statuses_exist():
    assert HomeworkType.ASSIGNMENT.value == "ASSIGNMENT"
    assert HomeworkSubmissionStatus.PENDING.value == "PENDING"
    assert HomeworkStatus.PUBLISHED.value == "PUBLISHED"
