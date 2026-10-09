"""Phase 18: My Life — diary user_id + goals/habits tables.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-05
"""

from typing import Sequence, Union

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    # Diary: owner is the authenticated user (student or teacher)
    op.execute(
        """
        ALTER TABLE diary_entries
            ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE
        """
    )
    op.execute(
        """
        UPDATE diary_entries de
        SET user_id = s.user_id
        FROM students s
        WHERE de.student_id = s.id AND de.user_id IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE diary_entries
            ALTER COLUMN student_id DROP NOT NULL
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_diary_entries_user_id ON diary_entries (user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS personal_goals (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            category VARCHAR(32) NOT NULL DEFAULT 'personal',
            target_date DATE,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            progress_percent INTEGER NOT NULL DEFAULT 0,
            completed_on DATE,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personal_goals_user_id ON personal_goals (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personal_goals_school_id ON personal_goals (school_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS personal_goal_milestones (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            goal_id UUID NOT NULL REFERENCES personal_goals(id) ON DELETE CASCADE,
            label VARCHAR(255) NOT NULL,
            completed BOOLEAN NOT NULL DEFAULT FALSE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personal_goal_milestones_goal_id ON personal_goal_milestones (goal_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS personal_habits (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            description TEXT,
            frequency VARCHAR(32) NOT NULL DEFAULT 'daily',
            weekly_target INTEGER NOT NULL DEFAULT 7,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personal_habits_user_id ON personal_habits (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personal_habits_school_id ON personal_habits (school_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS personal_habit_checkins (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            habit_id UUID NOT NULL REFERENCES personal_habits(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            checkin_date DATE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (habit_id, checkin_date)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personal_habit_checkins_user_id ON personal_habit_checkins (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personal_habit_checkins_date ON personal_habit_checkins (checkin_date)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS personal_habit_checkins")
    op.execute("DROP TABLE IF EXISTS personal_habits")
    op.execute("DROP TABLE IF EXISTS personal_goal_milestones")
    op.execute("DROP TABLE IF EXISTS personal_goals")
    op.execute("DROP INDEX IF EXISTS ix_diary_entries_user_id")
    op.execute("ALTER TABLE diary_entries DROP COLUMN IF EXISTS user_id")
