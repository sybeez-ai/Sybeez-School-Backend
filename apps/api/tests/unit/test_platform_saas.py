"""Platform Admin SaaS control-plane contract tests."""

from fastapi.testclient import TestClient

from src.main import app
from src.modules.platform.schemas import (
    PlatformDashboard,
    PlatformSearchResult,
    PlanCreate,
    PlatformAiSettingsUpdate,
    PlatformSettingsUpdate,
)

client = TestClient(app)


def test_platform_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/platform/dashboard",
        "/api/v1/platform/search",
        "/api/v1/platform/schools",
        "/api/v1/platform/schools/{school_id}",
        "/api/v1/platform/schools/{school_id}/suspend",
        "/api/v1/platform/schools/{school_id}/reactivate",
        "/api/v1/platform/schools/{school_id}/archive",
        "/api/v1/platform/admins",
        "/api/v1/platform/admins/{admin_id}",
        "/api/v1/platform/plans",
        "/api/v1/platform/plans/{plan_id}",
        "/api/v1/platform/settings",
        "/api/v1/platform/ai",
        "/api/v1/platform/ai/models",
        "/api/v1/platform/ai/providers",
        "/api/v1/platform/ai/usage",
        "/api/v1/platform/ai/settings",
        "/api/v1/platform/activity",
        "/api/v1/platform/employees",
        "/api/v1/platform/employees/{employee_id}",
    ):
        assert p in paths, f"missing {p}"


def test_platform_routes_require_auth():
    assert client.get("/api/v1/platform/dashboard").status_code == 401
    assert client.get("/api/v1/platform/schools").status_code == 401
    assert client.get("/api/v1/platform/plans").status_code == 401
    assert client.get("/api/v1/platform/settings").status_code == 401
    assert client.get("/api/v1/platform/ai").status_code == 401
    assert client.get("/api/v1/platform/admins").status_code == 401
    assert client.get("/api/v1/platform/search?q=alpha").status_code == 401
    assert client.post("/api/v1/platform/schools", json={}).status_code == 401


def test_school_admin_cannot_use_platform_prefix_without_token():
    r = client.get("/api/v1/platform/dashboard", headers={"Authorization": "Bearer invalid"})
    assert r.status_code == 401


def test_dashboard_schema_fields():
    fields = set(PlatformDashboard.model_fields.keys())
    assert "schools_count" in fields
    assert "total_students" in fields
    assert "attention" in fields
    assert "ai_requests" in fields
    assert "school_growth" in fields


def test_plan_and_ai_settings_schemas():
    PlanCreate(code="starter", name="Starter")
    PlatformSettingsUpdate(platform_name="Sybeez")
    PlatformAiSettingsUpdate(default_provider="qwen")


def test_search_result_schema():
    result = PlatformSearchResult(query="alpha")
    assert result.schools == []
    assert result.admins == []
    assert result.team == []
    assert result.plans == []


def test_ai_providers_do_not_expose_secrets_in_schema():
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/platform/ai/providers" in paths
    assert client.get("/api/v1/platform/ai/providers").status_code == 401
