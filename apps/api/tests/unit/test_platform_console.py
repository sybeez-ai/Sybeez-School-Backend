from src.api.dependencies import CurrentUser, is_super_admin
from src.modules.features.catalog import is_known_key, is_nav_key, portal_keys_for_role
from src.modules.platform.console import _ntype, category_of
from src.shared.enums import UserRole


def _user(**kw):
    base = dict(id="00000000-0000-0000-0000-000000000001", email="x@sybeez.ai", full_name="X",
                role=UserRole.PLATFORM_ADMIN, school_id=None, membership_id=None, is_platform_admin=True,
                platform_role="SUPER_ADMIN", portals=[])
    base.update(kw)
    return CurrentUser(**base)


def test_log_categories():
    assert category_of("auth.login_failed") == "login_failed"
    assert category_of("app.error") == "error"
    assert category_of("app.client_error") == "error"
    assert category_of("platform.school.create") == "platform"
    assert category_of("family_document.viewed") == "activity"


def test_notification_types():
    assert _ntype("platform.school.admin.create") == "admins"
    assert _ntype("platform.school.create") == "schools"
    assert _ntype("auth.login_failed") == "login_failures"


def test_nav_keys():
    assert is_nav_key("nav.teacher.homework")
    assert is_nav_key("nav.payroll.payslips")
    assert not is_nav_key("nav.platform.x")
    assert not is_nav_key("nav.teacher.")
    assert is_known_key("portal.payroll") and is_known_key("ai_assistant")
    assert "portal.payroll" in portal_keys_for_role(UserRole.PAYROLL_ADMIN.value)
    assert "portal.hrm" in portal_keys_for_role(UserRole.PAYROLL_ADMIN.value)


def test_super_admin_flag():
    assert is_super_admin(_user())
    assert not is_super_admin(_user(platform_role="ADMIN", email="someone@sybeez.ai"))
    assert not is_super_admin(_user(is_platform_admin=False))
