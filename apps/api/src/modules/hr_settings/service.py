"""Per-school HR business settings, read by letters, files, leave, attendance, onboarding and HR notifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError
from src.db.models import HrSettings, School
from src.modules.employees.lookup import user_names
from src.modules.hr.audit import hr_audit
from src.modules.hr_settings.schemas import HrSettingsOut, HrSettingsUpdate

DEFAULT_CHECKLISTS = {
    "ONBOARDING": (
        "Collect joining documents",
        "Create email and portal access",
        "Assign department and reporting manager",
        "Schedule orientation",
        "Issue ID card",
    ),
    "OFFBOARDING": (
        "Conduct exit interview",
        "Collect school assets",
        "Revoke system access",
        "Final salary clearance",
        "Issue relieving letter",
    ),
}


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
    employee_code_prefix: str | None = None
    working_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4, 5)
    work_start_time: time | None = None
    late_grace_minutes: int = 10
    leave_balance_enforced: bool = True
    leave_min_notice_days: int = 0
    document_expiry_warning_days: int = 30
    onboarding_checklist: tuple[str, ...] = DEFAULT_CHECKLISTS["ONBOARDING"]
    offboarding_checklist: tuple[str, ...] = DEFAULT_CHECKLISTS["OFFBOARDING"]

    def checklist(self, workflow_type: str) -> tuple[str, ...]:
        return self.onboarding_checklist if workflow_type == "ONBOARDING" else self.offboarding_checklist


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
    "employee_code_prefix",
    "working_weekdays",
    "work_start_time",
    "late_grace_minutes",
    "leave_balance_enforced",
    "leave_min_notice_days",
    "document_expiry_warning_days",
    "onboarding_checklist",
    "offboarding_checklist",
)
_TUPLE_FIELDS = ("required_document_categories", "working_weekdays", "onboarding_checklist", "offboarding_checklist")
# Fields an admin may clear by sending null.
_NULLABLE_FIELDS = ("employee_code_prefix", "work_start_time")

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
    for f in _TUPLE_FIELDS:
        raw = values[f]
        values[f] = getattr(DEFAULTS, f) if raw is None else tuple(raw)
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
        values = {f: getattr(rules, f) for f in RULE_FIELDS}
        for f in _TUPLE_FIELDS:
            values[f] = list(values[f])
        return HrSettingsOut(
            **values,
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
        empty = [k for k, v in payload.items() if v is None and k not in _NULLABLE_FIELDS]
        if empty:
            raise AppError(
                f"{empty[0].replace('_', ' ').capitalize()} cannot be empty",
                status_code=422,
            )
        before = await load_rules(self.db, school_id)
        row = await _row(self.db, school_id)
        if row is None:
            row = HrSettings(
                school_id=school_id,
                required_document_categories=list(
                    DEFAULTS.required_document_categories
                ),
                working_weekdays=list(DEFAULTS.working_weekdays),
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
        changes = {}
        for k, v in payload.items():
            old = getattr(before, k)
            old = list(old) if isinstance(old, tuple) else old
            if old != v:
                changes[k] = {"from": old, "to": v}
        if changes:
            await hr_audit(
                self.db,
                school_id=school_id,
                actor_id=user_id,
                action="hr.settings.updated",
                resource_type="hr_settings",
                resource_id=row.id,
                message="HR settings updated",
                details={"changes": changes},
            )
        return await self.get(school_id)
