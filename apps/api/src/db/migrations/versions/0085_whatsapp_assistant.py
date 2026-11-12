"""WhatsApp assistant tables. Additive. Sending stays off until WHATSAPP_SENDING_ENABLED is set.

Revision ID: 0085
Revises: 0084
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0085"
down_revision: Union[str, None] = "0084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "whatsapp_assistant_settings",
    "whatsapp_links",
    "whatsapp_link_codes",
    "whatsapp_sessions",
    "whatsapp_messages",
    "whatsapp_usage",
)

BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
SCHOOL = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
ROLE = "NULLIF(current_setting('app.role', true), '')"
USER = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
ADMIN = f"{ROLE} IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE')"


def _policy(table: str, read: str, write: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sybeez_app")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {table}_select ON {table} FOR SELECT USING ({read})")
    op.execute(f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK ({write})")
    op.execute(f"CREATE POLICY {table}_update ON {table} FOR UPDATE USING ({write}) WITH CHECK ({write})")
    op.execute(f"CREATE POLICY {table}_delete ON {table} FOR DELETE USING ({write})")


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import whatsapp_assistant as _models

    del _models
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables[name] for name in TABLES], checkfirst=True)
    school_read = f"{BYPASS} OR ({SCHOOL} AND {ADMIN})"
    school_write = school_read
    _policy("whatsapp_assistant_settings", school_read, school_write)
    _policy("whatsapp_usage", school_read, school_write)
    own = f"{BYPASS} OR {USER} OR {ROLE} IN ('PLATFORM_ADMIN', 'SERVICE')"
    _policy("whatsapp_links", own, own)
    _policy("whatsapp_link_codes", own, own)
    session_read = f"{BYPASS} OR ({SCHOOL} AND {ADMIN})"
    _policy("whatsapp_sessions", session_read, f"{BYPASS} OR ({SCHOOL} AND {ADMIN})")
    message_read = f"{BYPASS} OR ({SCHOOL} AND {ADMIN})"
    _policy("whatsapp_messages", message_read, f"{BYPASS} OR ({SCHOOL} AND {ADMIN})")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
