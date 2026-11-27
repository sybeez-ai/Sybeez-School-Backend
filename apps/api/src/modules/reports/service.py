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
    ExamSubject,
    FeeInvoice,
    Homework,
    HomeworkSubmission,
    OnlineTest,
    OnlineTestAttempt,
    Payment,
    ReportAuditLog,
    Result,
    SavedReport,
    Student,
    StudentMark,
    Subject,
    User,
)
from src.db.models.exam import Exam
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
from src.shared.utils import format_class_label
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

    async def history(self, school_id: UUID, limit: int = 50) -> list[dict]:
        import json

        rows = (
            await self.db.execute(
                select(ReportAuditLog)
                .where(ReportAuditLog.school_id == school_id)
                .order_by(ReportAuditLog.created_at.desc())
                .limit(min(max(limit, 1), 200))
            )
        ).scalars().all()
        out: list[dict] = []
        for row in rows:
            filters = None
            if row.filters_json:
                try:
                    filters = json.loads(row.filters_json)
                except json.JSONDecodeError:
                    filters = None
            out.append(
                {
                    "id": row.id,
                    "report_type": row.report_type,
                    "action": row.action,
                    "filters": filters,
                    "created_at": row.created_at,
                    "entity_id": row.entity_id,
                }
            )
        return out


    async def _student_name(self, student_id: UUID) -> str:
        st = await self.db.get(Student, student_id)
        if not st:
            return "Student"
        u = await self.db.get(User, st.user_id)
        return (u.full_name if u else None) or "Student"

    async def _class_name(self, class_id: UUID | None) -> str:
        if not class_id:
            return "—"
        c = await self.db.get(ClassRoom, class_id)
        if not c:
            return "—"
        return format_class_label(c.name, c.section)

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
        elif rt in ("academic_performance", "report_card"):
            columns = [
                "student",
                "class",
                "subject",
                "assessment",
                "date",
                "obtained",
                "max",
                "percent",
                "result",
            ]
            mq = (
                select(
                    StudentMark.student_id,
                    StudentMark.class_id,
                    ExamSubject.subject_id,
                    Exam.title,
                    ExamSubject.exam_date,
                    StudentMark.obtained_marks,
                    StudentMark.max_marks,
                    ExamSubject.pass_marks,
                )
                .join(ExamSubject, ExamSubject.id == StudentMark.exam_subject_id)
                .join(Exam, Exam.id == StudentMark.exam_id)
                .where(StudentMark.school_id == school_id, StudentMark.obtained_marks.is_not(None))
            )
            if data.section_id:
                mq = mq.where(StudentMark.class_id == data.section_id)
            if data.subject_id:
                mq = mq.where(ExamSubject.subject_id == data.subject_id)
            if data.from_date:
                mq = mq.where(ExamSubject.exam_date >= data.from_date)
            if data.to_date:
                mq = mq.where(ExamSubject.exam_date <= data.to_date)
            for sid, cid, subj_id, title, exam_date, obtained, max_marks, pass_marks in (
                await self.db.execute(mq.limit(5000))
            ).all():
                mx = float(max_marks or 0)
                pct = round(float(obtained) / mx * 100, 1) if mx else None
                passed = ""
                if pct is not None and pass_marks is not None:
                    passed = "Pass" if float(obtained) >= float(pass_marks) else "Fail"
                elif pct is not None:
                    passed = "Pass" if pct >= 50 else "Fail"
                subj = await self.db.get(Subject, subj_id) if subj_id else None
                rows.append(
                    {
                        "student": await self._student_name(sid),
                        "class": await self._class_name(cid),
                        "subject": subj.name if subj else "",
                        "assessment": title or "Exam",
                        "date": str(exam_date or ""),
                        "obtained": obtained,
                        "max": max_marks,
                        "percent": pct if pct is not None else "Not marked",
                        "result": passed or "Not marked",
                    }
                )
            oq = (
                select(
                    OnlineTestAttempt.student_id,
                    OnlineTestAttempt.class_id,
                    OnlineTest.subject_id,
                    OnlineTest.title,
                    OnlineTestAttempt.submitted_at,
                    OnlineTestAttempt.score,
                    OnlineTestAttempt.max_score,
                    OnlineTestAttempt.percentage,
                )
                .join(OnlineTest, OnlineTest.id == OnlineTestAttempt.test_id)
                .where(
                    OnlineTestAttempt.school_id == school_id,
                    OnlineTestAttempt.submitted_at.is_not(None),
                )
            )
            if data.section_id:
                oq = oq.where(OnlineTestAttempt.class_id == data.section_id)
            if data.subject_id:
                oq = oq.where(OnlineTest.subject_id == data.subject_id)
            for sid, cid, subj_id, title, submitted_at, score, max_score, percentage in (
                await self.db.execute(oq.limit(5000))
            ).all():
                if data.from_date and submitted_at and submitted_at.date() < data.from_date:
                    continue
                if data.to_date and submitted_at and submitted_at.date() > data.to_date:
                    continue
                pct = float(percentage) if percentage is not None else None
                if pct is None and score is not None and max_score:
                    pct = round(float(score) / float(max_score) * 100, 1)
                subj = await self.db.get(Subject, subj_id) if subj_id else None
                rows.append(
                    {
                        "student": await self._student_name(sid),
                        "class": await self._class_name(cid),
                        "subject": subj.name if subj else "",
                        "assessment": title or "Online test",
                        "date": str(submitted_at.date() if submitted_at else ""),
                        "obtained": score,
                        "max": max_score,
                        "percent": pct if pct is not None else "Not marked",
                        "result": ("Pass" if pct >= 50 else "Fail") if pct is not None else "Not marked",
                    }
                )
            if rt == "report_card":
                # Collapse to one row per student + subject (average) plus overall attendance.
                from collections import defaultdict
                from src.modules.analytics.insights import InsightsService
                from src.modules.analytics.schemas import AnalyticsFilters

                grouped: dict[tuple, list] = defaultdict(list)
                for row in rows:
                    grouped[(row["student"], row["class"], row["subject"])].append(row)
                collapsed = []
                for (student, class_name, subject), items in grouped.items():
                    pcts = [i["percent"] for i in items if isinstance(i["percent"], (int, float))]
                    avg = round(sum(pcts) / len(pcts), 1) if pcts else "Not marked"
                    collapsed.append(
                        {
                            "student": student,
                            "class": class_name,
                            "subject": subject,
                            "assessment": "Average",
                            "date": "",
                            "obtained": "",
                            "max": "",
                            "percent": avg,
                            "result": (
                                "Pass" if isinstance(avg, float) and avg >= 50 else "Fail"
                                if isinstance(avg, float)
                                else "Not marked"
                            ),
                        }
                    )
                rows = collapsed
                columns = ["student", "class", "subject", "percent", "result"]
                rows = [{k: r.get(k, "") for k in columns} for r in rows]

        elif rt == "class_summary":
            from src.modules.analytics.insights import InsightsService
            from src.modules.analytics.schemas import AnalyticsFilters

            columns = [
                "class",
                "students",
                "attendance_rate",
                "average_score",
                "at_risk",
            ]
            prog = await InsightsService(self.db).progress(
                school_id,
                AnalyticsFilters(
                    academic_year_id=data.academic_year_id,
                    grade_id=data.grade_id,
                    section_id=data.section_id,
                    subject_id=data.subject_id,
                    from_date=data.from_date,
                    to_date=data.to_date,
                ),
            )
            for c in prog.classes:
                rows.append(
                    {
                        "class": c.class_name,
                        "students": c.students,
                        "attendance_rate": c.attendance_rate if c.attendance_rate is not None else "Not marked",
                        "average_score": c.average_score if c.average_score is not None else "Not marked",
                        "at_risk": c.at_risk_count,
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
