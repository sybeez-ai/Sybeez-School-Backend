"""Daily section lessons, private board pictures, and AI memory.

Revision ID: 0083
Revises: 0082

Students and parents only see their own section. Other sections see nothing.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0083"
down_revision: Union[str, None] = "0082"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "daily_lessons",
    "daily_lesson_files",
    "ai_board_pictures",
    "ai_student_facts",
    "ai_class_facts",
    "ai_session_memory",
)

SCHOOL = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
ROLE = "NULLIF(current_setting('app.role', true), '')"
STAFF = f"{ROLE} IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE')"
SECTION = "app.can_see('class_content', NULL::uuid, class_id, NULL::uuid)"
OWN_STUDENT = "app.can_see('student_data', student_id, NULL::uuid, NULL::uuid)"


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
    from src.db.models import daily_lesson as _models

    del _models
    Base.metadata.create_all(
        op.get_bind(), tables=[Base.metadata.tables[name] for name in TABLES], checkfirst=True
    )
    section_read = f"{BYPASS} OR ({SCHOOL} AND ({STAFF} OR {SECTION}))"
    section_write = f"{BYPASS} OR ({SCHOOL} AND ({STAFF} OR ({ROLE} = 'TEACHER' AND {SECTION})))"
    file_class = "(SELECT d.class_id FROM daily_lessons d WHERE d.id = lesson_id)"
    file_section = f"app.can_see('class_content', NULL::uuid, {file_class}, NULL::uuid)"
    file_read = f"{BYPASS} OR ({SCHOOL} AND ({STAFF} OR {file_section}))"
    file_write = f"{BYPASS} OR ({SCHOOL} AND ({STAFF} OR ({ROLE} = 'TEACHER' AND {file_section})))"
    picture_read = f"{BYPASS} OR ({SCHOOL} AND ({STAFF} OR {SECTION} OR {OWN_STUDENT}))"
    service_write = f"{BYPASS} OR ({SCHOOL} AND {STAFF})"
    student_fact_read = (
        f"{BYPASS} OR ({SCHOOL} AND ({STAFF} OR {OWN_STUDENT} OR EXISTS ("
        "SELECT 1 FROM enrollments e WHERE e.student_id = ai_student_facts.student_id "
        "AND e.school_id = ai_student_facts.school_id AND e.status = 'ACTIVE' "
        "AND app.can_see('class_content', NULL::uuid, e.class_id, NULL::uuid))))"
    )
    _policy("daily_lessons", section_read, section_write)
    _policy("daily_lesson_files", file_read, file_write)
    _policy("ai_board_pictures", picture_read, service_write)
    _policy("ai_student_facts", student_fact_read, service_write)
    _policy("ai_class_facts", section_read, service_write)
    _policy("ai_session_memory", picture_read, service_write)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
