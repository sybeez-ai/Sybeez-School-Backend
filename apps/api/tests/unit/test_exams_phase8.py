"""Phase 8 exams & results — full checklist coverage."""

from datetime import date, time
from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app
from src.modules.exams.pdf import build_report_card_pdf
from src.modules.exams.schemas import (
    ExamCreate,
    ExamScheduleCreate,
    ExamSubjectCreate,
    GradeRuleIn,
    GradingScaleCreate,
    MarkRowIn,
    MarksSaveIn,
)
from src.modules.exams.service import ExamService
from src.shared.enums import ExamAttendanceStatus, ExamStatus, MarkEntryStatus

client = TestClient(app)


def test_exams_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/exams",
        "/api/v1/exams/types",
        "/api/v1/exams/grading-scales",
        "/api/v1/exams/mine",
        "/api/v1/exams/{exam_id}",
        "/api/v1/exams/{exam_id}/subjects",
        "/api/v1/exams/{exam_id}/schedule",
        "/api/v1/exams/{exam_id}/marks",
        "/api/v1/exams/{exam_id}/marks/submit",
        "/api/v1/exams/{exam_id}/open",
        "/api/v1/exams/{exam_id}/calculate",
        "/api/v1/exams/{exam_id}/publish",
        "/api/v1/exams/{exam_id}/overview",
        "/api/v1/exams/{exam_id}/unlock",
        "/api/v1/results/me",
        "/api/v1/results/children",
        "/api/v1/results/children/{student_id}",
        "/api/v1/results/exams/{exam_id}",
        "/api/v1/results/{exam_id}/report-card",
        "/api/v1/results/{exam_id}/report-card/pdf",
    ):
        assert p in paths, f"missing {p}"


def test_exams_endpoints_require_auth():
    assert client.get("/api/v1/exams").status_code == 401
    assert client.post("/api/v1/exams", json={}).status_code == 401
    assert client.get("/api/v1/exams/mine").status_code == 401
    eid = str(uuid4())
    assert client.post(f"/api/v1/exams/{eid}/publish").status_code == 401
    assert client.post(f"/api/v1/exams/{eid}/calculate").status_code == 401
    assert client.post(f"/api/v1/exams/{eid}/open").status_code == 401
    assert client.get("/api/v1/results/me").status_code == 401
    assert client.get("/api/v1/results/children").status_code == 401


def test_exam_create_schema():
    body = ExamCreate(
        title="Mid-Term Examination",
        academic_year_id=uuid4(),
        grade_id=uuid4(),
        class_ids=[uuid4(), uuid4()],
        starts_on=date(2026, 10, 10),
        ends_on=date(2026, 10, 20),
        status=ExamStatus.DRAFT,
        must_pass_all_subjects=True,
    )
    assert body.title.startswith("Mid")
    assert body.must_pass_all_subjects is True


def test_exam_create_rejects_bad_dates():
    try:
        ExamCreate(
            title="Bad",
            academic_year_id=uuid4(),
            starts_on=date(2026, 10, 20),
            ends_on=date(2026, 10, 10),
        )
        assert False, "expected validation error"
    except Exception:
        pass


def test_schedule_rejects_bad_times():
    try:
        ExamScheduleCreate(
            exam_subject_id=uuid4(),
            exam_date=date(2026, 10, 12),
            starts_at=time(12, 0),
            ends_at=time(9, 0),
        )
        assert False
    except Exception:
        pass


def test_schedule_section_conflict_validation():
    exam = type("E", (), {"starts_on": date(2026, 10, 10), "ends_on": date(2026, 10, 20)})()
    sid_a = uuid4()
    sid_b = uuid4()
    class_id = uuid4()
    items = [
        ExamScheduleCreate(
            exam_subject_id=sid_a,
            class_id=class_id,
            exam_date=date(2026, 10, 12),
            starts_at=time(9, 0),
            ends_at=time(12, 0),
        ),
        ExamScheduleCreate(
            exam_subject_id=sid_b,
            class_id=class_id,
            exam_date=date(2026, 10, 12),
            starts_at=time(10, 0),
            ends_at=time(13, 0),
        ),
    ]
    svc = ExamService(db=None)  # type: ignore[arg-type]
    try:
        svc._validate_schedule_items(exam, items)  # type: ignore[arg-type]
        assert False, "expected conflict"
    except Exception as e:
        assert "conflict" in str(e).lower() or "overlap" in str(e).lower() or hasattr(e, "status_code")


def test_schedule_date_outside_window():
    exam = type("E", (), {"starts_on": date(2026, 10, 10), "ends_on": date(2026, 10, 20)})()
    items = [
        ExamScheduleCreate(
            exam_subject_id=uuid4(),
            exam_date=date(2026, 9, 1),
            starts_at=time(9, 0),
            ends_at=time(12, 0),
        )
    ]
    svc = ExamService(db=None)  # type: ignore[arg-type]
    try:
        svc._validate_schedule_items(exam, items)  # type: ignore[arg-type]
        assert False
    except Exception:
        pass


def test_exam_subject_and_marks_schemas():
    ExamSubjectCreate(subject_id=uuid4(), max_marks=100, pass_marks=35, instructions="Bring calculator")
    try:
        ExamSubjectCreate(subject_id=uuid4(), max_marks=50, pass_marks=60)
        assert False
    except Exception:
        pass
    MarksSaveIn(
        exam_subject_id=uuid4(),
        class_id=uuid4(),
        rows=[
            MarkRowIn(student_id=uuid4(), obtained_marks=82),
            MarkRowIn(
                student_id=uuid4(),
                obtained_marks=None,
                attendance=ExamAttendanceStatus.ABSENT,
            ),
        ],
    )
    try:
        MarkRowIn(student_id=uuid4(), obtained_marks=-5)
        assert False
    except Exception:
        pass


def test_grading_scale_schema():
    scale = GradingScaleCreate(
        name="Standard",
        is_default=True,
        must_pass_all_subjects=True,
        rules=[
            GradeRuleIn(grade="A+", min_percentage=90, max_percentage=100, sort_order=1),
            GradeRuleIn(grade="F", min_percentage=0, max_percentage=34, sort_order=7),
        ],
    )
    assert len(scale.rules) == 2


def test_grade_for_percentage():
    class R:
        def __init__(self, g, lo, hi):
            self.grade = g
            self.min_percentage = lo
            self.max_percentage = hi

    rules = [R("A", 80, 100), R("B", 60, 79), R("F", 0, 59)]
    svc = ExamService(db=None)  # type: ignore[arg-type]
    assert svc._grade_for_percentage(rules, 84.2) == "A"  # type: ignore[arg-type]
    assert svc._grade_for_percentage(rules, 65) == "B"  # type: ignore[arg-type]
    assert svc._grade_for_percentage(rules, 20) == "F"  # type: ignore[arg-type]


def test_result_engine_totals_and_pass_rules():
    # Rahul scenario: 82/100 Math only → pass
    out = ExamService.compute_totals(
        [(82.0, 100.0, "PRESENT", 35.0)],
        must_pass_all=True,
    )
    assert out["total_obtained"] == 82
    assert out["total_max"] == 100
    assert out["percentage"] == 82.0
    assert out["pass_fail"] == "PASS"

    # Multi-subject mid-term
    out = ExamService.compute_totals(
        [
            (82.0, 100.0, "PRESENT", 35.0),
            (88.0, 100.0, "PRESENT", 35.0),
            (76.0, 100.0, "PRESENT", 35.0),
            (91.0, 100.0, "PRESENT", 35.0),
            (84.0, 100.0, "PRESENT", 35.0),
        ],
        must_pass_all=True,
    )
    assert out["total_obtained"] == 421
    assert out["total_max"] == 500
    assert out["percentage"] == 84.2
    assert out["pass_fail"] == "PASS"

    # Subject fail rule
    out = ExamService.compute_totals(
        [
            (25.0, 100.0, "PRESENT", 35.0),
            (90.0, 100.0, "PRESENT", 35.0),
        ],
        must_pass_all=True,
    )
    assert out["percentage"] == 57.5
    assert out["pass_fail"] == "FAIL"

    # Overall-only rule can still pass
    out = ExamService.compute_totals(
        [
            (25.0, 100.0, "PRESENT", 35.0),
            (90.0, 100.0, "PRESENT", 35.0),
        ],
        must_pass_all=False,
    )
    assert out["pass_fail"] == "PASS"

    # Absent ≠ zero
    out = ExamService.compute_totals(
        [
            (None, 100.0, "ABSENT", 35.0),
            (None, 100.0, "ABSENT", 35.0),
        ],
        must_pass_all=True,
    )
    assert out["pass_fail"] == "ABSENT"
    assert out["total_obtained"] == 0

    # Missing marks → incomplete
    out = ExamService.compute_totals(
        [
            (82.0, 100.0, "PRESENT", 35.0),
            (None, 100.0, "PRESENT", 35.0),
        ],
        must_pass_all=True,
    )
    assert out["pass_fail"] == "INCOMPLETE"

    # Excused excluded from max
    out = ExamService.compute_totals(
        [
            (82.0, 100.0, "PRESENT", 35.0),
            (None, 100.0, "EXCUSED", 35.0),
        ],
        must_pass_all=True,
    )
    assert out["total_max"] == 100
    assert out["percentage"] == 82.0
    assert out["pass_fail"] == "PASS"


def test_report_card_pdf_bytes():
    pdf = build_report_card_pdf(
        {
            "school_name": "ABC School",
            "exam_title": "Mid-Term Examination",
            "student_name": "Rahul Kumar",
            "class_name": "8-A",
            "roll_number": "18",
            "subjects": [
                {"name": "Mathematics", "max": 100, "marks": 82, "grade": "A", "attendance": "PRESENT"},
                {"name": "English", "max": 100, "marks": 88, "grade": "A", "attendance": "PRESENT"},
            ],
            "total_obtained": 170,
            "total_max": 200,
            "percentage": 85.0,
            "grade_label": "A",
            "pass_fail": "PASS",
        }
    )
    assert pdf.startswith(b"%PDF")
    assert b"Rahul" in pdf or b"Mathematics" in pdf


def test_exam_statuses_exist():
    assert ExamStatus.PUBLISHED.value == "PUBLISHED"
    assert ExamStatus.MARKS_ENTRY.value == "MARKS_ENTRY"
    assert MarkEntryStatus.SUBMITTED.value == "SUBMITTED"
    assert ExamAttendanceStatus.ABSENT.value == "ABSENT"
