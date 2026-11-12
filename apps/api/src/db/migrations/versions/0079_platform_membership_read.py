"""Let the platform super admin read school memberships across schools (School admins directory).

Revision ID: 0079
Revises: 0078

The school_memberships policy had no cross-school path, so /platform/admins listed nobody. A platform admin session
with no school and the transaction-local rls_bypass flag (set only by the read-only platform handlers) may now see
rows. Writes are unchanged: WITH CHECK still pins every written row to the session's school.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0079"
down_revision: Union[str, None] = "0078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BASE = """
          user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
          OR (
            school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
            AND current_setting('app.role', true) IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE')
          )
"""
CHECK = """
          school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
          AND current_setting('app.role', true) IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE')
"""
PLATFORM = """
          OR (
            current_setting('app.rls_bypass', true) = 'on'
            AND current_setting('app.role', true) = 'PLATFORM_ADMIN'
          )
"""


def _policy(extra: str) -> None:
    op.execute("DROP POLICY IF EXISTS school_memberships_tenant ON school_memberships")
    op.execute(f"CREATE POLICY school_memberships_tenant ON school_memberships USING ({BASE}{extra}) WITH CHECK ({CHECK})")


def upgrade() -> None:
    _policy(PLATFORM)


def downgrade() -> None:
    _policy("")
