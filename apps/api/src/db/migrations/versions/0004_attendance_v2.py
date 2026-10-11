"""Attendance v2: late details, audit trail, settings, leave attachments.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.db.migration_guards import create_index_if_missing, table_exists

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS period VARCHAR(32)")
    op.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS arrival_time TIME")
    op.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS reason TEXT")
    op.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS leave_request_id UUID")
    op.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS marked_at TIMESTAMPTZ")
    op.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS last_modified_by UUID")
    op.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS last_modified_at TIMESTAMPTZ")
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.conrelid = 'attendance'::regclass AND c.contype = 'f' AND a.attname = 'leave_request_id'
          ) THEN
            ALTER TABLE attendance
              ADD CONSTRAINT fk_attendance_leave_request
              FOREIGN KEY (leave_request_id) REFERENCES leave_requests(id) ON DELETE SET NULL;
          END IF;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_attendance_attended_on ON attendance (attended_on)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_attendance_leave_request_id ON attendance (leave_request_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_attendance_student_id ON attendance (student_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_attendance_class_id ON attendance (class_id)")

    op.execute("ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS attachment_name VARCHAR(255)")
    op.execute("ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS attachment_url TEXT")
    op.execute(
        "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS source VARCHAR(32) NOT NULL DEFAULT 'MANUAL'"
    )

    if not table_exists("attendance_audit_logs"):
        op.create_table(
            "attendance_audit_logs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("school_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
            sa.Column("attendance_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("attendance.id", ondelete="CASCADE"), nullable=False),
            sa.Column("student_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("attended_on", sa.Date(), nullable=False),
            sa.Column("from_status", sa.String(32), nullable=True),
            sa.Column("to_status", sa.String(32), nullable=False),
            sa.Column("changed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("leave_request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leave_requests.id", ondelete="SET NULL"), nullable=True),
        )
    create_index_if_missing("ix_attendance_audit_logs_school_id", "attendance_audit_logs", ["school_id"])
    create_index_if_missing("ix_attendance_audit_logs_attendance_id", "attendance_audit_logs", ["attendance_id"])
    create_index_if_missing("ix_attendance_audit_logs_student_id", "attendance_audit_logs", ["student_id"])

    if not table_exists("attendance_settings"):
        op.create_table(
            "attendance_settings",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("school_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
            sa.Column("school_start_time", sa.Time(), nullable=False, server_default="09:00:00"),
            sa.Column("absence_alert_days", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("absence_alert_window_days", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("late_alert_count", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("low_attendance_percent", sa.Integer(), nullable=False, server_default="85"),
            sa.Column("teacher_can_approve_leave", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("notify_parents_on_absent", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.UniqueConstraint("school_id", name="uq_attendance_settings_school"),
        )
    create_index_if_missing("ix_attendance_settings_school_id", "attendance_settings", ["school_id"])

    # Optional link metadata on notifications for deep-links
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS category VARCHAR(64)")
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS link_path TEXT")
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS meta_json TEXT")


def downgrade() -> None:
    op.drop_index("ix_attendance_settings_school_id", table_name="attendance_settings")
    op.drop_table("attendance_settings")
    op.drop_index("ix_attendance_audit_logs_student_id", table_name="attendance_audit_logs")
    op.drop_index("ix_attendance_audit_logs_attendance_id", table_name="attendance_audit_logs")
    op.drop_index("ix_attendance_audit_logs_school_id", table_name="attendance_audit_logs")
    op.drop_table("attendance_audit_logs")
