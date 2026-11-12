"""Platform access-control tables and default permission matrix.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-11

Renumbered from platform 9001 so it follows the single release chain.
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS permissions (
            code varchar(64) PRIMARY KEY,
            description varchar(255) NOT NULL,
            module varchar(64) NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS role_permissions (
            role varchar(32) NOT NULL,
            permission_code varchar(64) NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
            PRIMARY KEY (role, permission_code)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS membership_permission_overrides (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            school_id uuid NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            membership_id uuid NOT NULL REFERENCES school_memberships(id) ON DELETE CASCADE,
            permission_code varchar(64) NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
            effect varchar(16) NOT NULL,
            granted_by uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_membership_permission_override UNIQUE (membership_id, permission_code),
            CONSTRAINT ck_membership_permission_effect CHECK (effect IN ('GRANT', 'REVOKE'))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_membership_permission_overrides_school_id
        ON membership_permission_overrides (school_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_membership_permission_overrides_membership_id
        ON membership_permission_overrides (membership_id)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes WHERE indexname = 'uq_homework_submission_once'
            ) THEN
                BEGIN
                    CREATE UNIQUE INDEX uq_homework_submission_once
                    ON homework_submissions (homework_id, student_id);
                EXCEPTION
                    WHEN unique_violation THEN
                        RAISE NOTICE 'skipped uq_homework_submission_once; duplicate rows exist';
                END;
            END IF;
        END $$;
        """
    )

    from src.modules.access.catalog import PERMISSIONS, ROLE_PERMISSIONS

    bind = op.get_bind()
    for code, description in PERMISSIONS.items():
        bind.execute(
            text(
                """
                INSERT INTO permissions (code, description, module)
                VALUES (:code, :description, :module)
                ON CONFLICT (code) DO UPDATE
                SET description = EXCLUDED.description, module = EXCLUDED.module
                """
            ),
            {"code": code, "description": description, "module": code.split(".", 1)[0]},
        )
    for role, perms in ROLE_PERMISSIONS.items():
        for perm in perms:
            bind.execute(
                text(
                    """
                    INSERT INTO role_permissions (role, permission_code)
                    VALUES (:role, :perm)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"role": role.value, "perm": perm},
            )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_homework_submission_once")
    op.execute("DROP TABLE IF EXISTS membership_permission_overrides")
    op.execute("DROP TABLE IF EXISTS role_permissions")
    op.execute("DROP TABLE IF EXISTS permissions")
