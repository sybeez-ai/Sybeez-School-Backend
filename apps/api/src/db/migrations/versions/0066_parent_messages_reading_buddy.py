"""Parent language + WhatsApp consent, cached notice translations, progress updates, reading buddy.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-17

Seven new tables, each with explicit, forced row-level security (no generic loop policy):

* parent_message_preferences - a parent reads and writes only their own row; staff may read.
* message_translations - school-scoped; parents read only published rows for their children/classes,
  and may cache an AUTO translation of a notice they can see; staff and teachers edit.
* student_progress_updates - staff write; only the student's linked parents read (never students).
* reading_passages - school_id NULL is the seeded library (read-only for everyone); school rows
  are written by that school's staff and teachers.
* reading_sessions / reading_progress - the student writes their own rows; the class teachers,
  admins and linked parents read. Nobody deletes except the retention sweeper (RLS bypass).

Also seeds WhatsApp (Meta UTILITY) notice/progress template shells for ten languages. SMS is dropped.
"""

import json
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
from sqlalchemy import text

revision: str = "0066"
down_revision: Union[str, None] = "0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "parent_message_preferences",
    "message_translations",
    "student_progress_updates",
    "reading_passages",
    "reading_sessions",
    "reading_progress",
)

SCHOOL = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
ROLE = "NULLIF(current_setting('app.role', true), '')"
UID = "NULLIF(current_setting('app.user_id', true), '')::uuid"
STAFF = f"{ROLE} IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE')"
STAFF_OR_TEACHER = f"{ROLE} IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE', 'TEACHER')"
STUDENT_OK = "app.can_see('student_data', student_id, NULL::uuid, NULL::uuid)"
TRANSLATION_SCOPE = (
    "((class_id IS NULL AND student_id IS NULL) "
    "OR app.can_see('student_data', student_id, class_id, NULL::uuid))"
)

# (class level, title, text)
PASSAGES = [
    (1, "The Cat", "The cat sat on the mat. It is a big red cat. The cat can run and jump."),
    (1, "My Dog", "I have a dog. His name is Bruno. Bruno likes to play with a ball. We run in the sun."),
    (1, "The Sun", "The sun is up. It is warm and bright. I see the sun in the sky. I like the sun."),
    (2, "A Rainy Day", "It is a rainy day. Mina takes her red umbrella. The rain goes tap, tap, tap on the roof. She jumps in a puddle and laughs."),
    (2, "The Little Bird", "A little bird sits in a tree. It sings a happy song. Then it flies away to find food for its babies."),
    (2, "My School Bag", "My school bag is blue. In it I keep my books, my pencil and my lunch box. I carry it to school every day."),
    (3, "The Honest Woodcutter", "A woodcutter dropped his axe in a river. He was sad. A kind fairy came and showed him a golden axe. He said, \"That is not mine.\" The fairy smiled because he told the truth."),
    (3, "At the Market", "On Sunday, Ravi went to the market with his mother. They bought red apples, green beans and sweet mangoes. Ravi carried the small bag all the way home."),
    (3, "The Moon", "The moon shines in the night sky. It does not make its own light. It shines because the sun lights it up. Some nights it looks round and some nights it looks like a thin smile."),
    (4, "The Water Cycle", "The sun heats the water in rivers and seas. The water turns into vapour and rises into the sky. High up, it cools and forms clouds. When the clouds become heavy, the water falls back as rain. This journey of water is called the water cycle."),
    (4, "The Clever Crow", "A thirsty crow found a jug with a little water at the bottom. His beak could not reach it. He thought for a moment. Then he dropped small stones into the jug, one by one. The water slowly rose, and the happy crow drank."),
    (4, "Our Helpers", "Many people help us every day. The farmer grows our food. The doctor takes care of us when we are ill. The teacher helps us learn. We should always say thank you and treat everyone with respect."),
    (5, "Honey Bees", "Honey bees live together in a hive. Each bee has an important job. Worker bees collect nectar from flowers and turn it into honey. The queen bee lays eggs, and the guard bees protect the hive. Bees also help plants grow by carrying pollen from flower to flower, so we must take care of them."),
    (5, "A Walk in the Forest", "Early in the morning, Asha and her grandfather walked into the forest. The air was cool and smelled of wet leaves. Birds called from the tall trees, and a squirrel scurried up a trunk. Her grandfather said, \"The forest is our greatest teacher, if we learn to listen quietly.\""),
    (5, "Good Habits", "Good habits make a strong person. Waking up early, eating healthy food and drinking enough water keep the body fit. Reading every day makes the mind sharp. Being kind and speaking the truth makes the heart strong. Small habits, practised daily, can change a whole life."),
]

NOTICE_LANGS = ("en", "hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa")


def _policy(table: str, name: str, command: str, using: str | None, check: str | None) -> None:
    parts = [f"CREATE POLICY {name} ON {table} FOR {command}"]
    if using:
        parts.append(f"USING ({using})")
    if check:
        parts.append(f"WITH CHECK ({check})")
    op.execute(" ".join(parts))


def _seed_passages() -> None:
    conn = op.get_bind()
    for level, title, body in PASSAGES:
        conn.execute(
            text(
                """
                INSERT INTO reading_passages (id, school_id, class_level, title, body, word_count, source, is_active)
                SELECT CAST(:id AS uuid), NULL, :lvl, :title, :body, :wc, 'LIBRARY', TRUE
                WHERE NOT EXISTS (
                    SELECT 1 FROM reading_passages WHERE school_id IS NULL AND title = :title2
                )
                """
            ),
            {"id": str(uuid4()), "lvl": level, "title": title, "title2": title, "body": body, "wc": len(body.split())},
        )


def _seed_templates() -> None:
    conn = op.get_bind()
    for event in ("announcement", "progress_update"):
        for code in NOTICE_LANGS:
            conn.execute(
                text(
                    """
                    INSERT INTO alert_message_templates (
                        id, school_id, event_key, locale, whatsapp_template_name, language_code,
                        variables, sms_dlt_template_id, sms_text, body_text, category, active
                    )
                    SELECT CAST(:id AS uuid), NULL, CAST(:ev AS varchar), CAST(:loc AS varchar),
                           CAST(:name AS varchar), CAST(:loc2 AS varchar), CAST(:vars AS jsonb), '', '',
                           '{{1}}: {{2}} - {{3}}', 'UTILITY', TRUE
                    WHERE NOT EXISTS (
                        SELECT 1 FROM alert_message_templates
                        WHERE school_id IS NULL AND event_key = CAST(:ev2 AS varchar)
                          AND locale = CAST(:loc3 AS varchar)
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "ev": event,
                    "ev2": event,
                    "loc": code,
                    "loc2": code,
                    "loc3": code,
                    "name": f"sybeez_{event}_{code}",
                    "vars": json.dumps(["title", "preview", "school_name"]),
                },
            )


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import parent_messages, reading

    del parent_messages, reading
    Base.metadata.create_all(
        op.get_bind(),
        tables=[Base.metadata.tables[name] for name in TABLES],
        checkfirst=True,
    )
    _seed_passages()
    _seed_templates()

    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sybeez_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        for old in ("school", "role", "tenant", "access", "select", "insert", "update", "delete", "bypass"):
            op.execute(f"DROP POLICY IF EXISTS {table}_{old} ON {table}")

    # Parent preferences: own row only (staff read for audience counts). Consent is the parent's alone.
    t = "parent_message_preferences"
    _policy(t, f"{t}_select", "SELECT", f"{BYPASS} OR ({SCHOOL} AND (user_id = {UID} OR {STAFF_OR_TEACHER}))", None)
    own = f"{BYPASS} OR ({SCHOOL} AND user_id = {UID} AND {ROLE} = 'PARENT')"
    _policy(t, f"{t}_insert", "INSERT", None, own)
    _policy(t, f"{t}_update", "UPDATE", own, own)
    _policy(t, f"{t}_delete", "DELETE", BYPASS, None)

    # Translations.
    t = "message_translations"
    _policy(
        t,
        f"{t}_select",
        "SELECT",
        f"{BYPASS} OR ({SCHOOL} AND {ROLE} <> 'STUDENT' AND {TRANSLATION_SCOPE} "
        f"AND ({STAFF_OR_TEACHER} OR is_published))",
        None,
    )
    _policy(
        t,
        f"{t}_insert",
        "INSERT",
        None,
        f"{BYPASS} OR ({SCHOOL} AND {TRANSLATION_SCOPE} AND ({STAFF_OR_TEACHER} OR "
        f"({ROLE} = 'PARENT' AND is_published AND status = 'AUTO' AND edited_by IS NULL)))",
    )
    staff_write = f"{BYPASS} OR ({SCHOOL} AND {STAFF_OR_TEACHER} AND {TRANSLATION_SCOPE})"
    _policy(t, f"{t}_update", "UPDATE", staff_write, staff_write)
    _policy(t, f"{t}_delete", "DELETE", BYPASS, None)

    # Progress updates: parents of that student only; students never.
    t = "student_progress_updates"
    _policy(
        t,
        f"{t}_select",
        "SELECT",
        f"{BYPASS} OR ({SCHOOL} AND {ROLE} <> 'STUDENT' AND {STUDENT_OK} AND ({STAFF_OR_TEACHER} OR is_published))",
        None,
    )
    write = f"{BYPASS} OR ({SCHOOL} AND {STAFF_OR_TEACHER} AND {STUDENT_OK})"
    _policy(t, f"{t}_insert", "INSERT", None, write)
    _policy(t, f"{t}_update", "UPDATE", write, write)
    _policy(t, f"{t}_delete", "DELETE", write, None)

    # Passages: library rows (school_id NULL) are read-only; school rows belong to the school.
    t = "reading_passages"
    _policy(
        t,
        f"{t}_select",
        "SELECT",
        f"{BYPASS} OR (is_active AND (school_id IS NULL OR ({SCHOOL})) "
        f"AND NULLIF(current_setting('app.school_id', true), '') IS NOT NULL) "
        f"OR ({SCHOOL} AND {STAFF_OR_TEACHER})",
        None,
    )
    pw = f"{BYPASS} OR ({SCHOOL} AND school_id IS NOT NULL AND {STAFF_OR_TEACHER})"
    _policy(t, f"{t}_insert", "INSERT", None, pw)
    _policy(t, f"{t}_update", "UPDATE", pw, pw)
    _policy(t, f"{t}_delete", "DELETE", pw, None)

    # Sessions and progress: the student writes their own; teachers/admins/parents read.
    for t in ("reading_sessions", "reading_progress"):
        _policy(t, f"{t}_select", "SELECT", f"{BYPASS} OR ({SCHOOL} AND {STUDENT_OK})", None)
        sw = f"{BYPASS} OR ({SCHOOL} AND {ROLE} = 'STUDENT' AND {STUDENT_OK})"
        _policy(t, f"{t}_insert", "INSERT", None, sw)
        _policy(t, f"{t}_update", "UPDATE", sw, sw)
        _policy(t, f"{t}_delete", "DELETE", BYPASS, None)


    _audit_function()


def _audit_function() -> None:
    """Parents and students cannot write audit_logs directly (its policy is for admins/auditors), but
    their consent and reading actions must be audited. This definer function records an audit row for
    the caller's own school and user, taken from the session settings, never from arguments."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.log_audit(
          p_action text, p_resource_type text, p_resource_id text, p_message text, p_details jsonb
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
          sid uuid := NULLIF(current_setting('app.school_id', true), '')::uuid;
          uid uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
          rid uuid;
        BEGIN
          IF sid IS NULL OR uid IS NULL THEN
            RAISE EXCEPTION 'log_audit requires a school and user context';
          END IF;
          INSERT INTO audit_logs (id, school_id, actor_id, action, resource_type, resource_id, details, message,
                                  created_at, updated_at)
          VALUES (gen_random_uuid(), sid, uid, left(p_action, 128), left(p_resource_type, 64),
                  left(p_resource_id, 64), p_details, p_message, now(), now())
          RETURNING id INTO rid;
          RETURN rid;
        END;
        $$
        """
    )
    fn = "app.log_audit(text, text, text, text, jsonb)"
    op.execute(f"ALTER FUNCTION {fn} OWNER TO sybeez_definer")
    op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO sybeez_app")
    op.execute("GRANT SELECT, INSERT ON audit_logs TO sybeez_definer")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app.log_audit(text, text, text, text, jsonb)")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DELETE FROM alert_message_templates WHERE school_id IS NULL AND event_key = 'progress_update'")
