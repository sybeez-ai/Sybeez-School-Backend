"""Mobile push device tokens.

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-12

Rows are owned by a user: the RLS policy only shows a user their own devices.
app.claim_push_token() (SECURITY DEFINER) upserts a token for the *calling* user
(app.user_id), so a phone that switches accounts moves its token without letting
anyone read or write another user's rows.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0062"
down_revision: Union[str, None] = "0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import push_device as push_models

    del push_models
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["push_devices"]], checkfirst=True)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON push_devices TO sybeez_app")
    op.execute("ALTER TABLE push_devices ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE push_devices FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS push_devices_owner ON push_devices")
    op.execute(
        """
        CREATE POLICY push_devices_owner ON push_devices
        USING (
          current_setting('app.rls_bypass', true) = 'on'
          OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
        WITH CHECK (
          current_setting('app.rls_bypass', true) = 'on'
          OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.claim_push_token(
          p_token text, p_platform text, p_provider text, p_school uuid
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
          uid uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
          rid uuid;
        BEGIN
          IF uid IS NULL THEN
            RAISE EXCEPTION 'claim_push_token requires an authenticated user';
          END IF;
          INSERT INTO push_devices (id, user_id, school_id, token, platform, provider, last_seen_at, created_at, updated_at)
          VALUES (gen_random_uuid(), uid, p_school, p_token, p_platform, p_provider, now(), now(), now())
          ON CONFLICT (token) DO UPDATE
            SET user_id = uid, school_id = p_school, platform = p_platform, provider = p_provider,
                last_seen_at = now(), updated_at = now()
          RETURNING id INTO rid;
          RETURN rid;
        END;
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION app.claim_push_token(text, text, text, uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.claim_push_token(text, text, text, uuid) TO sybeez_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app.claim_push_token(text, text, text, uuid)")
    op.execute("DROP POLICY IF EXISTS push_devices_owner ON push_devices")
    op.execute("DROP TABLE IF EXISTS push_devices")
