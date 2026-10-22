"""Substitute finder, principal workload and the monthly principal report. Computed from live data."""

from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, literal_column, select

from src.api.dependencies import CurrentUser
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.db.models import (
    Attendance,
    ClassRoom,
    FeeInvoice,
    Homework,
    HomeworkSubmission,
    StaffLeaveRequest,
    SubstituteAssignment,
    Teacher,
    TeacherAssignment,
    TimetableEntry,
    User,
)
from src.modules.access.audit import write_audit
from src.modules.school_ops.planning import Slot, rank_candidates


def _slot(e: TimetableEntry) -> Slot:
    return Slot(str(e.id), str(e.teacher_id) if e.teacher_id else None, str(e.class_id), str(e.subject_id), e.day_of_week, e.starts_at, e.ends_at)


class SchoolOpsService:
    def __init__(self, db):
        self.db = db

    def _sid(self, user: CurrentUser) -> UUID:
        if user.school_id is None:
            raise ForbiddenError("School context required")
        return user.school_id

    async def _teacher_names(self, school_id: UUID) -> dict[str, str]:
        rows = (
            await self.db.execute(
                select(Teacher.id, User.full_name).join(User, User.id == Teacher.user_id).where(
                    Teacher.school_id == school_id, Teacher.status == "ACTIVE"
                )
            )
        ).all()
        return {str(i): n for i, n in rows}

    # ── substitute finder ────────────────────────────────────

    async def candidates(self, user: CurrentUser, absent_teacher_id: UUID, on: date) -> list[dict]:
        school_id = self._sid(user)
        names = await self._teacher_names(school_id)
        dow = on.weekday()
        entries = (
            await self.db.execute(
                select(TimetableEntry).where(
                    TimetableEntry.school_id == school_id, TimetableEntry.day_of_week == dow
                )
            )
        ).scalars().all()
        absent_slots = sorted([e for e in entries if e.teacher_id == absent_teacher_id], key=lambda e: e.starts_at)
        busy: dict[str, list[Slot]] = defaultdict(list)
        for e in entries:
            if e.teacher_id:
                busy[str(e.teacher_id)].append(_slot(e))
        covers = (
            await self.db.execute(
                select(SubstituteAssignment, TimetableEntry)
                .join(TimetableEntry, TimetableEntry.id == SubstituteAssignment.timetable_entry_id)
                .where(
                    SubstituteAssignment.school_id == school_id,
                    SubstituteAssignment.cover_date == on,
                    SubstituteAssignment.status == "CONFIRMED",
                )
            )
        ).all()
        covering: dict[str, list[Slot]] = defaultdict(list)
        covered_entries = {}
        for a, e in covers:
            covering[str(a.substitute_teacher_id)].append(_slot(e))
            covered_entries[str(e.id)] = a
        leave_users = set(
            (
                await self.db.execute(
                    select(StaffLeaveRequest.user_id).where(
                        StaffLeaveRequest.school_id == school_id,
                        StaffLeaveRequest.status == "APPROVED",
                        StaffLeaveRequest.start_date <= on,
                        StaffLeaveRequest.end_date >= on,
                    )
                )
            ).scalars()
        )
        on_leave = {
            str(tid)
            for tid, uid in (await self.db.execute(select(Teacher.id, Teacher.user_id).where(Teacher.school_id == school_id))).all()
            if uid in leave_users
        }
        assigns = (
            await self.db.execute(select(TeacherAssignment).where(TeacherAssignment.school_id == school_id))
        ).scalars().all()
        month_start = on.replace(day=1)
        counts = {
            str(t): n
            for t, n in (
                await self.db.execute(
                    select(SubstituteAssignment.substitute_teacher_id, func.count())
                    .where(
                        SubstituteAssignment.school_id == school_id,
                        SubstituteAssignment.status == "CONFIRMED",
                        SubstituteAssignment.cover_date >= month_start,
                    )
                    .group_by(SubstituteAssignment.substitute_teacher_id)
                )
            ).all()
        }
        out = []
        for e in absent_slots:
            slot = _slot(e)
            subj = {str(a.teacher_id) for a in assigns if a.subject_id == e.subject_id}
            cls = {str(a.teacher_id) for a in assigns if a.class_id == e.class_id}
            ranked = rank_candidates(slot, list(names), busy, on_leave, covering, subj, cls, counts)
            for c in ranked:
                c["name"] = names.get(c["teacher_id"], "Teacher")
            classroom = await self.db.get(ClassRoom, e.class_id)
            existing = covered_entries.get(str(e.id))
            out.append(
                {
                    "timetable_entry_id": str(e.id),
                    "class_name": getattr(classroom, "name", None),
                    "starts_at": e.starts_at.isoformat(timespec="minutes"),
                    "ends_at": e.ends_at.isoformat(timespec="minutes"),
                    "covered_by": str(existing.substitute_teacher_id) if existing else None,
                    "candidates": ranked,
                }
            )
        return out

    async def confirm(
        self, user: CurrentUser, *, entry_id: UUID, on: date, substitute_teacher_id: UUID, note: str | None
    ) -> SubstituteAssignment:
        """Admin confirmation (the human decision). Revalidates freedom server-side, then audits and notifies."""
        school_id = self._sid(user)
        entry = await self.db.get(TimetableEntry, entry_id)
        if entry is None or entry.school_id != school_id:
            raise NotFoundError("Lesson not found")
        if entry.day_of_week != on.weekday():
            raise ValidationError("That lesson does not run on this date")
        options = await self.candidates(user, entry.teacher_id, on) if entry.teacher_id else []
        match = next((o for o in options if o["timetable_entry_id"] == str(entry_id)), None)
        if match is None or str(substitute_teacher_id) not in {c["teacher_id"] for c in match["candidates"]}:
            raise ConflictError("That teacher is not free for this lesson")
        row = SubstituteAssignment(
            school_id=school_id,
            timetable_entry_id=entry_id,
            cover_date=on,
            absent_teacher_id=entry.teacher_id,
            substitute_teacher_id=substitute_teacher_id,
            status="CONFIRMED",
            note=(note or None),
            created_by=user.id,
        )
        self.db.add(row)
        try:
            await self.db.flush()
        except Exception as exc:  # unique index: already covered
            raise ConflictError("This lesson already has a substitute") from exc
        await write_audit(
            self.db,
            school_id=school_id,
            actor_id=user.id,
            action="substitute.confirmed",
            resource_type="substitute_assignment",
            resource_id=str(row.id),
            message="Substitute teacher confirmed",
            details={"entry": str(entry_id), "date": on.isoformat(), "substitute": str(substitute_teacher_id)},
        )
        teacher = await self.db.get(Teacher, substitute_teacher_id)
        if teacher is not None:
            from src.modules.notifications.service import create_in_app_notification

            await create_in_app_notification(
                self.db,
                school_id=school_id,
                user_id=teacher.user_id,
                title="Cover request confirmed",
                body=f"You are covering a lesson on {on.isoformat()} at {entry.starts_at.strftime('%H:%M')}.",
                category="SYSTEM",
                link_path="/teacher/timetable",
                event_key=f"SUBSTITUTE:{row.id}",
            )
            await self._whatsapp_cover(school_id, teacher, row, entry, on)
        return row

    async def _whatsapp_cover(self, school_id: UUID, teacher: Teacher, row: SubstituteAssignment, entry, on: date) -> None:
        """WhatsApp the covering teacher. Inert unless the school's WhatsApp switch, Meta credentials and the
        teacher's own opt-in are all on. Never raises: a failed message must not undo the assignment."""
        try:
            async with self.db.begin_nested():
                from src.modules.alerts.service import dispatch_alert, school_display_name

                teacher_user = await self.db.get(User, teacher.user_id)
                classroom = await self.db.get(ClassRoom, entry.class_id)
                await dispatch_alert(
                    self.db,
                    school_id=school_id,
                    event_key="substitute_assigned",
                    variables={
                        "employee_name": teacher_user.full_name if teacher_user else "Teacher",
                        "date": on.strftime("%d %b %Y"),
                        "time": entry.starts_at.strftime("%H:%M"),
                        "class_name": getattr(classroom, "name", None) or "a class",
                        "school_name": await school_display_name(self.db, school_id),
                    },
                    explicit_user_ids=[teacher.user_id],
                    dedupe_key=f"substitute:{row.id}",
                    link_path="/teacher/timetable",
                )
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("substitute WhatsApp notification failed")

    async def cancel(self, user: CurrentUser, assignment_id: UUID) -> None:
        school_id = self._sid(user)
        row = await self.db.get(SubstituteAssignment, assignment_id)
        if row is None or row.school_id != school_id:
            raise NotFoundError("Cover not found")
        row.status = "CANCELLED"
        row.cancelled_at = datetime.now(timezone.utc)
        await write_audit(
            self.db,
            school_id=school_id,
            actor_id=user.id,
            action="substitute.cancelled",
            resource_type="substitute_assignment",
            resource_id=str(row.id),
            message="Substitute cover cancelled",
        )

    async def list_covers(self, user: CurrentUser, on: date) -> list[dict]:
        school_id = self._sid(user)
        names = await self._teacher_names(school_id)
        rows = (
            await self.db.execute(
                select(SubstituteAssignment).where(
                    SubstituteAssignment.school_id == school_id,
                    SubstituteAssignment.cover_date == on,
                    SubstituteAssignment.status == "CONFIRMED",
                )
            )
        ).scalars().all()
        return [
            {
                "id": str(r.id),
                "timetable_entry_id": str(r.timetable_entry_id),
                "date": r.cover_date.isoformat(),
                "absent": names.get(str(r.absent_teacher_id)),
                "substitute": names.get(str(r.substitute_teacher_id)),
                "note": r.note,
            }
            for r in rows
        ]

    # ── principal workload ───────────────────────────────────

    async def workload(self, user: CurrentUser, start: date, end: date) -> list[dict]:
        school_id = self._sid(user)
        names = await self._teacher_names(school_id)
        entries = (
            await self.db.execute(select(TimetableEntry).where(TimetableEntry.school_id == school_id))
        ).scalars().all()
        periods = defaultdict(int)
        classes = defaultdict(set)
        for e in entries:
            if e.teacher_id:
                periods[str(e.teacher_id)] += 1
                classes[str(e.teacher_id)].add(str(e.class_id))
        hw = {
            str(t): n
            for t, n in (
                await self.db.execute(
                    select(Homework.teacher_id, func.count()).where(
                        Homework.school_id == school_id,
                        Homework.status == "PUBLISHED",
                        func.date(Homework.assigned_at) >= start,
                        func.date(Homework.assigned_at) <= end,
                    ).group_by(Homework.teacher_id)
                )
            ).all()
        }
        pending = {
            str(t): n
            for t, n in (
                await self.db.execute(
                    select(Homework.teacher_id, func.count())
                    .select_from(HomeworkSubmission)
                    .join(Homework, Homework.id == HomeworkSubmission.homework_id)
                    .where(
                        HomeworkSubmission.school_id == school_id,
                        HomeworkSubmission.submitted_at.is_not(None),
                        HomeworkSubmission.marks.is_(None),
                        HomeworkSubmission.reviewed_at.is_(None),
                    )
                    .group_by(Homework.teacher_id)
                )
            ).all()
        }
        covers = {
            str(t): n
            for t, n in (
                await self.db.execute(
                    select(SubstituteAssignment.substitute_teacher_id, func.count()).where(
                        SubstituteAssignment.school_id == school_id,
                        SubstituteAssignment.status == "CONFIRMED",
                        SubstituteAssignment.cover_date >= start,
                        SubstituteAssignment.cover_date <= end,
                    ).group_by(SubstituteAssignment.substitute_teacher_id)
                )
            ).all()
        }
        out = [
            {
                "teacher_id": tid,
                "name": name,
                "periods_per_week": periods.get(tid, 0),
                "classes": len(classes.get(tid, ())),
                "homework_published": hw.get(tid, 0),
                "submissions_awaiting_marks": pending.get(tid, 0),
                "covers_given": covers.get(tid, 0),
            }
            for tid, name in names.items()
        ]
        out.sort(key=lambda r: (-r["periods_per_week"], r["name"]))
        return out

    # ── monthly principal report ─────────────────────────────

    async def monthly_report(self, user: CurrentUser, year: int, month: int) -> dict:
        school_id = self._sid(user)
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        rows = (
            await self.db.execute(
                select(Attendance.class_id, Attendance.status, func.count())
                .where(Attendance.school_id == school_id, Attendance.attended_on >= first, Attendance.attended_on <= last)
                .group_by(Attendance.class_id, Attendance.status)
            )
        ).all()
        per_class: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for cid, status, n in rows:
            per_class[str(cid)][status] += n
        week = func.date_trunc(literal_column("'week'"), Attendance.attended_on)  # literal: same expr in SELECT and GROUP BY
        weekly = (
            await self.db.execute(
                select(week.label("wk"), Attendance.status, func.count())
                .where(Attendance.school_id == school_id, Attendance.attended_on >= first, Attendance.attended_on <= last)
                .group_by(week, Attendance.status)
            )
        ).all()
        wk: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for w, status, n in weekly:
            wk[w.date().isoformat()][status] += n

        def rate(d: dict[str, int]) -> float | None:
            total = sum(d.values())
            if not total:
                return None
            present = d.get("PRESENT", 0) + d.get("LATE", 0) + d.get("HALF_DAY", 0) * 0.5
            return round(100 * present / total, 1)

        names = {
            str(i): n
            for i, n in (
                await self.db.execute(select(ClassRoom.id, ClassRoom.name).where(ClassRoom.school_id == school_id))
            ).all()
        }
        class_rows = [
            {"class_id": cid, "class_name": names.get(cid, "Class"), "attendance_rate": rate(d), "marked_days": sum(d.values())}
            for cid, d in per_class.items()
        ]
        class_rows.sort(key=lambda r: (r["attendance_rate"] is None, r["attendance_rate"] or 0))
        fees = (
            await self.db.execute(
                select(
                    func.coalesce(func.sum(FeeInvoice.total_amount), 0),
                    func.coalesce(func.sum(FeeInvoice.paid_amount), 0),
                    func.coalesce(func.sum(FeeInvoice.balance_amount), 0),
                    func.count(),
                ).where(
                    FeeInvoice.school_id == school_id,
                    FeeInvoice.invoice_date >= first,
                    FeeInvoice.invoice_date <= last,
                    FeeInvoice.status != "CANCELLED",
                )
            )
        ).one()
        billed, collected, balance, n_inv = (Decimal(fees[0]), Decimal(fees[1]), Decimal(fees[2]), int(fees[3]))
        overdue = (
            await self.db.execute(
                select(func.count(), func.coalesce(func.sum(FeeInvoice.balance_amount), 0)).where(
                    FeeInvoice.school_id == school_id,
                    FeeInvoice.balance_amount > 0,
                    FeeInvoice.due_date < last,
                    FeeInvoice.status != "CANCELLED",
                )
            )
        ).one()
        attention = [
            {"class_name": r["class_name"], "reason": f"Attendance {r['attendance_rate']}% (below 85%)"}
            for r in class_rows
            if r["attendance_rate"] is not None and r["attendance_rate"] < 85
        ]
        return {
            "month": f"{year:04d}-{month:02d}",
            "attendance": {
                "overall_rate": rate(
                    {k: sum(d.get(k, 0) for d in per_class.values()) for k in ("PRESENT", "ABSENT", "LATE", "EXCUSED", "HALF_DAY")}
                ),
                "weekly": [{"week_starting": w, "rate": rate(d)} for w, d in sorted(wk.items())],
                "classes": class_rows,
            },
            "fees": {
                "invoices_issued": n_inv,
                "billed": str(billed),
                "collected": str(collected),
                "outstanding": str(balance),
                "collection_rate": round(float(100 * collected / billed), 1) if billed else None,
                "overdue_invoices": int(overdue[0]),
                "overdue_amount": str(Decimal(overdue[1])),
            },
            "classes_needing_attention": attention,
        }

    def report_lines(self, school_name: str, rep: dict) -> list[str]:
        a, f = rep["attendance"], rep["fees"]
        lines = [f"{school_name} - Principal report {rep['month']}", ""]
        lines.append(f"Attendance this month: {a['overall_rate'] if a['overall_rate'] is not None else 'no data'}%")
        for w in a["weekly"]:
            lines.append(f"  Week of {w['week_starting']}: {w['rate'] if w['rate'] is not None else '-'}%")
        lines += ["", "Fees"]
        lines.append(f"  Invoices issued: {f['invoices_issued']}   Billed: {f['billed']}   Collected: {f['collected']}")
        lines.append(f"  Collection rate: {f['collection_rate'] if f['collection_rate'] is not None else '-'}%   Outstanding: {f['outstanding']}")
        lines.append(f"  Overdue invoices: {f['overdue_invoices']} ({f['overdue_amount']})")
        lines += ["", "Classes needing attention"]
        if rep["classes_needing_attention"]:
            lines += [f"  {c['class_name']}: {c['reason']}" for c in rep["classes_needing_attention"][:12]]
        else:
            lines.append("  None this month")
        return lines
