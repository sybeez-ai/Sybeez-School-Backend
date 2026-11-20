"""HR letters: drafts, issue/revoke lifecycle, versioned templates and branded PDF output."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, NotFoundError
from src.db.models import HrLetter, HrLetterTemplate, School
from src.db.models.hr import LETTER_TYPES
from src.modules.employees.lookup import (
    EmployeeBrief,
    employee_briefs,
    employee_for_user,
    get_employee_brief,
    user_names,
)
from src.modules.hr.audit import hr_audit
from src.modules.hr.text_pdf import build_text_pdf
from src.modules.hr_letters.schemas import (
    LetterCreate,
    LetterIssue,
    LetterOut,
    LetterRevoke,
    LetterTemplateOut,
    LetterTemplateSave,
    LetterTemplateSummary,
    LetterTemplateVersionOut,
    LetterUpdate,
)
from src.modules.hr_notifications.service import HR_LETTER, HrNotifier
from src.modules.hr_settings.service import load_rules

LETTER_LABELS = {
    "OFFER": "Offer letter",
    "APPOINTMENT": "Appointment letter",
    "CONFIRMATION": "Confirmation letter",
    "EMPLOYMENT_VERIFICATION": "Employment verification letter",
    "SALARY_CERTIFICATE": "Salary certificate",
    "EXPERIENCE": "Experience letter",
    "PROMOTION": "Promotion letter",
    "TRANSFER": "Transfer letter",
    "DESIGNATION_CHANGE": "Designation change letter",
    "INCREMENT": "Increment / salary revision letter",
    "RELIEVING": "Relieving letter",
    "OTHER": "Custom letter",
}

PLACEHOLDERS = (
    "employee_name",
    "employee_code",
    "employee_ref",
    "designation",
    "department",
    "joining_date",
    "exit_date",
    "school_name",
    "signatory",
    "today",
)
_PLACEHOLDER_RE = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")
# Blanks in the built-in wording that only the admin can fill in.
_UNFILLED_RE = re.compile(r"\[(amount|new designation|new department|effective date|last working day)\]")

_SIGN = "Sincerely,\n\n{signatory}\nFor {school_name}"
# Salary figures are never filled in automatically: Payroll is the source of truth, so the
# admin types the confirmed amounts into the bracketed spaces before issuing.
BUILT_IN_TEMPLATES: dict[str, tuple[str, str]] = {
    "OFFER": (
        "Offer letter - {employee_name}",
        "Dear {employee_name},\n\n"
        "We are pleased to offer you the position of {designation} at {school_name}{department}. "
        "Your expected date of joining is {joining_date}.\n\n"
        "Please confirm your acceptance by signing and returning a copy of this letter.\n\n"
        "We look forward to welcoming you.\n\n" + _SIGN,
    ),
    "APPOINTMENT": (
        "Appointment letter - {employee_name}",
        "Dear {employee_name},\n\n"
        "This letter confirms your appointment as {designation} at {school_name}{department} "
        "with effect from {joining_date}. Your terms of employment are as agreed and as per school policy.\n\n"
        + _SIGN,
    ),
    "CONFIRMATION": (
        "Confirmation of employment - {employee_name}",
        "Dear {employee_name},\n\n"
        "We are pleased to confirm your employment as {designation} at {school_name}{department} "
        "following the successful completion of your probation period. All other terms of your "
        "appointment remain unchanged.\n\n" + _SIGN,
    ),
    "EMPLOYMENT_VERIFICATION": (
        "Employment verification - {employee_name}",
        "To whom it may concern,\n\n"
        "This is to verify that {employee_ref} is employed at {school_name} as {designation}{department} "
        "since {joining_date}.\n\n"
        "This letter is issued at the request of the employee for verification purposes.\n\n" + _SIGN,
    ),
    "SALARY_CERTIFICATE": (
        "Salary certificate - {employee_name}",
        "To whom it may concern,\n\n"
        "This is to certify that {employee_ref} is employed at {school_name} as {designation}{department} "
        "since {joining_date}.\n\n"
        "As per the school's payroll records, the gross monthly salary is [amount] and the net monthly "
        "salary is [amount].\n\n"
        "This certificate is issued at the request of the employee.\n\n" + _SIGN,
    ),
    "EXPERIENCE": (
        "Experience letter - {employee_name}",
        "To whom it may concern,\n\n"
        "This is to certify that {employee_ref} worked at {school_name} as {designation}{department} "
        "from {joining_date} to {exit_date}. During this period we found them sincere and dedicated.\n\n"
        "We wish them every success.\n\n" + _SIGN,
    ),
    "PROMOTION": (
        "Promotion letter - {employee_name}",
        "Dear {employee_name},\n\n"
        "We are pleased to inform you that you have been promoted to the position of [new designation] "
        "with effect from [effective date], in recognition of your contribution to {school_name}.\n\n"
        "Congratulations.\n\n" + _SIGN,
    ),
    "TRANSFER": (
        "Transfer letter - {employee_name}",
        "Dear {employee_name},\n\n"
        "This is to inform you that you are transferred from{department} to [new department] "
        "with effect from [effective date]. Your designation and other terms remain unchanged.\n\n"
        + _SIGN,
    ),
    "DESIGNATION_CHANGE": (
        "Change of designation - {employee_name}",
        "Dear {employee_name},\n\n"
        "This is to inform you that your designation changes from {designation} to [new designation] "
        "with effect from [effective date]. All other terms of your employment remain unchanged.\n\n"
        + _SIGN,
    ),
    "INCREMENT": (
        "Salary revision - {employee_name}",
        "Dear {employee_name},\n\n"
        "We are pleased to inform you that your salary has been revised with effect from [effective date]. "
        "Your revised gross monthly salary is [amount], as approved in payroll.\n\n"
        "We appreciate your contribution to {school_name}.\n\n" + _SIGN,
    ),
    "RELIEVING": (
        "Relieving letter - {employee_name}",
        "Dear {employee_name},\n\n"
        "This is to confirm that {employee_ref}, {designation}, has been relieved of duties at "
        "{school_name} with effect from {exit_date}. Responsibilities have been handed over as per "
        "school policy.\n\n"
        "We thank you for your contribution and wish you well.\n\n" + _SIGN,
    ),
    "OTHER": ("HR letter - {employee_name}", "Dear {employee_name},\n\n\n\n" + _SIGN),
}


def _fmt(d: date | None) -> str:
    return d.strftime("%d %b %Y") if d else ""


def placeholder_values(emp: EmployeeBrief, school_name: str, signatory: str) -> dict[str, str]:
    return {
        "employee_name": emp.full_name,
        "employee_code": emp.employee_code or "",
        "employee_ref": f"{emp.full_name} ({emp.employee_code})" if emp.employee_code else emp.full_name,
        "designation": emp.designation_name or emp.title or "staff member",
        "department": f" in the {emp.department_name} department" if emp.department_name else "",
        "joining_date": _fmt(emp.joining_date) or "the agreed date",
        "exit_date": _fmt(emp.exit_date) or "[last working day]",
        "school_name": school_name,
        "signatory": signatory,
        "today": _fmt(date.today()),
    }


def fill(text: str, values: dict[str, str]) -> str:
    """Replace known ``{placeholder}`` tokens only; any other braces are left as typed."""
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], text)


def render_template(
    letter_type: str,
    emp: EmployeeBrief,
    school_name: str,
    signatory: str,
    saved: HrLetterTemplate | None = None,
) -> LetterTemplateOut:
    title, body = (saved.title, saved.body) if saved else BUILT_IN_TEMPLATES[letter_type]
    values = placeholder_values(emp, school_name, signatory)
    return LetterTemplateOut(
        letter_type=letter_type,
        title=fill(title, values)[:200],
        body=fill(body, values),
        template_version=saved.version if saved else None,
    )


class HrLetterService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── templates ──────────────────────────────────────────────────────────

    async def _current_template(self, school_id: UUID, letter_type: str) -> HrLetterTemplate | None:
        return (
            await self.db.execute(
                select(HrLetterTemplate)
                .where(HrLetterTemplate.school_id == school_id, HrLetterTemplate.letter_type == letter_type)
                .order_by(HrLetterTemplate.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def list_templates(self, school_id: UUID) -> list[LetterTemplateSummary]:
        latest = (
            select(HrLetterTemplate.letter_type, func.max(HrLetterTemplate.version).label("v"))
            .where(HrLetterTemplate.school_id == school_id)
            .group_by(HrLetterTemplate.letter_type)
            .subquery()
        )
        rows = (
            await self.db.execute(
                select(HrLetterTemplate).join(
                    latest,
                    (latest.c.letter_type == HrLetterTemplate.letter_type) & (latest.c.v == HrLetterTemplate.version),
                ).where(HrLetterTemplate.school_id == school_id)
            )
        ).scalars()
        saved = {r.letter_type: r for r in rows}
        names = await user_names(self.db, {r.created_by for r in saved.values()})
        out = []
        for t in LETTER_TYPES:
            s = saved.get(t)
            title, body = (s.title, s.body) if s else BUILT_IN_TEMPLATES[t]
            out.append(
                LetterTemplateSummary(
                    letter_type=t,
                    label=LETTER_LABELS[t],
                    title=title,
                    body=body,
                    current_version=s.version if s else None,
                    is_built_in=s is None,
                    updated_at=s.created_at if s else None,
                    updated_by_name=names.get(s.created_by) if s and s.created_by else None,
                    placeholders=list(PLACEHOLDERS),
                )
            )
        return out

    async def template_versions(self, school_id: UUID, letter_type: str) -> list[LetterTemplateVersionOut]:
        rows = list(
            (
                await self.db.execute(
                    select(HrLetterTemplate)
                    .where(HrLetterTemplate.school_id == school_id, HrLetterTemplate.letter_type == letter_type)
                    .order_by(HrLetterTemplate.version.desc())
                )
            ).scalars()
        )
        names = await user_names(self.db, {r.created_by for r in rows})
        return [
            LetterTemplateVersionOut(
                letter_type=r.letter_type,
                version=r.version,
                title=r.title,
                body=r.body,
                created_by_name=names.get(r.created_by) if r.created_by else None,
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def save_template(
        self, school_id: UUID, user_id: UUID, letter_type: str, data: LetterTemplateSave
    ) -> LetterTemplateVersionOut:
        """Saving never edits an earlier version: it adds the next one, which becomes current."""
        for _ in range(3):
            current = await self._current_template(school_id, letter_type)
            if current and current.title == data.title and current.body == data.body:
                raise AppError("No changes to save", status_code=422)
            row = HrLetterTemplate(
                school_id=school_id,
                letter_type=letter_type,
                version=(current.version if current else 0) + 1,
                title=data.title,
                body=data.body,
                created_by=user_id,
            )
            try:
                async with self.db.begin_nested():
                    self.db.add(row)
                    await self.db.flush()
            except IntegrityError:
                continue
            await hr_audit(
                self.db,
                school_id=school_id,
                actor_id=user_id,
                action="hr.letter_template.saved",
                resource_type="hr_letter_template",
                resource_id=row.id,
                message=f"{LETTER_LABELS[letter_type]} template saved as version {row.version}",
                details={"letter_type": letter_type, "version": row.version},
            )
            return (await self.template_versions(school_id, letter_type))[0]
        raise AppError("Could not save the template; please try again", status_code=409)

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
            letter_type,
            emp,
            await self._school_name(school_id),
            signatory,
            await self._current_template(school_id, letter_type),
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
        lines = await self._letterhead(school_id)
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
        tpl = await self.template(school_id, emp.id, data.letter_type, signatory)
        letter = HrLetter(
            school_id=school_id,
            employee_id=emp.id,
            letter_type=data.letter_type,
            title=data.title or tpl.title,
            body=data.body if data.body else tpl.body,
            template_version=tpl.template_version,
            status="DRAFT",
            created_by=user_id,
            updated_by=user_id,
        )
        self.db.add(letter)
        await self.db.flush()
        await self._audit(letter, user_id, "created", f"{LETTER_LABELS[letter.letter_type]} drafted for {emp.full_name}")
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
        if _UNFILLED_RE.search(letter.body):
            raise AppError(
                "Fill in the bracketed details (for example [amount] or [effective date]) before issuing",
                status_code=422,
            )
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
            event="issued",
            actor=user_id,
        )
        await self._audit(
            letter, user_id, "issued", f"{out.reference_number} issued to {emp.full_name}",
            {"reference_number": out.reference_number, "issue_date": out.issue_date},
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
            event="revoked",
            actor=user_id,
        )
        await self._audit(
            letter, user_id, "revoked", f"{out.reference_number} revoked", {"reason": data.reason}
        )
        return out

    async def delete(self, school_id: UUID, user_id: UUID, letter_id: UUID) -> None:
        letter = await self._get(school_id, letter_id)
        self._assert_draft(letter, "deleted")
        await self._audit(letter, user_id, "draft_deleted", f"Draft '{letter.title}' deleted")
        await self.db.delete(letter)
        await self.db.flush()

    # ── internals ──────────────────────────────────────────────────────────

    async def _audit(
        self, letter: HrLetter, user_id: UUID, verb: str, message: str, details: dict | None = None
    ) -> None:
        await hr_audit(
            self.db,
            school_id=letter.school_id,
            actor_id=user_id,
            action=f"hr.letter.{verb}",
            resource_type="hr_letter",
            resource_id=letter.id,
            message=message,
            details={"employee_id": letter.employee_id, "letter_type": letter.letter_type, **(details or {})},
        )

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

    async def _letterhead(self, school_id: UUID) -> list[str]:
        """School branding block printed at the top of every letter."""
        school = await self.db.get(School, school_id)
        if school is None:
            return ["SCHOOL", ""]
        lines = [school.name.upper()]
        place = ", ".join(x for x in (school.address, school.city, school.state, school.postal_code) if x)
        if place:
            lines.append(place)
        contact = " | ".join(x for x in (school.phone, school.email, school.website) if x)
        if contact:
            lines.append(contact)
        lines += ["-" * 78, ""]
        return lines

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
                    template_version=r.template_version,
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
