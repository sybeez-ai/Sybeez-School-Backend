from datetime import datetime, timezone
from uuid import uuid4

from src.modules.audit.schemas import AuditLogOut


def test_audit_listing_never_500s_on_reserved_or_legacy_email():
    for email in ("a@rls.test", "seed@school.local", "x@example.test"):
        out = AuditLogOut(id=uuid4(), created_at=datetime.now(timezone.utc), action="x", actor_email=email)
        assert out.actor_email == email
