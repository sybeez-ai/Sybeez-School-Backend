"""Per-school HR business settings, read by letters, files, leave, onboarding and HR notifications."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError
from src.db.models import HrSettings, School
from src.modules.employees.lookup import user_names
from src.modules.hr_settings.schemas import HrSettingsOut, HrSettingsUpdate


@dataclass(frozen=True)
class HrRules:
    letter_reference_prefix: str = "HR"
    required_document_categories: tuple[str, ...] = (
        "ID_PROOF",
        "CONTRACT",
        "QUALIFICATION",
        "BANK",
    )
    leave_max_days: int = 366
    onboarding_target_days: int = 14
    notify_leave: bool = True
    notify_requests: bool = True
    notify_letters: bool = True
    notify_documents: bool = True
    notify_onboarding: bool = True


DEFAULTS = HrRules()
RULE_FIELDS = (
    "letter_reference_prefix",
    "required_document_categories",
    "leave_max_days",
    "onboarding_target_days",
    "notify_leave",
    "notify_requests",
    "notify_letters",
    "notify_documents",
    "notify_onboarding",
)

# Notification category -> settings toggle that controls it.
NOTIFY_TOGGLE = {
    "LEAVE_SUBMITTED": "notify_leave",
    "LEAVE_APPROVED": "notify_leave",
    "LEAVE_REJECTED": "notify_leave",
    "HR_REQUEST": "notify_requests",
    "HR_LETTER": "notify_letters",
    "HR_DOCUMENT": "notify_documents",
    "HR_ONBOARDING": "notify_onboarding",
}


async def _row(db: AsyncSession, school_id: UUID) -> HrSettings | None:
    return (
        await db.execute(
            select(HrSettings)
            .where(HrSettings.school_id == school_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def load_rules(db: AsyncSession, school_id: UUID) -> HrRules:
    """Saved rules for a school, or the defaults when none have been saved yet."""
    row = await _row(db, school_id)
    if row is None:
        return DEFAULTS
    values = {f: getattr(row, f) for f in RULE_FIELDS}
    values["required_document_categories"] = tuple(
        row.required_document_categories or ()
    )
    return HrRules(**values)


async def notifications_enabled(
    db: AsyncSession, school_id: UUID, category: str
) -> bool:
    toggle = NOTIFY_TOGGLE.get(category)
    return (
        True
        if toggle is None
        else bool(getattr(await load_rules(db, school_id), toggle))
    )


class HrSettingsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, school_id: UUID) -> HrSettingsOut:
        row = await _row(self.db, school_id)
        rules = await load_rules(self.db, school_id)
        tz = (
            await self.db.execute(select(School.timezone).where(School.id == school_id))
        ).scalar_one_or_none()
        names = await user_names(self.db, {row.updated_by} if row else set())
        return HrSettingsOut(
            **{
                f: getattr(rules, f)
                for f in RULE_FIELDS
                if f != "required_document_categories"
            },
            required_document_categories=list(rules.required_document_categories),
            school_timezone=tz or "UTC",
            is_default=row is None,
            updated_at=row.updated_at if row else None,
            updated_by_name=names.get(row.updated_by)
            if row and row.updated_by
            else None,
        )

    async def update(
        self, school_id: UUID, user_id: UUID, data: HrSettingsUpdate
    ) -> HrSettingsOut:
        payload = data.model_dump(exclude_unset=True)
        empty = [k for k, v in payload.items() if v is None]
        if empty:
            raise AppError(
                f"{empty[0].replace('_', ' ').capitalize()} cannot be empty",
                status_code=422,
            )
        row = await _row(self.db, school_id)
        if row is None:
            row = HrSettings(
                school_id=school_id,
                required_document_categories=list(
                    DEFAULTS.required_document_categories
                ),
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(row)
                    await self.db.flush()
            except IntegrityError:
                row = await _row(self.db, school_id)
                if row is None:
                    raise
        for k, v in payload.items():
            setattr(row, k, v)
        row.updated_by = user_id
        await self.db.flush()
        return await self.get(school_id)
