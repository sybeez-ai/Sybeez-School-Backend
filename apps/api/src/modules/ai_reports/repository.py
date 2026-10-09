from __future__ import annotations

import json
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.ai_reports.schemas import AiReportOut


class AiReportsRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        *,
        school_id: UUID,
        student_id: UUID,
        parent_user_id: UUID,
        period_start: date,
        period_end: date,
        title: str,
        summary_json: dict,
        body_markdown: str,
        status: str = "READY",
    ) -> AiReportOut:
        row = (
            await self.db.execute(
                text(
                    """
                    INSERT INTO parent_ai_reports
                        (school_id, student_id, parent_user_id, period_start, period_end,
                         title, summary_json, body_markdown, status)
                    VALUES
                        (:school_id, :student_id, :parent_user_id, :period_start, :period_end,
                         :title, :summary_json, :body_markdown, :status)
                    RETURNING id, school_id, student_id, parent_user_id, period_start, period_end,
                              title, summary_json, body_markdown, status, created_at
                    """
                ),
                {
                    "school_id": str(school_id),
                    "student_id": str(student_id),
                    "parent_user_id": str(parent_user_id),
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "title": title,
                    "summary_json": json.dumps(summary_json),
                    "body_markdown": body_markdown,
                    "status": status,
                },
            )
        ).mappings().one()
        await self.db.commit()
        return _row_to_out(row)

    async def list_for_parent(
        self, *, school_id: UUID, parent_user_id: UUID, limit: int = 50
    ) -> list[AiReportOut]:
        rows = (
            await self.db.execute(
                text(
                    """
                    SELECT id, school_id, student_id, parent_user_id, period_start, period_end,
                           title, summary_json, body_markdown, status, created_at
                    FROM parent_ai_reports
                    WHERE school_id = :school_id AND parent_user_id = :parent_user_id
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """
                ),
                {
                    "school_id": str(school_id),
                    "parent_user_id": str(parent_user_id),
                    "limit": limit,
                },
            )
        ).mappings().all()
        return [_row_to_out(r) for r in rows]

    async def get(
        self, *, school_id: UUID, report_id: UUID, parent_user_id: UUID
    ) -> AiReportOut | None:
        row = (
            await self.db.execute(
                text(
                    """
                    SELECT id, school_id, student_id, parent_user_id, period_start, period_end,
                           title, summary_json, body_markdown, status, created_at
                    FROM parent_ai_reports
                    WHERE id = :report_id
                      AND school_id = :school_id
                      AND parent_user_id = :parent_user_id
                    """
                ),
                {
                    "report_id": str(report_id),
                    "school_id": str(school_id),
                    "parent_user_id": str(parent_user_id),
                },
            )
        ).mappings().one_or_none()
        return _row_to_out(row) if row else None


def _row_to_out(row: dict) -> AiReportOut:
    return AiReportOut(
        id=row["id"],
        school_id=row["school_id"],
        student_id=row["student_id"],
        parent_user_id=row["parent_user_id"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        title=row["title"],
        summary_json=row["summary_json"],
        body_markdown=row["body_markdown"],
        status=row["status"],
        created_at=row["created_at"],
    )
