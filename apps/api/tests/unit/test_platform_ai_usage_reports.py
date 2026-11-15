"""AI usage recording, plan-delete bypass, and platform CSV reports. No database."""

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import CurrentUser, normalize_platform_role
from src.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from src.main import app
from src.modules.platform.ai_consumption import model_breakdown, school_people_tokens, school_token_rows
from src.modules.platform.ai_usage import (
    USAGE_BATCH_LIMIT,
    AiUsageIn,
    apply_usage,
    backfill_ai_usage,
    normalize_usage,
    record_usage,
    usage_day,
)
from src.modules.platform.reports import (
    COLUMNS,
    ai_token_export_rows,
    assert_report_access,
    render_csv,
    report_audience,
)
from src.shared.enums import UserRole

client = TestClient(app)


def _user(**kw) -> CurrentUser:
    base = dict(
        id=uuid4(),
        email="admin@sybeez.ai",
        full_name="Owner",
        role=UserRole.PLATFORM_ADMIN,
        school_id=None,
        membership_id=None,
        is_platform_admin=True,
        platform_role="SUPER_ADMIN",
        portals=(),
    )
    base.update(kw)
    return CurrentUser(**base)


def test_platform_role_normalization_strips_case_spaces_and_hyphens():
    assert normalize_platform_role(" super admin ") == "SUPER_ADMIN"
    assert normalize_platform_role("platform-admin") == "PLATFORM_ADMIN"
    assert normalize_platform_role("admin") == "ADMIN"
    assert normalize_platform_role(None) == ""
    assert normalize_platform_role("  ") == ""


def test_usage_day_is_midnight_utc():
    morning = datetime(2026, 11, 15, 9, 30, tzinfo=timezone.utc)
    evening = datetime(2026, 11, 15, 23, 5, tzinfo=timezone.utc)
    assert usage_day(morning) == usage_day(evening)
    assert usage_day(morning) == datetime(2026, 11, 15, tzinfo=timezone.utc)


def test_normalize_usage_keeps_posted_tokens_and_defaults_the_caller():
    school_id = uuid4()
    actor = _user(school_id=school_id, role=UserRole.STUDENT)
    usage = normalize_usage(AiUsageIn(input_tokens=4, output_tokens=5, model=""), actor)
    assert usage.input_tokens == 4
    assert usage.output_tokens == 5
    assert usage.tokens == 9
    assert usage.model == "unknown"
    assert usage.provider == "unknown"
    assert usage.capability == "CHAT"
    assert usage.user_id == actor.id
    assert usage.school_id == school_id
    assert usage.role is None

    other = uuid4()
    posted = normalize_usage(
        AiUsageIn(
            school_id=other,
            user_id=other,
            role="STUDENT",
            model="qwen",
            provider="qwen",
            capability="CHAT",
            input_tokens=1,
            output_tokens=0,
        ),
        actor,
    )
    assert posted.school_id == other
    assert posted.user_id == other
    assert posted.role == "STUDENT"
    assert posted.model == "qwen"
    assert posted.tokens == 1


def test_school_user_cannot_record_someone_elses_usage():
    school_id = uuid4()
    actor = _user(
        email="student@school.test",
        role=UserRole.STUDENT,
        school_id=school_id,
        is_platform_admin=False,
        platform_role=None,
    )
    own = normalize_usage(AiUsageIn(input_tokens=2), actor)
    assert own.school_id == school_id
    assert own.user_id == actor.id
    with pytest.raises(ForbiddenError):
        normalize_usage(AiUsageIn(school_id=uuid4(), input_tokens=1), actor)
    with pytest.raises(ForbiddenError):
        normalize_usage(AiUsageIn(user_id=uuid4(), input_tokens=1), actor)


def test_live_usage_post_does_not_write_an_audit_row():
    source = inspect.getsource(record_usage) + inspect.getsource(apply_usage)
    assert "AuditRepository" not in source
    assert ".write(" not in source


@pytest.mark.asyncio
async def test_backfill_rejects_more_than_500_rows():
    with pytest.raises(ValidationError):
        await backfill_ai_usage(None, _user(), [AiUsageIn(input_tokens=1)] * (USAGE_BATCH_LIMIT + 1))


@pytest.mark.asyncio
async def test_backfill_writes_each_row_and_audits_the_batch_once(monkeypatch):
    applied = []
    events = []

    async def apply(db, actor, row, *, when=None):
        applied.append((row.input_tokens, row.output_tokens))

    async def bypass(db, enabled):
        events.append(("bypass", enabled))

    async def audit_write(self, **kwargs):
        events.append(kwargs["action"])
        assert kwargs["details"] == {"written": 2}

    monkeypatch.setattr("src.modules.platform.ai_usage.apply_usage", apply)
    monkeypatch.setattr("src.modules.fees.tenant.set_rls_bypass", bypass)
    monkeypatch.setattr("src.modules.audit.repository.AuditRepository.write", audit_write)

    result = await backfill_ai_usage(
        object(),
        _user(),
        [AiUsageIn(input_tokens=2, output_tokens=1), AiUsageIn(output_tokens=4)],
    )
    assert result == {"written": 2}
    assert applied == [(2, 1), (0, 4)]
    assert events == [("bypass", True), "platform.ai_usage.backfill"]


def test_school_token_rows_include_zero_schools_and_split_models():
    alpha = UUID("00000000-0000-0000-0000-0000000000aa")
    beta = UUID("00000000-0000-0000-0000-0000000000bb")
    rows = school_token_rows(
        [
            (alpha, "Alpha", "qwen", 5),
            (alpha, "Alpha", "deepseek", 1),
            (beta, "Beta", None, 0),
        ]
    )
    assert rows[0]["school_name"] == "Alpha"
    assert rows[0]["tokens"] == 6
    assert rows[0]["models"] == [
        {"model": "qwen", "tokens": 5},
        {"model": "deepseek", "tokens": 1},
    ]
    assert "estimated_cost" not in rows[0]
    assert "cost" not in rows[0]
    assert rows[1]["school_name"] == "Beta"
    assert rows[1]["tokens"] == 0
    assert rows[1]["models"] == []


def test_person_models_come_from_the_usage_table():
    assert model_breakdown([]) == []
    assert model_breakdown([("qwen", 3), ("qwen", 2), ("deepseek", 1)]) == [
        {"model": "qwen", "tokens": 5},
        {"model": "deepseek", "tokens": 1},
    ]
    assert "recorded" not in inspect.getsource(school_people_tokens)
    assert "recorded" not in inspect.getsource(model_breakdown)


def test_ai_token_csv_has_school_model_tokens_and_user_without_a_price():
    rows = ai_token_export_rows(
        daily=[("North", "qwen", 10), ("South", "qwen", 2)],
        people=[("North", "qwen", 4, "Ada"), ("North", "qwen", 3, "Bea")],
    )
    assert rows == [
        {"school": "North", "model": "qwen", "tokens": 3, "user": ""},
        {"school": "North", "model": "qwen", "tokens": 4, "user": "Ada"},
        {"school": "North", "model": "qwen", "tokens": 3, "user": "Bea"},
        {"school": "South", "model": "qwen", "tokens": 2, "user": ""},
    ]
    text = render_csv(COLUMNS["ai-tokens"], [])
    assert text.strip() == "school,model,tokens,user"
    filled = render_csv(COLUMNS["ai-tokens"], rows)
    assert "estimated_cost" not in filled
    assert "price" not in filled
    assert "Ada" in filled


def test_csv_reports_have_no_price_columns_and_empty_is_a_header():
    for headers in COLUMNS.values():
        blob = ",".join(headers).lower()
        assert "price" not in blob
        assert "cost" not in blob
        assert "amount" not in blob
        assert "estimated_cost" not in blob
    assert set(COLUMNS) == {
        "schools",
        "trials",
        "renewals",
        "billing",
        "ai-tokens",
        "sign-ins",
        "logs",
    }
    assert render_csv(COLUMNS["schools"], []).strip() == ",".join(COLUMNS["schools"])


def test_report_access_splits_members_and_super_admins():
    member = _user(email="ops@sybeez.ai", platform_role="ADMIN")
    owner = _user()
    for kind in ("schools", "trials", "renewals", "ai-tokens"):
        assert report_audience(kind) == "member"
        assert assert_report_access(kind, member) == "member"
    for kind in ("billing", "sign-ins", "logs"):
        assert report_audience(kind) == "super"
        assert assert_report_access(kind, owner) == "super"
        with pytest.raises(ForbiddenError):
            assert_report_access(kind, member)
    with pytest.raises(NotFoundError):
        report_audience("prices")
    with pytest.raises(NotFoundError):
        assert_report_access("nope", owner)


def test_usage_and_report_routes_require_a_signed_in_user():
    paths = client.get("/openapi.json").json()["paths"]
    assert "post" in paths["/api/v1/platform/ai/usage"]
    assert "/api/v1/platform/ai/usage/backfill" in paths
    assert "/api/v1/platform/reports/{kind}" in paths
    assert client.post("/api/v1/platform/ai/usage", json={"input_tokens": 1}).status_code == 401
    assert client.post("/api/v1/platform/ai/usage/backfill", json={"rows": []}).status_code == 401
    assert client.get("/api/v1/platform/reports/schools").status_code == 401
    assert client.get("/api/v1/platform/reports/billing").status_code == 401
    assert client.get("/api/v1/platform/reports/logs").status_code == 401


@pytest.mark.asyncio
async def test_delete_plan_turns_on_rls_bypass_before_subscriptions(monkeypatch):
    events = []

    class Result:
        def scalars(self):
            return self

        def all(self):
            events.append("subscriptions")
            return []

    class FakeDB:
        async def get(self, model, plan_id):
            events.append("plan")
            return SimpleNamespace(id=plan_id, code="starter", name="Starter")

        async def execute(self, statement):
            events.append("execute")
            return Result()

        async def delete(self, obj):
            events.append("delete-plan")

        async def flush(self):
            events.append("flush")

    async def bypass(db, enabled):
        events.append(f"bypass:{enabled}")

    async def audit_write(self, **kwargs):
        events.append(kwargs["action"])
        assert kwargs["resource_type"] == "platform_plan"

    monkeypatch.setattr("src.modules.fees.tenant.set_rls_bypass", bypass)
    monkeypatch.setattr("src.modules.audit.repository.AuditRepository.write", audit_write)

    from src.modules.platform.saas_service import PlatformSaasService

    plan_id = uuid4()
    await PlatformSaasService(FakeDB()).delete_plan(_user(), plan_id)
    assert events[0] == "bypass:True"
    assert events.index("bypass:True") < events.index("subscriptions")
    assert events.index("bypass:True") < events.index("delete-plan")
    assert "platform.super_admin.delete" in events


def test_school_and_admin_deletes_enable_bypass_before_the_write():
    from src.modules.platform.service import PlatformService

    school = inspect.getsource(PlatformService.delete_school)
    admin = inspect.getsource(PlatformService.remove_school_admin)
    assert school.index("open_platform_school") < school.index("DELETE FROM")
    assert "confirm_name" in school
    assert "platform.super_admin.delete" in school
    assert admin.index("open_platform_school") < admin.index("LEFT")
    assert "platform.super_admin.delete" in admin
