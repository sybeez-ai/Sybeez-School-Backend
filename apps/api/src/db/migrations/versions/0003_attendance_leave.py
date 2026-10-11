"""Attendance table ensure + leave_requests.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.db.migration_guards import create_index_if_missing, table_exists

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensure attendance exists for DBs created before the model was added
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance (
            id UUID PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            attended_on DATE NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'PRESENT',
            marked_by UUID REFERENCES users(id),
            CONSTRAINT uq_attendance_day UNIQUE (student_id, class_id, attended_on)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_attendance_school_id ON attendance (school_id)")

    if not table_exists("leave_requests"):
        op.create_table(
            "leave_requests",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column(
                "school_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("schools.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "student_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("students.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "requested_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
            sa.Column(
                "decided_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("decision_note", sa.Text(), nullable=True),
        )
    create_index_if_missing("ix_leave_requests_school_id", "leave_requests", ["school_id"])
    create_index_if_missing("ix_leave_requests_student_id", "leave_requests", ["student_id"])
    create_index_if_missing("ix_leave_requests_requested_by", "leave_requests", ["requested_by"])
    create_index_if_missing("ix_leave_requests_status", "leave_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_leave_requests_status", table_name="leave_requests")
    op.drop_index("ix_leave_requests_requested_by", table_name="leave_requests")
    op.drop_index("ix_leave_requests_student_id", table_name="leave_requests")
    op.drop_index("ix_leave_requests_school_id", table_name="leave_requests")
    op.drop_table("leave_requests")
