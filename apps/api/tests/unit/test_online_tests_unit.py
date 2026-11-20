import pytest
from pydantic import ValidationError

from src.modules.access.catalog import permissions_for
from src.modules.online_tests import schemas as S
from src.modules.online_tests.service import _csv_safe
from src.shared.enums import UserRole


def test_permissions_by_role():
    assert {"online_tests.read", "online_tests.write"} <= permissions_for(UserRole.TEACHER)
    assert "online_tests.take" not in permissions_for(UserRole.TEACHER)
    assert "online_tests.manage" not in permissions_for(UserRole.TEACHER)
    admin = permissions_for(UserRole.SCHOOL_ADMIN)
    assert {"online_tests.read", "online_tests.write", "online_tests.manage"} <= admin
    assert "online_tests.take" in permissions_for(UserRole.STUDENT)
    assert permissions_for(UserRole.STUDENT).isdisjoint({"online_tests.write", "online_tests.manage", "online_tests.read"})
    assert "online_tests.read" in permissions_for(UserRole.PARENT)
    assert permissions_for(UserRole.PARENT).isdisjoint({"online_tests.write", "online_tests.take", "online_tests.manage"})


def test_question_validation():
    ok = S.QuestionIn(prompt=" What? ", options=[" a ", "b"], correct_index=1)
    assert ok.prompt == "What?" and ok.options == ["a", "b"]
    for bad in (
        dict(prompt="x", options=["a", "A"], correct_index=0),
        dict(prompt="x", options=["a", "b"], correct_index=2),
        dict(prompt="x", options=["a"], correct_index=0),
        dict(prompt="", options=["a", "b"], correct_index=0),
        dict(prompt="x", options=["a", " "], correct_index=0),
        dict(prompt="x", options=["a"] * 7, correct_index=0),
    ):
        with pytest.raises(ValidationError):
            S.QuestionIn(**bad)


def test_student_models_cannot_carry_a_key():
    for model in (S.StudentQuestionOut, S.AttemptStartOut, S.StudentTestOut, S.AnswerSavedOut, S.AttemptStateOut):
        assert not {f for f in model.model_fields if "correct" in f or "explanation" in f}, model
    assert not {f for f in S.OptionOut.model_fields if f not in ("id", "text")}


def test_window_validation_and_defaults():
    with pytest.raises(ValidationError):
        S.OnlineTestCreate(
            class_id="00000000-0000-0000-0000-000000000001", subject_id="00000000-0000-0000-0000-000000000002",
            title="x", opens_at="2026-10-20T10:00:00Z", closes_at="2026-10-20T09:00:00Z",
        )
    s = S.OnlineTestSettings()
    assert s.max_violations == 3 and s.shuffle_questions and s.secure_mode and s.result_release == S.ResultRelease.MANUAL


def test_event_types_are_whitelisted():
    with pytest.raises(ValidationError):
        S.EventIn(type="ANYTHING")
    with pytest.raises(ValidationError):
        S.EventIn(type="TAB_HIDDEN", meta={"x": "y" * 600})


def test_csv_cells_cannot_inject_formulas():
    assert _csv_safe("=1+1") == "'=1+1"
    assert _csv_safe("@SUM(A1)") == "'@SUM(A1)"
    assert _csv_safe("Asha") == "Asha"
    assert _csv_safe(None) == ""
