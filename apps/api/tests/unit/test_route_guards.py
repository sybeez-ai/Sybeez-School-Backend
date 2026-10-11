"""Every HTTP route is public, service-authenticated, or permission-gated."""

from fastapi.routing import APIRoute

from src.main import app

_SKIP = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _walk(router, prefix=""):
    found = []
    for route in router.routes:
        if isinstance(route, APIRoute):
            found.append((prefix + route.path, route))
        elif type(route).__name__ == "_IncludedRouter":
            ctx = route.include_context
            found.extend(_walk(route.original_router, prefix + (getattr(ctx, "prefix", "") or "")))
    return found


def _permission_codes(dependant) -> set[str]:
    codes = set()
    call = getattr(dependant, "call", None)
    code = getattr(call, "__permission_code__", None)
    if code:
        codes.add(code)
    for child in getattr(dependant, "dependencies", []) or []:
        codes |= _permission_codes(child)
    return codes


def _marked(dependant, attribute: str) -> bool:
    call = getattr(dependant, "call", None)
    if getattr(call, attribute, False):
        return True
    return any(_marked(child, attribute) for child in getattr(dependant, "dependencies", []) or [])


def _authenticated(dependant) -> bool:
    call = getattr(dependant, "call", None)
    name = getattr(call, "__name__", "")
    if name in {
        "get_current_user",
        "get_scoped_user",
        "require_school_scope",
        "require_super_admin",
        "require_admin_or_above",
        "require_hr_admin",
        "require_internal_token",
    }:
        return True
    if getattr(call, "__service_auth__", False):
        return True
    if getattr(call, "__roles__", None):
        return True
    return any(_authenticated(child) for child in getattr(dependant, "dependencies", []) or [])


def test_every_endpoint_declares_an_access_mode():
    routes = [(path, route) for path, route in _walk(app.router) if path not in _SKIP]
    assert len(routes) > 40
    missing = []
    for path, route in routes:
        endpoint = route.endpoint
        public = getattr(endpoint, "__public__", False)
        service = getattr(endpoint, "__service_auth__", False)
        codes = _permission_codes(route.dependant)
        super_admin = _marked(route.dependant, "__super_admin__")
        if public or service or codes or super_admin or _authenticated(route.dependant):
            continue
        missing.append(f"{sorted(route.methods)} {path}")
    assert missing == []


def test_public_routes_are_only_health_and_auth_entry():
    public = sorted(
        path
        for path, route in _walk(app.router)
        if getattr(route.endpoint, "__public__", False)
    )
    assert public == [
        "/api/v1/admissions/health",
        "/api/v1/ai-reports/health",
        "/api/v1/assignments/health",
        "/api/v1/attendance/health",
        "/api/v1/audit/health",
        "/api/v1/auth/accept-invitation",
        "/api/v1/auth/accept-staff-invitation",
        "/api/v1/auth/cognito/callback",
        "/api/v1/auth/cognito/google/start",
        "/api/v1/auth/cognito/result",
        "/api/v1/auth/complete-new-password",
        "/api/v1/auth/confirm-forgot-password",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/refresh",
        "/api/v1/bookstore/health",
        "/api/v1/communication/health",
        "/api/v1/diary/health",
        "/api/v1/exams/health",
        "/api/v1/fees/health",
        "/api/v1/finance/health",
        "/api/v1/homework/health",
        "/api/v1/hr/health",
        "/api/v1/notes/health",
        "/api/v1/payments/webhook/{provider}",
        "/api/v1/payroll/assistant/downloads/{token}",
        "/api/v1/personal-life/health",
        "/api/v1/results/health",
        "/api/v1/webhooks/sms/msg91",
        "/api/v1/webhooks/whatsapp/meta",
        "/api/v1/webhooks/whatsapp/meta",
        "/health",
        "/ready",
    ]


def test_internal_ai_context_is_service_auth_not_anonymous():
    matches = [route for path, route in _walk(app.router) if path == "/internal/v1/students/ai-context"]
    assert len(matches) == 1
    assert getattr(matches[0].endpoint, "__service_auth__", False) is True
    assert _permission_codes(matches[0].dependant) == set()
