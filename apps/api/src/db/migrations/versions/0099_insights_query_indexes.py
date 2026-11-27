"""Indexes for school-admin insights queries.

Revision ID: 0099
Revises: 0098

Speeds up attendance, marks, fee and report-history aggregates used by
the Progress, Heatmap, Analytics and Reports pages. No new tables —
generated report history reuses report_audit_logs.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0099"
down_revision: Union[str, None] = "0098"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_attendance_school_class_on",
        "attendance",
        ["school_id", "class_id", "attended_on"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_student_marks_school_exam",
        "student_marks",
        ["school_id", "exam_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fee_invoices_school_invoice_date",
        "fee_invoices",
        ["school_id", "invoice_date"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_report_audit_logs_school_created",
        "report_audit_logs",
        ["school_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_online_test_attempts_school_submitted",
        "online_test_attempts",
        ["school_id", "submitted_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_online_test_attempts_school_submitted", table_name="online_test_attempts", if_exists=True)
    op.drop_index("ix_report_audit_logs_school_created", table_name="report_audit_logs", if_exists=True)
    op.drop_index("ix_fee_invoices_school_invoice_date", table_name="fee_invoices", if_exists=True)
    op.drop_index("ix_student_marks_school_exam", table_name="student_marks", if_exists=True)
    op.drop_index("ix_attendance_school_class_on", table_name="attendance", if_exists=True)
