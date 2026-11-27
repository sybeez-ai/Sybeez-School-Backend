from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ForbiddenError, NotFoundError
from src.db.models import ClassRoom, ClassSubject, Subject, Teacher, TeacherAssignment, User
from src.db.models.question_bank import QuestionBankItem
from src.modules.question_bank.schemas import QuestionBankCreate, QuestionBankOut, QuestionBankUpdate
from src.shared.enums import UserRole
from src.shared.utils import format_class_label


def _options_list(value) -> list[str] | None:
    if not value:
        return None
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.append(str(item.get("text") or item.get("label") or ""))
            else:
                out.append(str(item))
        return [x for x in out if x]
    return None


def _correct_index(options: list[str] | None, answer_key: str | None) -> int | None:
    if not options or answer_key is None or answer_key == "":
        return None
    if answer_key.isdigit():
        idx = int(answer_key)
        if 0 <= idx < len(options):
            return idx
    try:
        return options.index(answer_key)
    except ValueError:
        return None


def _answer_key(options: list[str] | None, correct_index: int | None, fallback: str | None) -> str | None:
    if options is not None and correct_index is not None and 0 <= correct_index < len(options):
        return str(correct_index)
    return fallback


class QuestionBankService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _teacher(self, school_id: UUID, user_id: UUID) -> Teacher | None:
        return (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
            )
        ).scalar_one_or_none()

    async def subject_ids_for_teacher(self, school_id: UUID, teacher_id: UUID) -> set[UUID]:
        assigned = (
            await self.db.execute(
                select(TeacherAssignment.subject_id).where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.teacher_id == teacher_id,
                )
            )
        ).scalars().all()
        class_subj = (
            await self.db.execute(
                select(ClassSubject.subject_id).where(
                    ClassSubject.school_id == school_id,
                    ClassSubject.teacher_id == teacher_id,
                )
            )
        ).scalars().all()
        return {sid for sid in list(assigned) + list(class_subj) if sid}

    async def _assert_can_write(
        self, school_id: UUID, user, subject_id: UUID
    ) -> Teacher | None:
        role = getattr(user, "role", None)
        if role in {UserRole.SCHOOL_ADMIN, UserRole.SCHOOL_ADMIN.value, "SCHOOL_ADMIN", "PLATFORM_ADMIN"}:
            return None
        teacher = await self._teacher(school_id, user.id)
        if not teacher:
            raise ForbiddenError("Only teachers can add questions for their subjects")
        allowed = await self.subject_ids_for_teacher(school_id, teacher.id)
        if subject_id not in allowed:
            raise ForbiddenError("You can only add questions for subjects you teach")
        return teacher

    async def _out(self, row: QuestionBankItem) -> QuestionBankOut:
        subject = await self.db.get(Subject, row.subject_id)
        class_name = None
        if row.class_id:
            room = await self.db.get(ClassRoom, row.class_id)
            if room:
                class_name = format_class_label(room.name, room.section)
        author = None
        if row.created_by:
            user = await self.db.get(User, row.created_by)
            author = user.full_name if user else None
        options = _options_list(row.options)
        return QuestionBankOut(
            id=row.id,
            school_id=row.school_id,
            subject_id=row.subject_id,
            subject_name=subject.name if subject else None,
            class_id=row.class_id,
            class_name=class_name,
            created_by=row.created_by,
            author_name=author,
            teacher_id=row.teacher_id,
            prompt=row.prompt,
            question_type=row.question_type,
            options=options,
            answer_key=row.answer_key,
            correct_index=_correct_index(options, row.answer_key),
            points=row.points,
            created_at=row.created_at,
        )

    async def list_items(
        self,
        school_id: UUID,
        *,
        subject_id: UUID | None = None,
        class_id: UUID | None = None,
        user=None,
    ) -> list[QuestionBankOut]:
        q = select(QuestionBankItem).where(QuestionBankItem.school_id == school_id)
        if subject_id:
            q = q.where(QuestionBankItem.subject_id == subject_id)
        if class_id:
            q = q.where(QuestionBankItem.class_id == class_id)
        q = q.order_by(QuestionBankItem.created_at.desc())
        rows = (await self.db.execute(q)).scalars().all()
        return [await self._out(r) for r in rows]

    async def create(self, school_id: UUID, user, data: QuestionBankCreate) -> QuestionBankOut:
        teacher = await self._assert_can_write(school_id, user, data.subject_id)
        subject = await self.db.get(Subject, data.subject_id)
        if not subject or subject.school_id != school_id:
            raise NotFoundError("Subject not found")
        if data.class_id:
            room = await self.db.get(ClassRoom, data.class_id)
            if not room or room.school_id != school_id:
                raise NotFoundError("Class not found")
        options = [o.strip() for o in (data.options or []) if o and o.strip()] or None
        if (data.question_type or "mcq").lower() == "mcq" and options and len(options) < 2:
            raise AppError("Multiple-choice questions need at least two options", status_code=422)
        row = QuestionBankItem(
            school_id=school_id,
            subject_id=data.subject_id,
            class_id=data.class_id,
            created_by=user.id,
            teacher_id=teacher.id if teacher else None,
            prompt=data.prompt.strip(),
            question_type=(data.question_type or "mcq").lower(),
            options=options,
            answer_key=_answer_key(options, data.correct_index, data.answer_key),
            points=data.points,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return await self._out(row)

    async def update(self, school_id: UUID, user, item_id: UUID, data: QuestionBankUpdate) -> QuestionBankOut:
        row = await self.db.get(QuestionBankItem, item_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Question not found")
        role = getattr(user, "role", None)
        is_admin = role in {UserRole.SCHOOL_ADMIN, UserRole.SCHOOL_ADMIN.value, "SCHOOL_ADMIN", "PLATFORM_ADMIN"}
        if not is_admin and row.created_by != user.id:
            raise ForbiddenError("You can only edit your own questions")
        payload = data.model_dump(exclude_unset=True)
        if "prompt" in payload and payload["prompt"]:
            row.prompt = payload["prompt"].strip()
        if "question_type" in payload and payload["question_type"]:
            row.question_type = payload["question_type"].lower()
        if "points" in payload and payload["points"] is not None:
            row.points = payload["points"]
        if "class_id" in payload:
            row.class_id = payload["class_id"]
        options = row.options
        if "options" in payload:
            options = [o.strip() for o in (payload["options"] or []) if o and o.strip()] or None
            row.options = options
        if "correct_index" in payload or "answer_key" in payload:
            row.answer_key = _answer_key(
                _options_list(options),
                payload.get("correct_index"),
                payload.get("answer_key", row.answer_key),
            )
        await self.db.commit()
        await self.db.refresh(row)
        return await self._out(row)

    async def delete(self, school_id: UUID, user, item_id: UUID) -> None:
        row = await self.db.get(QuestionBankItem, item_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Question not found")
        role = getattr(user, "role", None)
        is_admin = role in {UserRole.SCHOOL_ADMIN, UserRole.SCHOOL_ADMIN.value, "SCHOOL_ADMIN", "PLATFORM_ADMIN"}
        if not is_admin and row.created_by != user.id:
            raise ForbiddenError("You can only delete your own questions")
        await self.db.delete(row)
        await self.db.commit()

    async def get_many(self, school_id: UUID, ids: list[UUID]) -> list[QuestionBankItem]:
        if not ids:
            return []
        rows = (
            await self.db.execute(
                select(QuestionBankItem).where(
                    QuestionBankItem.school_id == school_id,
                    QuestionBankItem.id.in_(ids),
                )
            )
        ).scalars().all()
        by_id = {r.id: r for r in rows}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise NotFoundError("One or more questions were not found in the bank")
        return [by_id[i] for i in ids]
