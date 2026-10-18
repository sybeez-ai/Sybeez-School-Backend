"""Class heatmap: students x subjects, from the school's computed progress (never estimated)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Enrollment, Student, StudentProgress, Subject, User
from src.shared.enums import EnrollmentStatus

BANDS = (("needs-work", 0, 50), ("ok", 50, 75), ("strong", 75, 101))


def band(value: float | None) -> str:
    if value is None:
        return "no-data"
    for name, lo, hi in BANDS:
        if lo <= value < hi:
            return name
    return "strong"


async def class_heatmap(db: AsyncSession, school_id: UUID, section_id: UUID) -> dict:
    students = (
        await db.execute(
            select(Student.id, User.full_name, Student.admission_no)
            .join(Enrollment, Enrollment.student_id == Student.id)
            .join(User, User.id == Student.user_id)
            .where(
                Enrollment.school_id == school_id,
                Enrollment.class_id == section_id,
                Enrollment.status == EnrollmentStatus.ACTIVE.value,
            )
            .order_by(User.full_name)
        )
    ).all()
    ids = [s.id for s in students]
    scores: dict[tuple[UUID, UUID], float] = {}
    subject_ids: set[UUID] = set()
    if ids:
        for row in (
            await db.execute(
                select(StudentProgress.student_id, StudentProgress.subject_id, StudentProgress.value).where(
                    StudentProgress.school_id == school_id,
                    StudentProgress.student_id.in_(ids),
                    StudentProgress.metric == "overall",
                )
            )
        ).all():
            scores[(row.student_id, row.subject_id)] = float(row.value)
            subject_ids.add(row.subject_id)
    names = {}
    if subject_ids:
        names = dict((await db.execute(select(Subject.id, Subject.name).where(Subject.id.in_(subject_ids)))).all())
    subjects = sorted(subject_ids, key=lambda s: names.get(s) or "")
    rows = []
    for s in students:
        cells = []
        for sub in subjects:
            v = scores.get((s.id, sub))
            cells.append({"subject_id": str(sub), "value": None if v is None else round(v, 1), "band": band(v)})
        known = [c["value"] for c in cells if c["value"] is not None]
        rows.append({
            "student_id": str(s.id),
            "name": s.full_name or s.admission_no or "Student",
            "average": round(sum(known) / len(known), 1) if known else None,
            "cells": cells,
        })
    col_avg = []
    for i, sub in enumerate(subjects):
        vals = [r["cells"][i]["value"] for r in rows if r["cells"][i]["value"] is not None]
        col_avg.append({"subject_id": str(sub), "subject_name": names.get(sub) or "Subject",
                        "average": round(sum(vals) / len(vals), 1) if vals else None, "students_scored": len(vals)})
    return {"section_id": str(section_id), "subjects": col_avg, "students": rows,
            "legend": {"needs-work": "below 50%", "ok": "50-74%", "strong": "75% and above", "no-data": "no marks yet"}}
