from datetime import datetime, timezone
from uuid import uuid4

from src.modules.exam_prep.readiness import band, subject_readiness
from src.modules.learning_paths.service import Attempt

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def test_bands():
    assert band(None, None) == "not_started"
    assert band(85, None) == "ready"
    assert band(85, 40) == "getting_ready"  # strong but covers too little of the syllabus
    assert band(70, 90) == "getting_ready"
    assert band(45, 90) == "needs_work"


def test_subject_readiness_weak_topics_and_coverage():
    sub = uuid4()
    a = [Attempt(sub, "Light", 40, NOW), Attempt(sub, "Light", 50, NOW), Attempt(sub, "Sound", 90, NOW)]
    r = subject_readiness(sub, "Science", a, ["Light", "Sound", "Heat", "Force"], [(NOW, 55.0)], NOW)
    assert r["mastery"] == 67.5 and r["band"] == "getting_ready"
    assert [t["topic"] for t in r["weak_topics"]] == ["Light"]
    assert r["syllabus_covered"] == 2 and r["coverage_pct"] == 50.0
    assert r["recent_mocks"] == [55.0] and r["mock_count"] == 1


def test_no_attempts_is_not_started_and_no_invented_numbers():
    r = subject_readiness(uuid4(), "Maths", [], ["A"], [], NOW)
    assert r["band"] == "not_started" and r["mastery"] is None and r["coverage_pct"] == 0.0
