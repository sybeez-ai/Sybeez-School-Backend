import pytest

from src.core.exceptions import ConflictError
from src.modules.platform.staff_service import console_role


def test_invite_role_is_super_admin_admin_or_sales():
    assert console_role("super admin") == "SUPER_ADMIN"
    assert console_role("Admin") == "ADMIN"
    assert console_role("sales") == "SALES"
    assert console_role(None) is None
    assert console_role("  ") is None


def test_invite_role_rejects_a_department_label():
    with pytest.raises(ConflictError, match="Super Admin, Admin, or Sales"):
        console_role("SUPPORT")
