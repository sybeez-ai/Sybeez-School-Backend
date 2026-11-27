"""School-admin tests, notices and message broadcasts.

Revision ID: 0098
Revises: 0097

Adds a subject question bank, approval fields on exams and online tests,
notice targeting/channel/schedule/attachment columns, and message broadcasts.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0098"
down_revision: Union[str, None] = "0097"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "question_bank_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("classes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("teacher_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teachers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("question_type", sa.String(32), nullable=False, server_default="mcq"),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("answer_key", sa.Text(), nullable=True),
        sa.Column("points", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_question_bank_items_school_id", "question_bank_items", ["school_id"])
    op.create_index("ix_question_bank_items_subject_id", "question_bank_items", ["subject_id"])
    op.create_index("ix_question_bank_items_class_id", "question_bank_items", ["class_id"])
    op.create_index("ix_question_bank_items_created_by", "question_bank_items", ["created_by"])

    op.create_table(
        "message_broadcasts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("audience_type", sa.String(32), nullable=False),
        sa.Column("audience_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("channels", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_message_broadcasts_school_id", "message_broadcasts", ["school_id"])
    op.create_index("ix_message_broadcasts_created_at", "message_broadcasts", ["created_at"])

    op.add_column("announcements", sa.Column("channels", postgresql.JSONB(), nullable=False, server_default=sa.text("""'["IN_APP"]'::jsonb""")))
    op.add_column("announcements", sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True))
    op.add_column("announcements", sa.Column("attachments", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))

    op.add_column("announcement_audiences", sa.Column("student_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_announcement_audiences_student_id", "announcement_audiences", ["student_id"])

    for table in ("exams", "online_tests"):
        op.add_column(table, sa.Column("approval_status", sa.String(32), nullable=False, server_default="DRAFT"))
        op.add_column(table, sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("submitted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
        op.add_column(table, sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
        op.add_column(table, sa.Column("rejection_reason", sa.Text(), nullable=True))
        op.create_index(f"ix_{table}_approval_status", table, ["approval_status"])

    # Already-published or closed items were not waiting on approval.
    op.execute(
        """
        UPDATE exams
        SET approval_status = 'APPROVED'
        WHERE status IN ('SCHEDULED', 'ONGOING', 'MARKS_ENTRY', 'UNDER_REVIEW', 'PUBLISHED', 'CLOSED')
        """
    )
    op.execute(
        """
        UPDATE online_tests
        SET approval_status = 'APPROVED'
        WHERE status IN ('PUBLISHED', 'CLOSED')
        """
    )


def downgrade() -> None:
    for table in ("exams", "online_tests"):
        op.drop_index(f"ix_{table}_approval_status", table_name=table)
        op.drop_column(table, "rejection_reason")
        op.drop_column(table, "approved_by")
        op.drop_column(table, "approved_at")
        op.drop_column(table, "submitted_by")
        op.drop_column(table, "submitted_at")
        op.drop_column(table, "approval_status")
    op.drop_index("ix_announcement_audiences_student_id", table_name="announcement_audiences")
    op.drop_column("announcement_audiences", "student_id")
    op.drop_column("announcements", "attachments")
    op.drop_column("announcements", "scheduled_for")
    op.drop_column("announcements", "channels")
    op.drop_index("ix_message_broadcasts_created_at", table_name="message_broadcasts")
    op.drop_index("ix_message_broadcasts_school_id", table_name="message_broadcasts")
    op.drop_table("message_broadcasts")
    op.drop_index("ix_question_bank_items_created_by", table_name="question_bank_items")
    op.drop_index("ix_question_bank_items_class_id", table_name="question_bank_items")
    op.drop_index("ix_question_bank_items_subject_id", table_name="question_bank_items")
    op.drop_index("ix_question_bank_items_school_id", table_name="question_bank_items")
    op.drop_table("question_bank_items")
