"""HR letters: drafts, issue/revoke lifecycle, built-in templates and PDF output."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, NotFoundError
from src.db.models import HrLetter, School
from src.modules.employees.lookup import (
    EmployeeBrief,
    employee_briefs,
    employee_for_user,
    get_employee_brief,
    user_names,
)
from src.modules.hr.text_pdf import build_text_pdf
from src.modules.hr_letters.schemas import (
    LetterCreate,
    LetterIssue,
    LetterOut,
    LetterRevoke,
    LetterTemplateOut,
    LetterUpdate,
)
from src.modules.hr_notifications.service import HR_LETTER, HrNotifier
from src.modules.hr_settings.service import load_rules

LETTER_LABELS = {
    "OFFER": "Offer letter",
    "APPOINTMENT": "Appointment letter",
    "EXPERIENCE": "Experience letter",
    "RELIEVING": "Relieving letter",
    "OTHER": "HR letter",
}


def _fmt(d: date | None) -> str:
    return d.strftime("%d %b %Y") if d else ""


def render_template(
    letter_type: str, emp: EmployeeBrief, school_name: str, signatory: str
) -> LetterTemplateOut:
    role = emp.designation_name or emp.title or "staff member"
    dept = f" in the {emp.department_name} department" if emp.department_name else ""
    joined = _fmt(emp.joining_date) or "the agreed date"
    who = (
        f"{emp.full_name} ({emp.employee_code})" if emp.employee_code else emp.full_name
    )
    sign = f"Sincerely,\n\n{signatory}\nFor {school_name}"
    bodies = {
        "OFFER": (
            f"Dear {emp.full_name},\n\n"
            f"We are pleased to offer you the position of {role} at {school_name}{dept}. "
            f"Your expected date of joining is {joined}.\n\n"
            "Please confirm your acceptance by signing and returning a copy of this letter.\n\n"
            f"We look forward to welcoming you.\n\n{sign}"
        ),
        "APPOINTMENT": (
            f"Dear {emp.full_name},\n\n"
            f"This letter confirms your appointment as {role} at {school_name}{dept} "
            f"with effect from {joined}. Your terms of employment are as agreed and as per school policy.\n\n"
            f"{sign}"
        ),
        "EXPERIENCE": (
            "To whom it may concern,\n\n"
            f"This is to certify that {who} has been working at {school_name} as {role}{dept} "
            f"since {joined}. During this period we found them sincere and dedicated.\n\n"
            f"We wish them every success.\n\n{sign}"
        ),
        "RELIEVING": (
            f"Dear {emp.full_name},\n\n"
            f"This is to confirm that {who}, {role}, has been relieved of duties at {school_name}. "
            "Responsibilities have been handed over as per school policy.\n\n"
            f"We thank you for your contribution and wish you well.\n\n{sign}"
        ),
        "OTHER": f"Dear {emp.full_name},\n\n\n\n{sign}",
    }
    return LetterTemplateOut(
        letter_type=letter_type,
        title=f"{LETTER_LABELS[letter_type]} - {emp.full_name}",
        body=bodies[letter_type],
    )


class HrLetterService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── reads ──────────────────────────────────────────────────────────────

    async def list(
        self,
        school_id: UUID,
        *,
        employee_id: UUID | None = None,
        letter_type: str | None = None,
        status: str | None = None,
        q: str | None = None,
        limit: int = 500,
    ) -> list[LetterOut]:
        stmt = select(HrLetter).where(HrLetter.school_id == school_id)
        if employee_id:
            stmt = stmt.where(HrLetter.employee_id == employee_id)
        if letter_type:
            stmt = stmt.where(HrLetter.letter_type == letter_type.upper())
        if status:
            stmt = stmt.where(HrLetter.status == status.upper())
        rows = list(
            (
                await self.db.execute(
                    stmt.order_by(HrLetter.created_at.desc()).limit(limit)
                )
            ).scalars()
        )
        out = await self._many_out(school_id, rows)
        if q:
            needle = q.strip().lower()
            out = [
                o
                for o in out
                if needle in o.title.lower()
                or needle in o.employee_name.lower()
                or needle in (o.employee_code or "").lower()
                or needle in (o.reference_number or "").lower()
            ]
        return out

    async def get(self, school_id: UUID, letter_id: UUID) -> LetterOut:
        return (
            await self._many_out(school_id, [await self._get(school_id, letter_id)])
        )[0]

    async def template(
        self, school_id: UUID, employee_id: UUID, letter_type: str, signatory: str
    ) -> LetterTemplateOut:
        emp = await get_employee_brief(self.db, school_id, employee_id)
        return render_template(
            letter_type, emp, await self._school_name(school_id), signatory
        )

    async def my_letters(self, school_id: UUID, user_id: UUID) -> list[LetterOut]:
        emp = await employee_for_user(self.db, school_id, user_id)
        rows = list(
            (
                await self.db.execute(
                    select(HrLetter)
                    .where(
                        HrLetter.school_id == school_id,
                        HrLetter.employee_id == emp.id,
                        HrLetter.status == "ISSUED",
                    )
                    .order_by(HrLetter.issue_date.desc(), HrLetter.created_at.desc())
                )
            ).scalars()
        )
        return await self._many_out(school_id, rows)

    async def pdf(
        self, school_id: UUID, letter_id: UUID, *, user_id: UUID, is_admin: bool
    ) -> tuple[bytes, str]:
        letter = await self._get(school_id, letter_id)
        if not is_admin:
            emp = await employee_for_user(self.db, school_id, user_id)
            if letter.employee_id != emp.id or letter.status != "ISSUED":
                raise NotFoundError("Letter not found")
        out = (await self._many_out(school_id, [letter]))[0]
        lines = [(await self._school_name(school_id)).upper(), ""]
        if out.status == "DRAFT":
            lines += ["DRAFT - NOT ISSUED", ""]
        elif out.status == "REVOKED":
            lines += [
                f"REVOKED ON {_fmt(out.revoked_at.date() if out.revoked_at else None).upper()}",
                "",
            ]
        if out.reference_number:
            lines.append(f"Ref: {out.reference_number}")
        lines.append(f"Date: {_fmt(out.issue_date) or _fmt(date.today())}")
        lines += ["", f"To: {out.employee_name}"]
        detail = " | ".join(
            x
            for x in (out.employee_code, out.designation_name, out.department_name)
            if x
        )
        if detail:
            lines.append(f"    {detail}")
        lines += ["", f"Subject: {out.title}", ""]
        lines += out.body.splitlines()
        name = f"{(out.reference_number or LETTER_LABELS[out.letter_type]).replace('/', '-')}.pdf".replace(
            " ", "-"
        )
        return build_text_pdf(lines), name

    # ── writes ─────────────────────────────────────────────────────────────

    async def create(
        self, school_id: UUID, user_id: UUID, signatory: str, data: LetterCreate
    ) -> LetterOut:
        emp = await get_employee_brief(self.db, school_id, data.employee_id)
        tpl = render_template(
            data.letter_type, emp, await self._school_name(school_id), signatory
        )
        letter = HrLetter(
            school_id=school_id,
            employee_id=emp.id,
            letter_type=data.letter_type,
            title=data.title or tpl.title,
            body=data.body if data.body else tpl.body,
            status="DRAFT",
            created_by=user_id,
            updated_by=user_id,
        )
        self.db.add(letter)
        await self.db.flush()
        return await self._reload_out(school_id, letter)

    async def update(
        self, school_id: UUID, user_id: UUID, letter_id: UUID, data: LetterUpdate
    ) -> LetterOut:
        letter = await self._get(school_id, letter_id)
        self._assert_draft(letter, "edited")
        payload = data.model_dump(exclude_unset=True, exclude_none=True)
        for k, v in payload.items():
            setattr(letter, k, v)
        letter.updated_by = user_id
        await self.db.flush()
        return await self._reload_out(school_id, letter)

    async def issue(
        self, school_id: UUID, user_id: UUID, letter_id: UUID, data: LetterIssue
    ) -> LetterOut:
        letter = await self._get(school_id, letter_id)
        self._assert_draft(letter, "issued")
        if not letter.body.strip():
            raise AppError("Add the letter content before issuing it", status_code=422)
        emp = await get_employee_brief(self.db, school_id, letter.employee_id)
        letter.status = "ISSUED"
        letter.issue_date = data.issue_date or date.today()
        letter.issued_at = datetime.now(UTC)
        letter.issued_by = user_id
        letter.updated_by = user_id
        await self.db.flush()
        await self._assign_reference(school_id, letter)
        out = await self._reload_out(school_id, letter)
        await HrNotifier(self.db).to_employee(
            school_id,
            emp.user_id,
            kind="letter",
            category=HR_LETTER,
            title=f"New HR letter: {out.title}",
            body=f"{out.reference_number} is ready to download from My HR.",
            entity_id=letter.id,
            actor=user_id,
        )
        return out

    async def revoke(
        self, school_id: UUID, user_id: UUID, letter_id: UUID, data: LetterRevoke
    ) -> LetterOut:
        letter = await self._get(school_id, letter_id)
        if letter.status != "ISSUED":
            raise AppError("Only issued letters can be revoked", status_code=422)
        letter.status = "REVOKED"
        letter.revoked_at = datetime.now(UTC)
        letter.revoked_by = user_id
        letter.revoke_reason = data.reason
        letter.updated_by = user_id
        await self.db.flush()
        out = await self._reload_out(school_id, letter)
        emp = await get_employee_brief(self.db, school_id, letter.employee_id)
        await HrNotifier(self.db).to_employee(
            school_id,
            emp.user_id,
            kind="letter",
            category=HR_LETTER,
            title=f"HR letter revoked: {out.title}",
            body=f"{out.reference_number} — {data.reason}",
            entity_id=letter.id,
            actor=user_id,
        )
        return out

    async def delete(self, school_id: UUID, letter_id: UUID) -> None:
        letter = await self._get(school_id, letter_id)
        self._assert_draft(letter, "deleted")
        await self.db.delete(letter)
        await self.db.flush()

    # ── internals ──────────────────────────────────────────────────────────

    @staticmethod
    def _assert_draft(letter: HrLetter, verb: str) -> None:
        if letter.status != "DRAFT":
            raise AppError(
                f"Only draft letters can be {verb}; this letter is {letter.status.lower()}",
                status_code=422,
            )

    async def _assign_reference(self, school_id: UUID, letter: HrLetter) -> None:
        year = letter.issue_date.year if letter.issue_date else date.today().year
        rules = await load_rules(self.db, school_id)
        prefix = f"{rules.letter_reference_prefix}/{year}/"
        for _ in range(3):
            last = (
                await self.db.execute(
                    select(
                        func.max(
                            cast(
                                func.substr(HrLetter.reference_number, len(prefix) + 1),
                                Integer,
                            )
                        )
                    ).where(
                        HrLetter.school_id == school_id,
                        HrLetter.reference_number.op("~")(
                            f"^{re.escape(prefix)}[0-9]{{1,9}}$"
                        ),
                    )
                )
            ).scalar_one_or_none()
            try:
                async with self.db.begin_nested():
                    letter.reference_number = f"{prefix}{(last or 0) + 1:04d}"
                    await self.db.flush()
                return
            except IntegrityError:
                continue
        raise AppError(
            "Could not assign a reference number; please try again", status_code=409
        )

    async def _get(self, school_id: UUID, letter_id: UUID) -> HrLetter:
        letter = (
            await self.db.execute(
                select(HrLetter).where(
                    HrLetter.id == letter_id, HrLetter.school_id == school_id
                )
            )
        ).scalar_one_or_none()
        if letter is None:
            raise NotFoundError("Letter not found")
        return letter

    async def _reload_out(self, school_id: UUID, letter: HrLetter) -> LetterOut:
        fresh = (
            await self.db.execute(
                select(HrLetter)
                .where(HrLetter.id == letter.id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        return (await self._many_out(school_id, [fresh]))[0]

    async def _school_name(self, school_id: UUID) -> str:
        return (
            await self.db.execute(select(School.name).where(School.id == school_id))
        ).scalar_one_or_none() or "School"

    async def _many_out(self, school_id: UUID, rows: list[HrLetter]) -> list[LetterOut]:
        emps = await employee_briefs(self.db, school_id, {r.employee_id for r in rows})
        users = await user_names(
            self.db,
            {
                u
                for r in rows
                for u in (r.issued_by, r.revoked_by, r.created_by, r.updated_by)
            },
        )
        out: list[LetterOut] = []
        for r in rows:
            emp = emps.get(r.employee_id)
            out.append(
                LetterOut(
                    id=r.id,
                    employee_id=r.employee_id,
                    employee_name=emp.full_name if emp else "—",
                    employee_code=emp.employee_code if emp else None,
                    department_name=emp.department_name if emp else None,
                    designation_name=emp.designation_name if emp else None,
                    letter_type=r.letter_type,
                    title=r.title,
                    body=r.body,
                    status=r.status,
                    reference_number=r.reference_number,
                    issue_date=r.issue_date,
                    issued_at=r.issued_at,
                    issued_by_name=users.get(r.issued_by) if r.issued_by else None,
                    revoked_at=r.revoked_at,
                    revoked_by_name=users.get(r.revoked_by) if r.revoked_by else None,
                    revoke_reason=r.revoke_reason,
                    created_by_name=users.get(r.created_by) if r.created_by else None,
                    updated_by_name=users.get(r.updated_by) if r.updated_by else None,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
            )
        return out
