"""Tabular report generation from existing modules."""

from __future__ import annotations

import json
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.db.models import (
    Attendance,
    Enrollment,
    FeeInvoice,
    Homework,
    HomeworkSubmission,
    Payment,
    ReportAuditLog,
    Result,
    SavedReport,
    Student,
    User,
)
from src.db.models.class_ import ClassRoom
from src.db.models.fee import StudentFeeAccount
from src.modules.reports.exporters import rows_to_csv, rows_to_pdf
from src.modules.reports.schemas import (
    REPORT_CATALOG,
    ReportGenerateIn,
    ReportResultOut,
    SavedReportCreate,
    SavedReportOut,
    SavedReportUpdate,
)
from src.shared.enums import (
    AttendanceStatus,
    EnrollmentStatus,
    FeeInvoiceStatus,
    HomeworkStatus,
    PaymentStatus,
)


class ReportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _audit(
        self,
        school_id: UUID,
        user_id: UUID | None,
        report_type: str,
        action: str,
        filters: dict | None = None,
        entity_id: UUID | None = None,
    ) -> None:
        self.db.add(
            ReportAuditLog(
                id=uuid4(),
                school_id=school_id,
                user_id=user_id,
                report_type=report_type,
                filters_json=json.dumps(filters or {}, default=str),
                action=action,
                entity_id=entity_id,
            )
        )

    async def catalog(self) -> list[dict]:
        return REPORT_CATALOG

    async def _student_name(self, student_id: UUID) -> str:
        st = await self.db.get(Student, student_id)
        if not st:
            return "Student"
        u = await self.db.get(User, st.user_id)
        return u.name if u else "Student"

    async def _class_name(self, class_id: UUID | None) -> str:
        if not class_id:
            return "—"
        c = await self.db.get(ClassRoom, class_id)
        if not c:
            return "—"
        return f"{c.name}{('-' + c.section) if c.section else ''}"

    async def generate(
        self, school_id: UUID, user_id: UUID, data: ReportGenerateIn
    ) -> ReportResultOut:
        rt = data.report_type
        rows: list[dict] = []
        columns: list[str] = []
        title = next((c["name"] for c in REPORT_CATALOG if c["id"] == rt), rt)

        if rt in ("student_list", "enrollment", "class_report"):
            columns = ["student", "class", "status", "roll_number"]
            q = select(Enrollment).where(Enrollment.school_id == school_id)
            if data.academic_year_id:
                q = q.where(Enrollment.academic_year_id == data.academic_year_id)
            if data.grade_id:
                q = q.where(Enrollment.grade_id == data.grade_id)
            if data.section_id:
                q = q.where(Enrollment.class_id == data.section_id)
            if rt != "enrollment":
                q = q.where(Enrollment.status == EnrollmentStatus.ACTIVE.value)
            for enr in (await self.db.execute(q)).scalars().all():
                rows.append(
                    {
                        "student": await self._student_name(enr.student_id),
                        "class": await self._class_name(enr.class_id),
                        "status": enr.status,
                        "roll_number": enr.roll_number or "",
                    }
                )

        elif rt in ("attendance", "absence", "late"):
            columns = ["student", "class", "date", "status"]
            q = select(Attendance).where(Attendance.school_id == school_id)
            if data.from_date:
                q = q.where(Attendance.attended_on >= data.from_date)
            if data.to_date:
                q = q.where(Attendance.attended_on <= data.to_date)
            if data.section_id:
                q = q.where(Attendance.class_id == data.section_id)
            if rt == "absence":
                q = q.where(Attendance.status == AttendanceStatus.ABSENT.value)
            elif rt == "late":
                q = q.where(Attendance.status == AttendanceStatus.LATE.value)
            for a in (await self.db.execute(q.order_by(Attendance.attended_on.desc()).limit(5000))).scalars().all():
                rows.append(
                    {
                        "student": await self._student_name(a.student_id),
                        "class": await self._class_name(a.class_id),
                        "date": str(a.attended_on),
                        "status": a.status,
                    }
                )

        elif rt in ("homework_submission", "homework_missing"):
            columns = ["homework", "student", "status", "submitted_at"]
            hq = select(Homework).where(
                Homework.school_id == school_id,
                Homework.status != HomeworkStatus.DRAFT.value,
            )
            if data.section_id:
                hq = hq.where(Homework.class_id == data.section_id)
            if data.subject_id:
                hq = hq.where(Homework.subject_id == data.subject_id)
            homeworks = (await self.db.execute(hq.limit(200))).scalars().all()
            for h in homeworks:
                subs = (
                    await self.db.execute(
                        select(HomeworkSubmission).where(HomeworkSubmission.homework_id == h.id)
                    )
                ).scalars().all()
                if rt == "homework_submission":
                    for s in subs:
                        rows.append(
                            {
                                "homework": h.title,
                                "student": await self._student_name(s.student_id),
                                "status": s.status,
                                "submitted_at": str(s.submitted_at or ""),
                            }
                        )
                else:
                    submitted_ids = {s.student_id for s in subs}
                    enrs = (
                        await self.db.execute(
                            select(Enrollment).where(
                                Enrollment.class_id == h.class_id,
                                Enrollment.status == EnrollmentStatus.ACTIVE.value,
                            )
                        )
                    ).scalars().all()
                    for enr in enrs:
                        if enr.student_id not in submitted_ids:
                            rows.append(
                                {
                                    "homework": h.title,
                                    "student": await self._student_name(enr.student_id),
                                    "status": "MISSING",
                                    "submitted_at": "",
                                }
                            )

        elif rt in ("exam_marks", "exam_results"):
            columns = ["student", "exam", "percentage", "pass_fail", "grade"]
            q = select(Result).where(Result.school_id == school_id, Result.is_published.is_(True))
            if data.exam_id:
                q = q.where(Result.exam_id == data.exam_id)
            if data.section_id:
                q = q.where(Result.class_id == data.section_id)
            for r in (await self.db.execute(q.limit(5000))).scalars().all():
                from src.db.models import Exam

                exam = await self.db.get(Exam, r.exam_id)
                rows.append(
                    {
                        "student": await self._student_name(r.student_id),
                        "exam": exam.title if exam else "",
                        "percentage": r.percentage if r.percentage is not None else "",
                        "pass_fail": r.pass_fail or "",
                        "grade": r.grade_label or "",
                    }
                )

        elif rt in ("fee_collection", "fee_outstanding", "fee_overdue"):
            if rt == "fee_collection":
                columns = ["payment_number", "amount", "method", "status", "paid_at"]
                q = select(Payment).where(
                    Payment.school_id == school_id,
                    Payment.status == PaymentStatus.SUCCESS.value,
                )
                if data.from_date:
                    q = q.where(Payment.paid_at >= data.from_date)
                for p in (await self.db.execute(q.limit(5000))).scalars().all():
                    rows.append(
                        {
                            "payment_number": p.payment_number,
                            "amount": str(p.amount),
                            "method": p.method,
                            "status": p.status,
                            "paid_at": str(p.paid_at or ""),
                        }
                    )
            else:
                columns = ["student", "invoice", "due_date", "balance", "status"]
                q = select(FeeInvoice).where(
                    FeeInvoice.school_id == school_id,
                    FeeInvoice.balance_amount > 0,
                    FeeInvoice.status != FeeInvoiceStatus.CANCELLED.value,
                )
                if rt == "fee_overdue":
                    q = q.where(FeeInvoice.due_date < date.today())
                for inv in (await self.db.execute(q.limit(5000))).scalars().all():
                    acc = await self.db.get(StudentFeeAccount, inv.student_fee_account_id)
                    rows.append(
                        {
                            "student": await self._student_name(acc.student_id) if acc else "",
                            "invoice": inv.invoice_number,
                            "due_date": str(inv.due_date),
                            "balance": str(inv.balance_amount),
                            "status": inv.status,
                        }
                    )
        else:
            raise NotFoundError(f"Unknown report type: {rt}")

        if data.fields:
            columns = [c for c in columns if c in data.fields] or columns
            rows = [{k: r.get(k, "") for k in columns} for r in rows]

        await self._audit(
            school_id,
            user_id,
            rt,
            "REPORT_GENERATED",
            filters=data.model_dump(mode="json"),
        )
        await self.db.commit()
        return ReportResultOut(
            report_type=rt,
            title=title,
            columns=columns,
            rows=rows,
            total_rows=len(rows),
        )

    async def export_csv(
        self, school_id: UUID, user_id: UUID, data: ReportGenerateIn
    ) -> tuple[str, str]:
        result = await self.generate(school_id, user_id, data)
        await self._audit(school_id, user_id, data.report_type, "REPORT_EXPORTED", {"format": "csv"})
        await self.db.commit()
        return f"{data.report_type}.csv", rows_to_csv(result.rows, result.columns)

    async def export_pdf(
        self, school_id: UUID, user_id: UUID, data: ReportGenerateIn
    ) -> tuple[str, bytes]:
        result = await self.generate(school_id, user_id, data)
        await self._audit(school_id, user_id, data.report_type, "REPORT_EXPORTED", {"format": "pdf"})
        await self.db.commit()
        return f"{data.report_type}.pdf", rows_to_pdf(result.title, result.rows, result.columns)

    async def list_saved(self, school_id: UUID) -> list[SavedReportOut]:
        rows = (
            await self.db.execute(
                select(SavedReport)
                .where(SavedReport.school_id == school_id)
                .order_by(SavedReport.created_at.desc())
            )
        ).scalars().all()
        return [SavedReportOut.model_validate(r) for r in rows]

    async def create_saved(
        self, school_id: UUID, user_id: UUID, data: SavedReportCreate
    ) -> SavedReportOut:
        row = SavedReport(
            id=uuid4(),
            school_id=school_id,
            created_by=user_id,
            name=data.name.strip(),
            report_type=data.report_type,
            configuration_json=json.dumps(data.configuration),
        )
        self.db.add(row)
        await self._audit(school_id, user_id, data.report_type, "REPORT_SAVED", entity_id=row.id)
        await self.db.commit()
        await self.db.refresh(row)
        return SavedReportOut.model_validate(row)

    async def update_saved(
        self, school_id: UUID, report_id: UUID, data: SavedReportUpdate
    ) -> SavedReportOut:
        row = await self.db.get(SavedReport, report_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Saved report not found")
        if data.name is not None:
            row.name = data.name.strip()
        if data.configuration is not None:
            row.configuration_json = json.dumps(data.configuration)
        await self.db.commit()
        await self.db.refresh(row)
        return SavedReportOut.model_validate(row)

    async def delete_saved(self, school_id: UUID, user_id: UUID, report_id: UUID) -> None:
        row = await self.db.get(SavedReport, report_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Saved report not found")
        await self._audit(school_id, user_id, row.report_type, "REPORT_DELETED", entity_id=row.id)
        await self.db.delete(row)
        await self.db.commit()
