"""Phase 8 exams, marks, grading, and results.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-27
"""

from typing import Sequence, Union

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    # ── exam types ──
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS exam_types (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            name VARCHAR(128) NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_exam_types_school_id ON exam_types (school_id)")

    # ── grading ──
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS grading_scales (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            name VARCHAR(128) NOT NULL,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            must_pass_all_subjects BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_grading_scales_school_id ON grading_scales (school_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS grade_rules (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            grading_scale_id UUID NOT NULL REFERENCES grading_scales(id) ON DELETE CASCADE,
            grade VARCHAR(16) NOT NULL,
            min_percentage DOUBLE PRECISION NOT NULL,
            max_percentage DOUBLE PRECISION NOT NULL,
            description VARCHAR(255),
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_grade_rules_school_id ON grade_rules (school_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_grade_rules_grading_scale_id ON grade_rules (grading_scale_id)"
    )

    # ── alter exams ──
    op.execute("ALTER TABLE exams ALTER COLUMN class_id DROP NOT NULL")
    op.execute("ALTER TABLE exams ALTER COLUMN subject_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE exams ADD COLUMN IF NOT EXISTS exam_type_id UUID REFERENCES exam_types(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE exams ADD COLUMN IF NOT EXISTS grade_id UUID REFERENCES grades(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE exams ADD COLUMN IF NOT EXISTS grading_scale_id UUID REFERENCES grading_scales(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE exams ADD COLUMN IF NOT EXISTS starts_on DATE")
    op.execute("ALTER TABLE exams ADD COLUMN IF NOT EXISTS ends_on DATE")
    op.execute(
        "ALTER TABLE exams ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'DRAFT'"
    )
    op.execute(
        "ALTER TABLE exams ADD COLUMN IF NOT EXISTS must_pass_all_subjects BOOLEAN NOT NULL DEFAULT TRUE"
    )
    op.execute("ALTER TABLE exams ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ")
    op.execute("ALTER TABLE exams ADD COLUMN IF NOT EXISTS locked BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("CREATE INDEX IF NOT EXISTS ix_exams_exam_type_id ON exams (exam_type_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_exams_grade_id ON exams (grade_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_exams_status ON exams (status)")

    # ── exam sections / subjects / schedules ──
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS exam_sections (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
            class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_exam_section UNIQUE (exam_id, class_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_exam_sections_school_id ON exam_sections (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_exam_sections_exam_id ON exam_sections (exam_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_exam_sections_class_id ON exam_sections (class_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS exam_subjects (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
            subject_id UUID NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            grade_id UUID REFERENCES grades(id) ON DELETE SET NULL,
            max_marks DOUBLE PRECISION NOT NULL DEFAULT 100,
            pass_marks DOUBLE PRECISION NOT NULL DEFAULT 35,
            duration_minutes INTEGER,
            exam_date DATE,
            starts_at TIME,
            ends_at TIME,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_exam_subject UNIQUE (exam_id, subject_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_exam_subjects_school_id ON exam_subjects (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_exam_subjects_exam_id ON exam_subjects (exam_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_exam_subjects_subject_id ON exam_subjects (subject_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS exam_schedules (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
            exam_subject_id UUID NOT NULL REFERENCES exam_subjects(id) ON DELETE CASCADE,
            class_id UUID REFERENCES classes(id) ON DELETE CASCADE,
            exam_date DATE NOT NULL,
            starts_at TIME,
            ends_at TIME,
            room VARCHAR(64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_exam_schedules_school_id ON exam_schedules (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_exam_schedules_exam_id ON exam_schedules (exam_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_exam_schedules_exam_subject_id ON exam_schedules (exam_subject_id)"
    )

    # ── marks ──
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS exam_marks_entries (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
            exam_subject_id UUID NOT NULL REFERENCES exam_subjects(id) ON DELETE CASCADE,
            class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
            status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
            submitted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_marks_entry_subject_class UNIQUE (exam_subject_id, class_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_exam_marks_entries_school_id ON exam_marks_entries (school_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_exam_marks_entries_exam_id ON exam_marks_entries (exam_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_exam_marks_entries_teacher_id ON exam_marks_entries (teacher_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS student_marks (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
            exam_subject_id UUID NOT NULL REFERENCES exam_subjects(id) ON DELETE CASCADE,
            student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            obtained_marks DOUBLE PRECISION,
            max_marks DOUBLE PRECISION NOT NULL DEFAULT 100,
            attendance VARCHAR(16) NOT NULL DEFAULT 'PRESENT',
            grade VARCHAR(16),
            remarks TEXT,
            status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
            entered_by UUID REFERENCES users(id) ON DELETE SET NULL,
            entered_at TIMESTAMPTZ,
            updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_student_mark_subject UNIQUE (exam_subject_id, student_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_student_marks_school_id ON student_marks (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_student_marks_exam_id ON student_marks (exam_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_student_marks_exam_subject_id ON student_marks (exam_subject_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_student_marks_student_id ON student_marks (student_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_student_marks_status ON student_marks (status)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS result_audit_logs (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            exam_id UUID NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
            student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            exam_subject_id UUID REFERENCES exam_subjects(id) ON DELETE SET NULL,
            old_marks DOUBLE PRECISION,
            new_marks DOUBLE PRECISION,
            changed_by UUID REFERENCES users(id) ON DELETE SET NULL,
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_result_audit_logs_school_id ON result_audit_logs (school_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_result_audit_logs_exam_id ON result_audit_logs (exam_id)"
    )

    # ── alter results ──
    op.execute(
        "ALTER TABLE results ADD COLUMN IF NOT EXISTS class_id UUID REFERENCES classes(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE results ADD COLUMN IF NOT EXISTS max_score DOUBLE PRECISION NOT NULL DEFAULT 0"
    )
    op.execute("ALTER TABLE results ADD COLUMN IF NOT EXISTS pass_fail VARCHAR(16)")
    op.execute(
        "ALTER TABLE results ADD COLUMN IF NOT EXISTS is_published BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute("ALTER TABLE results ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ")
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_result_exam_student'
            ) THEN
                ALTER TABLE results ADD CONSTRAINT uq_result_exam_student UNIQUE (exam_id, student_id);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS result_audit_logs CASCADE")
    op.execute("DROP TABLE IF EXISTS student_marks CASCADE")
    op.execute("DROP TABLE IF EXISTS exam_marks_entries CASCADE")
    op.execute("DROP TABLE IF EXISTS exam_schedules CASCADE")
    op.execute("DROP TABLE IF EXISTS exam_subjects CASCADE")
    op.execute("DROP TABLE IF EXISTS exam_sections CASCADE")
    op.execute("DROP TABLE IF EXISTS grade_rules CASCADE")
    op.execute("DROP TABLE IF EXISTS grading_scales CASCADE")
    op.execute("DROP TABLE IF EXISTS exam_types CASCADE")
