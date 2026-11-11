"""Read-only tool queries. Every statement is built from a ToolQuery, which already pinned the school."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Announcement,
    AnnouncementAudience,
    Attendance,
    ClassRoom,
    Employee,
    FeeInvoice,
    FeeReceipt,
    Homework,
    Payment,
    PayrollPayslip,
    PayrollRun,
    Result,
    StudentFeeAccount,
    Subject,
    TimetableEntry,
)
from src.db.models.daily_lesson import DailyLesson
from src.db.models.exam import Exam
from src.modules.whatsapp_assistant.scope import ToolQuery

_VISIBLE_PAY = ("PUBLISHED", "PAID")


def attendance_statement(query: ToolQuery):
    stmt = select(
        Attendance.attended_on,
        Attendance.status,
        Attendance.student_id,
    ).where(Attendance.school_id == query.school_id)
    if query.student_ids is not None:
        stmt = stmt.where(Attendance.student_id.in_(query.student_ids))
    if query.class_ids is not None:
        stmt = stmt.where(Attendance.class_id.in_(query.class_ids))
    return stmt.order_by(Attendance.attended_on.desc()).limit(15)


def homework_statement(query: ToolQuery):
    stmt = select(Homework.title, Homework.due_at, ClassRoom.name.label("class_name")).join(
        ClassRoom, ClassRoom.id == Homework.class_id
    )
    stmt = stmt.where(Homework.school_id == query.school_id, Homework.status == "PUBLISHED")
    if query.class_ids is not None:
        stmt = stmt.where(Homework.class_id.in_(query.class_ids))
    return stmt.order_by(Homework.due_at.desc().nullslast()).limit(10)


def timetable_statement(query: ToolQuery):
    stmt = (
        select(
            TimetableEntry.day_of_week,
            TimetableEntry.starts_at,
            TimetableEntry.ends_at,
            ClassRoom.name.label("class_name"),
            Subject.name.label("subject_name"),
        )
        .join(ClassRoom, ClassRoom.id == TimetableEntry.class_id)
        .join(Subject, Subject.id == TimetableEntry.subject_id)
    )
    stmt = stmt.where(TimetableEntry.school_id == query.school_id)
    if query.class_ids is not None:
        stmt = stmt.where(TimetableEntry.class_id.in_(query.class_ids))
    return stmt.order_by(TimetableEntry.day_of_week, TimetableEntry.starts_at).limit(40)


def results_statement(query: ToolQuery):
    stmt = select(Exam.title, Result.score, Result.max_score, Result.grade_label, Result.is_published).join(
        Exam, Exam.id == Result.exam_id
    )
    stmt = stmt.where(Result.school_id == query.school_id)
    if query.published_only:
        stmt = stmt.where(Result.is_published.is_(True))
    if query.student_ids is not None:
        stmt = stmt.where(Result.student_id.in_(query.student_ids))
    if query.class_ids is not None:
        stmt = stmt.where(Result.class_id.in_(query.class_ids))
    return stmt.order_by(Result.published_at.desc().nullslast()).limit(10)


def fees_due_statement(query: ToolQuery):
    stmt = select(FeeInvoice.invoice_number, FeeInvoice.due_date, FeeInvoice.balance_amount, FeeInvoice.currency).join(
        StudentFeeAccount, StudentFeeAccount.id == FeeInvoice.student_fee_account_id
    )
    stmt = stmt.where(FeeInvoice.school_id == query.school_id, FeeInvoice.balance_amount > 0)
    if query.student_ids is not None:
        stmt = stmt.where(StudentFeeAccount.student_id.in_(query.student_ids))
    return stmt.order_by(FeeInvoice.due_date).limit(10)


def fee_receipts_statement(query: ToolQuery):
    stmt = (
        select(FeeReceipt.receipt_number, FeeReceipt.issued_at, Payment.amount)
        .join(Payment, Payment.id == FeeReceipt.payment_id)
        .join(StudentFeeAccount, StudentFeeAccount.id == Payment.student_fee_account_id)
        .where(FeeReceipt.school_id == query.school_id, FeeReceipt.status == "ACTIVE")
    )
    if query.student_ids is not None:
        stmt = stmt.where(StudentFeeAccount.student_id.in_(query.student_ids))
    return stmt.order_by(FeeReceipt.issued_at.desc()).limit(10)


def announcements_statement(query: ToolQuery):
    stmt = select(Announcement.title, Announcement.body).where(
        Announcement.school_id == query.school_id,
        Announcement.status == "PUBLISHED",
    )
    if query.class_ids is not None:
        stmt = stmt.where(or_(Announcement.class_id.is_(None), Announcement.class_id.in_(query.class_ids)))
        section_match = AnnouncementAudience.section_id.in_(query.class_ids)
        audience_match = exists(
            select(AnnouncementAudience.id).where(
                AnnouncementAudience.announcement_id == Announcement.id,
                AnnouncementAudience.school_id == query.school_id,
                or_(
                    AnnouncementAudience.audience_type == "SCHOOL",
                    AnnouncementAudience.role == query.role,
                    AnnouncementAudience.user_id == query.user_id,
                    section_match,
                ),
            )
        )
        no_audience = ~exists(
            select(AnnouncementAudience.id).where(AnnouncementAudience.announcement_id == Announcement.id)
        )
        stmt = stmt.where(or_(no_audience, audience_match))
    return stmt.order_by(Announcement.published_at.desc().nullslast()).limit(8)


def lessons_statement(query: ToolQuery):
    stmt = select(DailyLesson.taught_on, DailyLesson.chapter, DailyLesson.concept, DailyLesson.taught_text).where(
        DailyLesson.school_id == query.school_id
    )
    if query.class_ids is not None:
        stmt = stmt.where(DailyLesson.class_id.in_(query.class_ids))
    return stmt.order_by(DailyLesson.taught_on.desc()).limit(5)


def payslip_statement(query: ToolQuery):
    stmt = (
        select(PayrollRun.label, PayrollPayslip.net_amount, PayrollPayslip.status, PayrollPayslip.paid_on)
        .join(PayrollRun, PayrollRun.id == PayrollPayslip.payroll_run_id)
        .where(PayrollPayslip.school_id == query.school_id, PayrollPayslip.status.in_(_VISIBLE_PAY))
    )
    if query.own_only:
        owned = []
        if query.employee_id is not None:
            owned.append(PayrollPayslip.employee_id == query.employee_id)
        if query.teacher_id is not None:
            owned.append(PayrollPayslip.teacher_id == query.teacher_id)
        if not owned:
            stmt = stmt.where(PayrollPayslip.id.is_(None))
        else:
            stmt = stmt.where(or_(*owned))
    elif query.search_name:
        stmt = stmt.join(Employee, Employee.id == PayrollPayslip.employee_id).where(
            Employee.school_id == query.school_id,
            Employee.full_name.ilike(_like(query.search_name)),
        )
    return stmt.order_by(PayrollPayslip.created_at.desc()).limit(5)


def _like(name: str) -> str:
    cleaned = name.replace("%", "").replace("_", "").strip()
    return f"%{cleaned}%"


def _lines(rows: list, formatter) -> str:
    if not rows:
        return "I couldn't find that on your account."
    return "\n".join(formatter(row) for row in rows)


def _money(amount) -> str:
    if isinstance(amount, Decimal):
        return f"{amount:.2f}"
    return str(amount)


async def run_tool(db: AsyncSession, query: ToolQuery, arguments: dict | None = None) -> dict:
    if query.denied:
        return {"text": query.denied}
    arguments = arguments or {}
    if query.tool == "attendance":
        rows = (await db.execute(attendance_statement(query))).all()
        return {"text": _lines(rows, lambda row: f"{row.attended_on}: {row.status}")}
    if query.tool == "homework":
        rows = (await db.execute(homework_statement(query))).all()
        return {
            "text": _lines(
                rows,
                lambda row: f"{row.title} ({row.class_name})" + (f", due {row.due_at:%d %b}" if row.due_at else ""),
            )
        }
    if query.tool == "timetable":
        rows = (await db.execute(timetable_statement(query))).all()
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return {
            "text": _lines(
                rows,
                lambda row: f"{days[row.day_of_week] if 0 <= row.day_of_week <= 6 else row.day_of_week} {row.starts_at:%H:%M} {row.subject_name} — {row.class_name}",
            )
        }
    if query.tool == "results":
        rows = (await db.execute(results_statement(query))).all()
        return {"text": _lines(rows, lambda row: f"{row.title}: {row.score}/{row.max_score}" + (f" ({row.grade_label})" if row.grade_label else ""))}
    if query.tool == "fees_due":
        rows = (await db.execute(fees_due_statement(query))).all()
        return {"text": _lines(rows, lambda row: f"{row.invoice_number}: {_money(row.balance_amount)} {row.currency} due {row.due_date}")}
    if query.tool == "fee_receipts":
        rows = (await db.execute(fee_receipts_statement(query))).all()
        return {"text": _lines(rows, lambda row: f"{row.receipt_number}: {_money(row.amount)} on {row.issued_at:%d %b %Y}")}
    if query.tool == "announcements":
        rows = (await db.execute(announcements_statement(query))).all()
        return {"text": _lines(rows, lambda row: f"{row.title}: {row.body[:180]}")}
    if query.tool == "lesson_material":
        rows = (await db.execute(lessons_statement(query))).all()
        if not rows:
            return {"text": "I don't have teacher-uploaded lesson material for this class section yet."}
        parts = []
        for row in rows:
            title = row.chapter or row.concept or str(row.taught_on)
            parts.append(f"{title} ({row.taught_on}): {(row.taught_text or '')[:400]}")
        return {"text": "\n".join(parts)}
    if query.tool == "payslips":
        rows = (await db.execute(payslip_statement(query))).all()
        return {"text": _lines(rows, lambda row: f"{row.label}: net {_money(row.net_amount)} ({row.status})")}
    if query.tool == "leave_balance":
        return {"text": await _leave_balance(db, query)}
    if query.tool == "leave_request":
        return {"text": await _leave_request(db, query, arguments)}
    return {"text": "That action is not available."}


async def _leave_balance(db: AsyncSession, query: ToolQuery) -> str:
    from src.modules.hr_leave_policy.service import LeavePolicyService

    user_id = query.user_id
    if not query.own_only and query.search_name:
        rows = (
            await db.execute(
                select(Employee).where(
                    Employee.school_id == query.school_id,
                    Employee.full_name.ilike(_like(query.search_name)),
                ).limit(5)
            )
        ).scalars().all()
        if len(rows) != 1 or rows[0].user_id is None:
            return "I need the full name of one staff member in your school before I can check that leave balance."
        user_id = rows[0].user_id
    balances = await LeavePolicyService(db).balances(query.school_id, user_id, date.today().year)
    if not balances:
        return "I couldn't find a leave balance on this account."
    lines = []
    for item in balances:
        available = "no limit" if item.available is None else str(item.available)
        lines.append(f"{item.name}: available {available}, used {item.used}, pending {item.pending}")
    return "\n".join(lines)


async def _leave_request(db: AsyncSession, query: ToolQuery, arguments: dict) -> str:
    from src.modules.hr.schemas import StaffLeaveRequestCreate
    from src.modules.hr.service import HRService

    start = arguments.get("start_date")
    end = arguments.get("end_date")
    reason = str(arguments.get("reason") or "").strip()
    if not start or not end or len(reason) < 3:
        return "To request leave, send the start date, end date (YYYY-MM-DD), and a short reason."
    try:
        payload = StaffLeaveRequestCreate(
            start_date=date.fromisoformat(str(start)),
            end_date=date.fromisoformat(str(end)),
            reason=reason,
            leave_type_id=uuid.UUID(str(arguments["leave_type_id"])) if arguments.get("leave_type_id") else None,
        )
    except ValueError:
        return "I couldn't read those leave dates. Use YYYY-MM-DD."
    from src.core.exceptions import AppError

    try:
        created = await HRService(db).create_staff_leave(query.school_id, query.user_id, payload)
    except AppError as exc:
        return exc.message
    return f"Leave request submitted for {created.start_date} to {created.end_date}. It is waiting for approval."
