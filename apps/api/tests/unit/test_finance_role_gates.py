"""Finance portal roles must reach the desk reads and setup writes the audit blocked."""

from fastapi.testclient import TestClient

from src.main import app
from src.shared.enums import UserRole

client = TestClient(app)

VIEW = {
    UserRole.SCHOOL_ADMIN.value,
    UserRole.ACCOUNTANT.value,
    UserRole.CASHIER.value,
    UserRole.AUDITOR.value,
}
SETUP = {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value}
COLLECT = {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value, UserRole.CASHIER.value}


def _iter_routes(router, prefix: str = ""):
    for route in getattr(router, "routes", []):
        if type(route).__name__ == "_IncludedRouter":
            child = route.include_context.prefix or ""
            yield from _iter_routes(route.original_router, prefix + child)
            continue
        path = prefix + (getattr(route, "path", "") or "")
        yield path, route


def route_roles(path: str, method: str) -> set[str]:
    found: set[str] | None = None
    roots = []
    for route in app.routes:
        if type(route).__name__ == "_IncludedRouter":
            roots.append(route.original_router)
        else:
            roots.append(route)
    for root in roots:
        routes = _iter_routes(root) if hasattr(root, "routes") else [(getattr(root, "path", ""), root)]
        for route_path, route in routes:
            if route_path != path:
                continue
            if method.upper() not in (getattr(route, "methods", None) or set()):
                continue
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            stack = list(dependant.dependencies)
            while stack:
                dep = stack.pop()
                stack.extend(getattr(dep, "dependencies", []) or [])
                call = dep.call
                code = getattr(call, "__code__", None)
                closure = getattr(call, "__closure__", None)
                if not code or not closure or "roles" not in code.co_freevars:
                    continue
                roles = closure[code.co_freevars.index("roles")].cell_contents
                found = {role.value for role in roles}
    assert found is not None, path
    return found


def test_finance_view_roles_can_read_desk():
    reads = (
        ("/api/v1/fees/dashboard", "GET"),
        ("/api/v1/fees/reports/collection", "GET"),
        ("/api/v1/fees/reports/outstanding", "GET"),
        ("/api/v1/fees/students", "GET"),
        ("/api/v1/fees/structures", "GET"),
        ("/api/v1/fees/structures/{structure_id}", "GET"),
        ("/api/v1/fees/late-fee-rules", "GET"),
        ("/api/v1/fees/online-payments/reconciliation", "GET"),
    )
    for path, method in reads:
        assert route_roles(path, method) == VIEW


def test_finance_setup_roles_can_write_structures_and_demands():
    writes = (
        ("/api/v1/fees/structures", "POST"),
        ("/api/v1/fees/structures/{structure_id}", "PATCH"),
        ("/api/v1/fees/structures/{structure_id}/publish", "POST"),
        ("/api/v1/fees/structures/{structure_id}/items", "POST"),
        ("/api/v1/fees/structures/{structure_id}/installments", "POST"),
        ("/api/v1/fees/invoices/generate", "POST"),
        ("/api/v1/fees/demands/generate", "POST"),
        ("/api/v1/fees/invoices/{invoice_id}/cancel", "POST"),
        ("/api/v1/fees/late-fee-rules", "POST"),
        ("/api/v1/fees/late-fees/apply", "POST"),
        ("/api/v1/fees/reminders/run", "POST"),
        ("/api/v1/fees/settings", "GET"),
        ("/api/v1/fees/settings", "PATCH"),
    )
    for path, method in writes:
        roles = route_roles(path, method)
        assert roles == SETUP
        assert UserRole.CASHIER.value not in roles
        assert UserRole.AUDITOR.value not in roles


def test_payment_link_is_collect_and_new_reads_exist():
    assert route_roles("/api/v1/fees/payment-links", "POST") == COLLECT
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/v1/fees/payment-links",
        "/api/v1/fees/online-payments/reconciliation",
        "/api/v1/fees/demands/generate",
        "/api/v1/finance/notifications",
        "/api/v1/finance/reports/branch-wise",
        "/api/v1/finance/reports/category-wise",
    ):
        assert path in paths
    assert client.post("/api/v1/fees/payment-links", json={}).status_code == 401
    assert client.get("/api/v1/fees/online-payments/reconciliation").status_code == 401
    assert client.get("/api/v1/finance/notifications").status_code == 401
    assert client.get("/api/v1/finance/reports/branch-wise").status_code == 401
    assert client.get("/api/v1/finance/reports/category-wise").status_code == 401
