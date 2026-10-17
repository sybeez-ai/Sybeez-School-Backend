"""Self-service account deletion: account_deletions audit table and app.delete_my_account().

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-17

The user row is anonymized in place, never removed: hundreds of school records (fees,
results, payroll, audit) reference users.id and schools must keep them for their retention
duties. All personal data is cleared, credentials, sessions and push tokens are destroyed, and
every membership is closed. Removal is done by a SECURITY DEFINER function owned by
sybeez_definer (BYPASSRLS) because a user can belong to several schools while the request
only carries one school in app.school_id. The function acts only on app.user_id, the caller.

account_deletions holds no personal data (no email or name); it is the tamper-evident record
that the deletion happened. RLS is explicit and forced: only platform admins and the service
role can read it; nobody can write it except through the function.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0065"
down_revision: Union[str, None] = "0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FN = "app.delete_my_account(text, text, text)"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS account_deletions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id uuid NOT NULL,
          school_ids uuid[] NOT NULL DEFAULT '{}',
          roles text[] NOT NULL DEFAULT '{}',
          reauth_method text NOT NULL,
          cognito_status text NOT NULL DEFAULT 'skipped',
          ip_address text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_account_deletions_user_id ON account_deletions (user_id)")
    op.execute("GRANT SELECT ON account_deletions TO sybeez_app")
    op.execute("ALTER TABLE account_deletions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE account_deletions FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS account_deletions_read ON account_deletions")
    op.execute(
        """
        CREATE POLICY account_deletions_read ON account_deletions FOR SELECT
        USING (
          current_setting('app.rls_bypass', true) = 'on'
          OR current_setting('app.role', true) IN ('PLATFORM_ADMIN', 'SERVICE')
        )
        """
    )
    op.execute("DROP POLICY IF EXISTS account_deletions_write ON account_deletions")
    op.execute(
        """
        CREATE POLICY account_deletions_write ON account_deletions FOR ALL
        USING (current_setting('app.rls_bypass', true) = 'on')
        WITH CHECK (current_setting('app.rls_bypass', true) = 'on')
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.delete_my_account(
          p_reauth_method text, p_cognito_status text, p_ip text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
          uid uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
          u users%ROWTYPE;
          sids uuid[];
          rls text[];
          rid uuid;
          sid uuid;
        BEGIN
          IF uid IS NULL THEN
            RAISE EXCEPTION 'delete_my_account requires an authenticated user';
          END IF;
          SELECT * INTO u FROM users WHERE id = uid FOR UPDATE;
          IF NOT FOUND OR NOT u.is_active THEN
            RAISE EXCEPTION 'account not found';
          END IF;
          IF u.is_platform_admin OR u.platform_role IS NOT NULL THEN
            RAISE EXCEPTION 'platform accounts cannot be self-deleted';
          END IF;
          IF EXISTS (
            SELECT 1 FROM school_memberships
            WHERE user_id = uid AND status = 'ACTIVE'
              AND role NOT IN ('STUDENT', 'PARENT', 'TEACHER', 'STAFF')
          ) THEN
            RAISE EXCEPTION 'this role cannot be self-deleted';
          END IF;

          SELECT COALESCE(array_agg(DISTINCT school_id), '{}'), COALESCE(array_agg(DISTINCT role), '{}')
            INTO sids, rls FROM school_memberships WHERE user_id = uid;

          -- Credentials, sessions, devices and private content: destroyed.
          DELETE FROM refresh_tokens WHERE user_id = uid;
          DELETE FROM push_devices WHERE user_id = uid;
          DELETE FROM personal_habit_checkins WHERE user_id = uid;
          DELETE FROM personal_habits WHERE user_id = uid;
          DELETE FROM personal_goals WHERE user_id = uid;
          DELETE FROM personal_notes WHERE user_id = uid;
          DELETE FROM ai_conversations WHERE user_id = uid;
          DELETE FROM student_ai_memory WHERE student_id IN (SELECT id FROM students WHERE user_id = uid);
          DELETE FROM notification_deliveries WHERE user_id = uid;
          DELETE FROM notifications WHERE user_id = uid;
          DELETE FROM notification_preferences WHERE user_id = uid;
          DELETE FROM alert_channel_opt_outs WHERE user_id = uid;

          -- School profile rows are kept (retention) but carry no personal data.
          UPDATE students SET date_of_birth = NULL, gender = NULL, blood_group = NULL, nationality = NULL,
            phone = NULL, address_line1 = NULL, address_line2 = NULL, city = NULL, state = NULL,
            postal_code = NULL, country = NULL, photo_url = NULL, status = 'LEFT', updated_at = now()
            WHERE user_id = uid;
          UPDATE teachers SET date_of_birth = NULL, phone = NULL, qualification = NULL, photo_url = NULL,
            status = 'LEFT', updated_at = now()
            WHERE user_id = uid;
          UPDATE parents SET phone = NULL, status = 'LEFT', updated_at = now() WHERE user_id = uid;
          UPDATE school_memberships SET status = 'LEFT', updated_at = now() WHERE user_id = uid;
          UPDATE invitations SET status = 'REVOKED', email = 'deleted-' || uid::text || '@deleted.invalid'
            WHERE lower(email) = lower(u.email);

          UPDATE users SET
            email = 'deleted-' || uid::text || '@deleted.invalid',
            full_name = 'Deleted user', first_name = NULL, middle_name = NULL, last_name = NULL,
            phone = NULL, avatar_url = NULL, password_hash = NULL, cognito_sub = NULL,
            status = 'DELETED', is_active = false, must_change_password = false, updated_at = now()
            WHERE id = uid;

          INSERT INTO account_deletions (user_id, school_ids, roles, reauth_method, cognito_status, ip_address)
            VALUES (uid, sids, rls, p_reauth_method, p_cognito_status, p_ip)
            RETURNING id INTO rid;
          -- Each school sees that one of its users removed their account, without who they were.
          FOREACH sid IN ARRAY sids LOOP
            INSERT INTO audit_logs (id, school_id, actor_id, action, resource_type, resource_id, ip_address,
                                    details, message, created_at, updated_at)
            VALUES (gen_random_uuid(), sid, uid, 'account.deleted', 'user', uid::text, p_ip,
                    jsonb_build_object('roles', rls, 'reauth', p_reauth_method), 'User deleted their account',
                    now(), now());
          END LOOP;
          RETURN rid;
        END;
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {FN} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {FN} TO sybeez_app")
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
            ALTER FUNCTION app.delete_my_account(text, text, text) OWNER TO sybeez_definer;
            GRANT SELECT, UPDATE ON users, students, teachers, parents, school_memberships, invitations
              TO sybeez_definer;
            GRANT SELECT, INSERT ON account_deletions, audit_logs TO sybeez_definer;
            GRANT SELECT, DELETE ON refresh_tokens, push_devices, personal_habit_checkins, personal_habits,
              personal_goals, personal_notes, ai_conversations, student_ai_memory, notification_deliveries,
              notifications, notification_preferences, alert_channel_opt_outs TO sybeez_definer;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FN}")
    op.execute("DROP POLICY IF EXISTS account_deletions_write ON account_deletions")
    op.execute("DROP POLICY IF EXISTS account_deletions_read ON account_deletions")
    op.execute("DROP TABLE IF EXISTS account_deletions")
