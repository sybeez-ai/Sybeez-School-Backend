"""Deleted accounts leave class rosters: delete_my_account withdraws the student's active enrollments.

Revision ID: 0080
Revises: 0079
Create Date: 2026-10-19

Account deletion already anonymises the user ("Deleted user") and marks the student LEFT, but left the
enrollment ACTIVE, so the student kept showing in class lists, rosters and counts. The function now also sets
the student's ACTIVE enrollments to WITHDRAWN (same as an admin withdrawal), and students whose account was
already deleted get the same treatment. Nothing is hard-deleted.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0080"
down_revision: Union[str, None] = "0079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FN = "app.delete_my_account(text, text, text)"

FUNCTION_SQL = """
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
          DELETE FROM student_learning_profiles WHERE student_user_id = uid;
          DELETE FROM parent_message_preferences WHERE user_id = uid;
          DELETE FROM reading_sessions WHERE student_id IN (SELECT id FROM students WHERE user_id = uid);
          DELETE FROM reading_progress WHERE student_id IN (SELECT id FROM students WHERE user_id = uid);
          DELETE FROM student_progress_updates WHERE student_id IN (SELECT id FROM students WHERE user_id = uid);
          UPDATE substitute_assignments SET note = NULL, created_by = NULL WHERE created_by = uid;
          DELETE FROM ai_grade_suggestions WHERE submission_id IN (
            SELECT hs.id FROM homework_submissions hs JOIN students st ON st.id = hs.student_id WHERE st.user_id = uid);
          UPDATE ai_grade_suggestions SET requested_by = NULL WHERE requested_by = uid;
          UPDATE ai_grade_suggestions SET decided_by = NULL WHERE decided_by = uid;
          DELETE FROM family_documents WHERE student_id IN (SELECT id FROM students WHERE user_id = uid);
          DELETE FROM exam_mock_attempts WHERE student_id IN (SELECT id FROM students WHERE user_id = uid);
          UPDATE family_documents SET uploaded_by = NULL WHERE uploaded_by = uid;

          -- School profile rows are kept (retention) but carry no personal data.
          UPDATE students SET date_of_birth = NULL, gender = NULL, blood_group = NULL, nationality = NULL,
            phone = NULL, address_line1 = NULL, address_line2 = NULL, city = NULL, state = NULL,
            postal_code = NULL, country = NULL, photo_url = NULL, status = 'LEFT', updated_at = now()
            WHERE user_id = uid;
          UPDATE teachers SET date_of_birth = NULL, phone = NULL, qualification = NULL, photo_url = NULL,
            status = 'LEFT', updated_at = now()
            WHERE user_id = uid;
          UPDATE parents SET phone = NULL, status = 'LEFT', updated_at = now() WHERE user_id = uid;
          -- A deleted student leaves every class roster: close the active placements (rows are kept).
          UPDATE enrollments SET status = 'WITHDRAWN', left_at = COALESCE(left_at, now()), updated_at = now()
            WHERE status = 'ACTIVE' AND student_id IN (SELECT id FROM students WHERE user_id = uid);
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

PREVIOUS_FUNCTION_SQL = """
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
          DELETE FROM student_learning_profiles WHERE student_user_id = uid;
          DELETE FROM parent_message_preferences WHERE user_id = uid;
          DELETE FROM reading_sessions WHERE student_id IN (SELECT id FROM students WHERE user_id = uid);
          DELETE FROM reading_progress WHERE student_id IN (SELECT id FROM students WHERE user_id = uid);
          DELETE FROM student_progress_updates WHERE student_id IN (SELECT id FROM students WHERE user_id = uid);
          UPDATE substitute_assignments SET note = NULL, created_by = NULL WHERE created_by = uid;
          DELETE FROM ai_grade_suggestions WHERE submission_id IN (
            SELECT hs.id FROM homework_submissions hs JOIN students st ON st.id = hs.student_id WHERE st.user_id = uid);
          UPDATE ai_grade_suggestions SET requested_by = NULL WHERE requested_by = uid;
          UPDATE ai_grade_suggestions SET decided_by = NULL WHERE decided_by = uid;
          DELETE FROM family_documents WHERE student_id IN (SELECT id FROM students WHERE user_id = uid);
          DELETE FROM exam_mock_attempts WHERE student_id IN (SELECT id FROM students WHERE user_id = uid);
          UPDATE family_documents SET uploaded_by = NULL WHERE uploaded_by = uid;

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

GRANTS_SQL = """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
            ALTER FUNCTION app.delete_my_account(text, text, text) OWNER TO sybeez_definer;
            GRANT SELECT, UPDATE ON users, students, teachers, parents, school_memberships, invitations
              TO sybeez_definer;
            GRANT SELECT, INSERT ON account_deletions, audit_logs TO sybeez_definer;
            GRANT SELECT, DELETE ON refresh_tokens, push_devices, personal_habit_checkins, personal_habits,
              personal_goals, personal_notes, ai_conversations, student_ai_memory, notification_deliveries,
              notifications, notification_preferences, alert_channel_opt_outs TO sybeez_definer;
            GRANT SELECT, DELETE ON student_learning_profiles, parent_message_preferences, reading_sessions,
              reading_progress, student_progress_updates TO sybeez_definer;
            GRANT SELECT, UPDATE ON substitute_assignments TO sybeez_definer;
            GRANT SELECT, UPDATE, DELETE ON ai_grade_suggestions TO sybeez_definer;
            GRANT SELECT ON homework_submissions TO sybeez_definer;
            GRANT SELECT, UPDATE, DELETE ON family_documents TO sybeez_definer;
            GRANT SELECT, DELETE ON exam_mock_attempts TO sybeez_definer;
          END IF;
        END $$;
"""


def upgrade() -> None:
    op.execute(FUNCTION_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION {FN} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {FN} TO sybeez_app")
    op.execute(GRANTS_SQL)
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
            GRANT SELECT, UPDATE ON enrollments TO sybeez_definer;
          END IF;
        END $$;
        """
    )
    # Students whose account was deleted before this fix: close their still-active placements.
    # Tenant tables use forced RLS; this one-off backfill runs with the documented bypass switch.
    op.execute("SELECT set_config('app.rls_bypass', 'on', true)")
    op.execute(
        """
        UPDATE enrollments e SET status = 'WITHDRAWN', left_at = COALESCE(e.left_at, now()), updated_at = now()
          FROM students s JOIN users u ON u.id = s.user_id
         WHERE e.student_id = s.id AND e.status = 'ACTIVE' AND u.status = 'DELETED'
        """
    )


def downgrade() -> None:
    op.execute(PREVIOUS_FUNCTION_SQL)
    op.execute(f"REVOKE ALL ON FUNCTION {FN} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {FN} TO sybeez_app")
    op.execute(GRANTS_SQL)
