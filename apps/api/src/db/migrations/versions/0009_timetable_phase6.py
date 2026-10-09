"""Phase 6 timetable periods, settings, rooms.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-27
"""

from typing import Sequence, Union

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS timetable_settings (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            working_days VARCHAR(64) NOT NULL DEFAULT '0,1,2,3,4',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_timetable_settings_school UNIQUE (school_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_timetable_settings_school_id ON timetable_settings (school_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS timetable_periods (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            name VARCHAR(64) NOT NULL,
            kind VARCHAR(16) NOT NULL DEFAULT 'LESSON',
            sort_order INTEGER NOT NULL DEFAULT 0,
            starts_at TIME NOT NULL,
            ends_at TIME NOT NULL,
            is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_timetable_periods_school_id ON timetable_periods (school_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rooms (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            name VARCHAR(128) NOT NULL,
            code VARCHAR(64),
            capacity INTEGER,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_rooms_school_id ON rooms (school_id)")

    op.execute(
        "ALTER TABLE timetable_entries ADD COLUMN IF NOT EXISTS period_id UUID REFERENCES timetable_periods(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE timetable_entries ADD COLUMN IF NOT EXISTS room_id UUID REFERENCES rooms(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_timetable_entries_period_id ON timetable_entries (period_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_timetable_entries_room_id ON timetable_entries (room_id)"
    )
    op.execute(
        """
        DO $$ BEGIN
          CREATE UNIQUE INDEX uq_timetable_section_day_period
            ON timetable_entries (school_id, class_id, day_of_week, period_id)
            WHERE period_id IS NOT NULL;
        EXCEPTION WHEN duplicate_table THEN NULL;
                   WHEN duplicate_object THEN NULL;
        END $$;
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS uq_timetable_section_day_period")
    op.execute("DROP INDEX IF EXISTS ix_timetable_entries_room_id")
    op.execute("DROP INDEX IF EXISTS ix_timetable_entries_period_id")
    op.execute("ALTER TABLE timetable_entries DROP COLUMN IF EXISTS room_id")
    op.execute("ALTER TABLE timetable_entries DROP COLUMN IF EXISTS period_id")
    op.execute("DROP TABLE IF EXISTS rooms")
    op.execute("DROP TABLE IF EXISTS timetable_periods")
    op.execute("DROP TABLE IF EXISTS timetable_settings")
