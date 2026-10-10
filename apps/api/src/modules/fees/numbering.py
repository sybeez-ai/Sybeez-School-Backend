"""Gap-free receipt/invoice/payment numbers per school and Indian financial year.

Indian FY runs 1 Apr–31 Mar. Allocation is a single UPDATE … RETURNING inside
the caller's transaction, so a rollback does not burn a number and concurrent
transactions cannot observe the same value.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def indian_financial_year(on: date | None = None) -> tuple[int, str]:
    """Return (start_year, label) e.g. (2026, '2026-27') for 1 Apr 2026–31 Mar 2027."""
    on = on or date.today()
    start = on.year if on.month >= 4 else on.year - 1
    label = f"{start}-{str(start + 1)[-2:]}"
    return start, label


def format_series_number(prefix: str, fy_label: str, value: int) -> str:
    return f"{prefix}-{fy_label}-{int(value):06d}"


async def allocate_series_number(
    db: AsyncSession,
    school_id: UUID,
    seq_type: str,
    prefix: str,
    *,
    on: date | None = None,
) -> str:
    start_year, fy_label = indian_financial_year(on)
    await db.execute(
        text(
            """
            INSERT INTO fee_sequences (
                id, school_id, seq_type, year, fy_label, last_value, created_at, updated_at
            )
            VALUES (:id, :school_id, :seq_type, :year, :fy, 0, NOW(), NOW())
            ON CONFLICT ON CONSTRAINT uq_fee_sequence_fy DO NOTHING
            """
        ),
        {
            "id": str(uuid4()),
            "school_id": str(school_id),
            "seq_type": seq_type,
            "year": start_year,
            "fy": fy_label,
        },
    )
    result = await db.execute(
        text(
            """
            UPDATE fee_sequences
            SET last_value = last_value + 1, updated_at = NOW()
            WHERE school_id = :school_id AND seq_type = :seq_type AND fy_label = :fy
            RETURNING last_value
            """
        ),
        {"school_id": str(school_id), "seq_type": seq_type, "fy": fy_label},
    )
    value = result.scalar_one()
    return format_series_number(prefix, fy_label, int(value))
