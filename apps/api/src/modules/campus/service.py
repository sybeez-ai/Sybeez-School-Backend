"""Library, activities, events, and the school calendar."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from src.db.models import ClassRoom, Enrollment, Parent, Student, StudentParent
from src.db.models.campus import (
    LibraryCopy,
    LibraryFine,
    LibraryLoan,
    LibraryTitle,
    SchoolActivity,
    SchoolCalendarItem,
)
from src.shared.enums import EnrollmentStatus, UserRole

FAMILY = {UserRole.STUDENT, UserRole.PARENT}
WRITERS = {UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.PLATFORM_ADMIN}
LIBRARY_WRITERS = {UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN}


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def _dump(row) -> dict:
    out = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, (date, UUID)):
            value = str(value)
        out[column.name] = value
    return out


class CampusService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _write(self, user: CurrentUser, *, library: bool = False) -> None:
        allowed = LIBRARY_WRITERS if library else WRITERS
        if user.role not in allowed:
            raise ForbiddenError("You cannot change this")

    async def _student_ids(self, user: CurrentUser) -> set[UUID] | None:
        """None means the caller may see the whole school. A set is their own children."""
        if user.role == UserRole.STUDENT:
            student = (
                await self.db.execute(
                    select(Student).where(Student.school_id == user.school_id, Student.user_id == user.id)
                )
            ).scalar_one_or_none()
            return {student.id} if student else set()
        if user.role == UserRole.PARENT:
            parent = (
                await self.db.execute(select(Parent).where(Parent.school_id == user.school_id, Parent.user_id == user.id))
            ).scalar_one_or_none()
            if parent is None:
                return set()
            rows = (
                await self.db.execute(select(StudentParent.student_id).where(StudentParent.parent_id == parent.id))
            ).scalars().all()
            return set(rows)
        return None

    async def _class_ids(self, user: CurrentUser) -> set[UUID] | None:
        student_ids = await self._student_ids(user)
        if student_ids is None:
            return None
        if not student_ids:
            return set()
        rows = (
            await self.db.execute(
                select(Enrollment.class_id).where(
                    Enrollment.school_id == user.school_id,
                    Enrollment.student_id.in_(student_ids),
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalars().all()
        return set(rows)

    async def titles(self, user: CurrentUser) -> list[dict]:
        rows = (
            await self.db.execute(
                select(LibraryTitle).where(LibraryTitle.school_id == user.school_id).order_by(LibraryTitle.title).limit(300)
            )
        ).scalars().all()
        return [_dump(row) for row in rows]

    async def add_title(self, user: CurrentUser, data) -> dict:
        self._write(user, library=True)
        row = LibraryTitle(
            id=uuid4(), school_id=user.school_id, title=data.title.strip(),
            author=(data.author or "").strip() or None, isbn=(data.isbn or "").strip() or None,
            subject=(data.subject or "").strip() or None,
        )
        self.db.add(row)
        await self.db.flush()
        return _dump(row)

    async def copies(self, user: CurrentUser, title_id: UUID | None) -> list[dict]:
        stmt = select(LibraryCopy).where(LibraryCopy.school_id == user.school_id)
        if title_id:
            stmt = stmt.where(LibraryCopy.title_id == title_id)
        rows = (await self.db.execute(stmt.order_by(LibraryCopy.barcode).limit(500))).scalars().all()
        return [_dump(row) for row in rows]

    async def add_copy(self, user: CurrentUser, data) -> dict:
        self._write(user, library=True)
        title = await self.db.get(LibraryTitle, data.title_id)
        if title is None or title.school_id != user.school_id:
            raise NotFoundError("Book not found")
        row = LibraryCopy(id=uuid4(), school_id=user.school_id, title_id=title.id, barcode=data.barcode.strip(), status="AVAILABLE")
        self.db.add(row)
        await self.db.flush()
        return _dump(row)

    async def loans(self, user: CurrentUser) -> list[dict]:
        stmt = select(LibraryLoan).where(LibraryLoan.school_id == user.school_id)
        mine = await self._student_ids(user)
        if mine is not None:
            stmt = stmt.where(LibraryLoan.student_id.in_(mine or {uuid4()}))
        rows = (await self.db.execute(stmt.order_by(LibraryLoan.issued_on.desc()).limit(300))).scalars().all()
        return [_dump(row) for row in rows]

    async def add_loan(self, user: CurrentUser, data) -> dict:
        self._write(user, library=True)
        copy = await self.db.get(LibraryCopy, data.copy_id)
        if copy is None or copy.school_id != user.school_id:
            raise NotFoundError("Copy not found")
        if copy.status != "AVAILABLE":
            raise ValidationError("That copy is not available")
        student = await self.db.get(Student, data.student_id)
        if student is None or student.school_id != user.school_id:
            raise NotFoundError("Student not found")
        if data.due_on < data.issued_on:
            raise ValidationError("The due date must be on or after the issue date")
        copy.status = "LOANED"
        row = LibraryLoan(
            id=uuid4(), school_id=user.school_id, copy_id=copy.id, student_id=student.id,
            issued_on=data.issued_on, due_on=data.due_on,
        )
        self.db.add(row)
        await self.db.flush()
        return _dump(row)

    async def return_loan(self, user: CurrentUser, loan_id: UUID, returned_on: date, fine_amount: Decimal | None) -> dict:
        self._write(user, library=True)
        loan = await self.db.get(LibraryLoan, loan_id)
        if loan is None or loan.school_id != user.school_id:
            raise NotFoundError("Loan not found")
        if loan.returned_on is not None:
            raise ValidationError("That loan is already returned")
        loan.returned_on = returned_on
        copy = await self.db.get(LibraryCopy, loan.copy_id)
        if copy is not None:
            copy.status = "AVAILABLE"
        if fine_amount and fine_amount > 0:
            self.db.add(LibraryFine(id=uuid4(), school_id=user.school_id, loan_id=loan.id, amount=_money(fine_amount), status="OPEN"))
        await self.db.flush()
        return _dump(loan)

    async def fines(self, user: CurrentUser) -> list[dict]:
        stmt = select(LibraryFine).where(LibraryFine.school_id == user.school_id)
        mine = await self._student_ids(user)
        if mine is not None:
            stmt = stmt.join(LibraryLoan, LibraryLoan.id == LibraryFine.loan_id).where(LibraryLoan.student_id.in_(mine or {uuid4()}))
        rows = (await self.db.execute(stmt.order_by(LibraryFine.created_at.desc()).limit(300))).scalars().all()
        return [_dump(row) for row in rows]

    async def add_fine(self, user: CurrentUser, data) -> dict:
        self._write(user, library=True)
        loan = await self.db.get(LibraryLoan, data.loan_id)
        if loan is None or loan.school_id != user.school_id:
            raise NotFoundError("Loan not found")
        row = LibraryFine(id=uuid4(), school_id=user.school_id, loan_id=loan.id, amount=_money(data.amount), status="OPEN", note=data.note)
        self.db.add(row)
        await self.db.flush()
        return _dump(row)

    async def settle_fine(self, user: CurrentUser, fine_id: UUID, status: str) -> dict:
        self._write(user, library=True)
        if status not in {"PAID", "WAIVED"}:
            raise ValidationError("A fine can be marked paid or waived")
        row = await self.db.get(LibraryFine, fine_id)
        if row is None or row.school_id != user.school_id:
            raise NotFoundError("Fine not found")
        row.status = status
        await self.db.flush()
        return _dump(row)

    async def _visible_section(self, user: CurrentUser, model, order):
        stmt = select(model).where(model.school_id == user.school_id)
        classes = await self._class_ids(user)
        if classes is not None:
            stmt = stmt.where(or_(model.class_id.is_(None), model.class_id.in_(classes or {uuid4()})))
        rows = (await self.db.execute(stmt.order_by(order).limit(300))).scalars().all()
        return [_dump(row) for row in rows]

    async def activities(self, user: CurrentUser) -> list[dict]:
        return await self._visible_section(user, SchoolActivity, SchoolActivity.name)

    async def add_activity(self, user: CurrentUser, data) -> dict:
        self._write(user)
        if data.kind not in {"ACTIVITY", "EVENT"}:
            raise ValidationError("Kind must be ACTIVITY or EVENT")
        await self._class_ok(user, data.class_id)
        row = SchoolActivity(
            id=uuid4(), school_id=user.school_id, class_id=data.class_id, name=data.name.strip(),
            kind=data.kind, description=data.description, starts_on=data.starts_on, ends_on=data.ends_on, place=data.place,
        )
        self.db.add(row)
        await self.db.flush()
        return _dump(row)

    async def calendar(self, user: CurrentUser, kind: str | None) -> list[dict]:
        stmt = select(SchoolCalendarItem).where(SchoolCalendarItem.school_id == user.school_id)
        if kind:
            stmt = stmt.where(SchoolCalendarItem.kind == kind)
        classes = await self._class_ids(user)
        if classes is not None:
            stmt = stmt.where(or_(SchoolCalendarItem.class_id.is_(None), SchoolCalendarItem.class_id.in_(classes or {uuid4()})))
        rows = (await self.db.execute(stmt.order_by(SchoolCalendarItem.starts_on).limit(400))).scalars().all()
        return [_dump(row) for row in rows]

    async def add_calendar(self, user: CurrentUser, data) -> dict:
        self._write(user)
        if data.kind not in {"HOLIDAY", "TERM", "EXAM", "EVENT"}:
            raise ValidationError("Kind must be HOLIDAY, TERM, EXAM, or EVENT")
        if data.ends_on < data.starts_on:
            raise ValidationError("The end date must be on or after the start date")
        await self._class_ok(user, data.class_id)
        row = SchoolCalendarItem(
            id=uuid4(), school_id=user.school_id, class_id=data.class_id, kind=data.kind,
            title=data.title.strip(), starts_on=data.starts_on, ends_on=data.ends_on, notes=data.notes,
        )
        self.db.add(row)
        await self.db.flush()
        return _dump(row)

    async def _class_ok(self, user: CurrentUser, class_id: UUID | None) -> None:
        if class_id is None:
            return
        classroom = await self.db.get(ClassRoom, class_id)
        if classroom is None or classroom.school_id != user.school_id:
            raise NotFoundError("Class not found")
