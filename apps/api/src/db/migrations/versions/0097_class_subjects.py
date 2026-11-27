"""Class subjects with an optional teacher.

Revision ID: 0097
Revises: 0096

A class can list the subjects it studies without first assigning a teacher.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0097"
down_revision: Union[str, None] = "0096"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "class_subjects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("classes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("teacher_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teachers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("class_id", "subject_id", name="uq_class_subjects_class_subject"),
    )
    op.create_index("ix_class_subjects_school_id", "class_subjects", ["school_id"])
    op.create_index("ix_class_subjects_class_id", "class_subjects", ["class_id"])
    op.create_index("ix_class_subjects_subject_id", "class_subjects", ["subject_id"])
    op.create_index("ix_class_subjects_teacher_id", "class_subjects", ["teacher_id"])


def downgrade() -> None:
    op.drop_index("ix_class_subjects_teacher_id", table_name="class_subjects")
    op.drop_index("ix_class_subjects_subject_id", table_name="class_subjects")
    op.drop_index("ix_class_subjects_class_id", table_name="class_subjects")
    op.drop_index("ix_class_subjects_school_id", table_name="class_subjects")
    op.drop_table("class_subjects")
