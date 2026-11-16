"""Employee activity filters do not need a database."""

from src.modules.platform.employee_detail import action_matches, target_href


def test_action_groups_cover_portal_writes_and_sign_ins():
    assert action_matches("auth.login", "sign-in") is True
    assert action_matches("auth.login_failed", "sign-in") is False
    assert action_matches("auth.login_failed", "failed-sign-in") is True
    assert action_matches("school.suspend", "schools") is True
    assert action_matches("plan.update", "plans") is True
    assert action_matches("staff.revoke", "employees") is True
    assert action_matches("platform.ai_settings.update", "ai") is True
    assert action_matches("school.suspend", None) is True
    assert action_matches("school.suspend", "school.suspend") is True


def test_targets_link_to_the_portal_page():
    assert target_href("school", "abc") == "/platform/schools/abc"
    assert target_href("plan", "p1") == "/platform/plans"
    assert target_href("staff", "u1") == "/platform/staff/u1"
    assert target_href("note", None) is None
