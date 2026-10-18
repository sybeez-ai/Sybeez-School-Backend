from src.modules.auth import cognito as C


class FakeClient:
    def __init__(self, have):
        self.have, self.added, self.removed = have, [], []

    def admin_list_groups_for_user(self, **kw):
        return {"Groups": [{"GroupName": g} for g in self.have]}

    def admin_add_user_to_group(self, **kw):
        self.added.append(kw["GroupName"])

    def admin_remove_user_from_group(self, **kw):
        self.removed.append(kw["GroupName"])


def test_role_to_group_mapping():
    assert C.groups_for_roles(["HR_MANAGER", "HR", "TEACHER", "UNKNOWN"]) == {"HRM", "TEACHER"}
    assert C.groups_for_roles(["ACCOUNTANT", "CASHIER"]) == {"FINANCE"}


def test_sync_adds_missing_removes_stale_managed_only(monkeypatch):
    fake = FakeClient({"TEACHER", "SomeManualGroup"})
    monkeypatch.setattr(C, "cognito_configured", lambda: True)
    monkeypatch.setattr(C, "_client", lambda: fake)
    monkeypatch.setattr(C, "_resolve_username", lambda email: email)
    assert C.sync_user_groups(email="a@b.c", roles=["SCHOOL_ADMIN"]) is True
    assert fake.added == ["SCHOOL_ADMIN"] and fake.removed == ["TEACHER"]  # unmanaged group left alone


def test_sync_never_raises(monkeypatch):
    class Boom:
        def admin_list_groups_for_user(self, **kw):
            raise RuntimeError("denied")

    monkeypatch.setattr(C, "cognito_configured", lambda: True)
    monkeypatch.setattr(C, "_client", lambda: Boom())
    monkeypatch.setattr(C, "_resolve_username", lambda email: email)
    assert C.sync_user_groups(email="a@b.c", roles=["TEACHER"]) is False
