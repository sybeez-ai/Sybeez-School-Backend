from src.modules.platform.ai_limits import (
    PERSON_STOP,
    SCHOOL_STOP,
    allowance_for,
    effective_assignment,
    group_for_role,
    recorded_models,
)


def test_person_override_beats_the_group_and_the_school():
    school = {"monthly_token_limit": 50000, "model": "school-model"}
    group = {"monthly_token_limit": 20000, "model": "group-model"}
    person = {"monthly_token_limit": 1000, "model": "person-model"}
    assert effective_assignment(school, group, person) == {
        "monthly_token_limit": 1000,
        "model": "person-model",
    }
    assert effective_assignment(school, group, {"monthly_token_limit": None, "model": None}) == {
        "monthly_token_limit": 20000,
        "model": "group-model",
    }
    assert effective_assignment(school, None, None)["model"] == "school-model"


def test_a_reached_school_cap_stops_before_a_personal_cap():
    blocked = allowance_for(
        school_used=50000,
        school_limit=50000,
        person_used=10,
        person_limit=1000,
        model="gemini",
    )
    assert blocked["allowed"] is False
    assert blocked["message"] == SCHOOL_STOP
    personal = allowance_for(
        school_used=100,
        school_limit=50000,
        person_used=1000,
        person_limit=1000,
        model="gemini",
    )
    assert personal["allowed"] is False
    assert personal["message"] == PERSON_STOP
    open_cap = allowance_for(
        school_used=12400,
        school_limit=50000,
        person_used=40,
        person_limit=None,
        model="gemini",
    )
    assert open_cap["allowed"] is True
    assert open_cap["model"] == "gemini"


def test_only_configured_models_with_tokens_are_shown():
    rows = recorded_models(
        [
            {"model": "qwen3.8-flash", "tokens": 120},
            {"model": "echo", "tokens": 40},
            {"model": "qwen3.8-flash", "tokens": 0},
        ],
        ["qwen3.8-flash"],
    )
    assert rows == [{"model": "qwen3.8-flash", "tokens": 120}]


def test_role_groups():
    assert group_for_role("STUDENT") == "students"
    assert group_for_role("PARENT") == "parents"
    assert group_for_role("TEACHER") == "staff"
    assert group_for_role("SCHOOL_ADMIN") == "staff"
