from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator


class GenerateReportRequest(BaseModel):
    student_id: UUID
    period_start: date | None = None  # defaults to 7 days ago
    period_end: date | None = None  # defaults to today


class AiReportOut(BaseModel):
    id: UUID
    school_id: UUID
    student_id: UUID
    parent_user_id: UUID
    period_start: date
    period_end: date
    title: str
    summary_json: dict[str, Any]
    body_markdown: str
    status: str
    created_at: datetime

    @field_validator("summary_json", mode="before")
    @classmethod
    def parse_summary(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        return v or {}
