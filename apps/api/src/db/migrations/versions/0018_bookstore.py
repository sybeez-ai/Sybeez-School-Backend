"""Book Store — catalogue, wishlists, orders (school-scoped).

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-28
"""

from typing import Sequence, Union

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bookstore_items (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            author VARCHAR(255) NOT NULL DEFAULT '',
            category VARCHAR(32) NOT NULL DEFAULT 'textbooks',
            price_inr NUMERIC(12, 2) NOT NULL DEFAULT 0,
            rating NUMERIC(3, 1) NOT NULL DEFAULT 0,
            featured BOOLEAN NOT NULL DEFAULT FALSE,
            badge VARCHAR(64),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bookstore_items_school_id ON bookstore_items (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bookstore_items_category ON bookstore_items (category)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bookstore_wishlists (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            item_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_bookstore_wishlist_student UNIQUE (school_id, student_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bookstore_wishlists_school_id ON bookstore_wishlists (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bookstore_wishlists_student_id ON bookstore_wishlists (student_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bookstore_orders (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            currency VARCHAR(8) NOT NULL DEFAULT 'INR',
            total_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
            items JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bookstore_orders_school_id ON bookstore_orders (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_bookstore_orders_student_id ON bookstore_orders (student_id)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS bookstore_orders")
    op.execute("DROP TABLE IF EXISTS bookstore_wishlists")
    op.execute("DROP TABLE IF EXISTS bookstore_items")

