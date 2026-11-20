"""School stores are reachable at the path the admin screens call, and at the older /ops path."""

from src.api.router import api_router
from src.modules.school_ops.router import router as school_ops_router


def test_school_ops_assets_exist_on_both_prefixes():
    leaves = {route.path for route in school_ops_router.routes}
    for name in ("/assets", "/inventory", "/procurement", "/maintenance"):
        assert name in leaves
    prefixes = set()
    for included in api_router.routes:
        context = getattr(included, "include_context", None)
        router = getattr(context, "included_router", None)
        if router is school_ops_router and context is not None:
            prefixes.add(context.prefix)
    assert prefixes == {"/api/v1/ops", "/api/v1/school-ops"}
