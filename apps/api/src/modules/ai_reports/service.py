"""AI report generation service.

Gathers student context from Core DB, calls the AI service /reports/weekly endpoint
(via httpx) to produce a structured summary, then persists the report and notifies the parent.
Falls back to a template summary if the AI service is unavailable.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from uuid import UUID

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import Parent, Student, StudentParent, User
from src.modules.ai_reports.repository import AiReportsRepository
from src.modules.ai_reports.schemas import AiReportOut, GenerateReportRequest

logger = logging.getLogger(__name__)


class AiReportsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = AiReportsRepository(db)

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def generate(
        self,
        *,
        school_id: UUID,
        parent_user_id: UUID,
        data: GenerateReportRequest,
        bearer_token: str | None = None,
    ) -> AiReportOut:
        # 1. Resolve parent → confirm link to requested student
        student_id = data.student_id
        await self._assert_parent_owns_student(school_id, parent_user_id, student_id)

        # 2. Determine period
        period_end = data.period_end or date.today()
        period_start = data.period_start or (period_end - timedelta(days=7))

        # 3. Gather student context
        ctx = await self._gather_context(school_id, student_id, period_start, period_end)

        # 4. Call AI service (or use template fallback)
        ai_result = await self._call_ai(ctx, bearer_token=bearer_token)

        # 5. Persist
        student_name = ctx.get("student_name", "Your child")
        title = f"Weekly Report · {student_name} · {period_start.strftime('%d %b')}–{period_end.strftime('%d %b %Y')}"
        report = await self.repo.create(
            school_id=school_id,
            student_id=student_id,
            parent_user_id=parent_user_id,
            period_start=period_start,
            period_end=period_end,
            title=title,
            summary_json=ai_result.get("summary", {}),
            body_markdown=ai_result.get("body_markdown", ""),
            status="READY",
        )

        # 6. Notify parent (best-effort)
        try:
            await self._notify_parent(school_id, parent_user_id, report.title)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to notify parent for AI report %s", report.id)

        return report

    async def list_for_parent(
        self, *, school_id: UUID, parent_user_id: UUID
    ) -> list[AiReportOut]:
        return await self.repo.list_for_parent(
            school_id=school_id, parent_user_id=parent_user_id
        )

    async def get(
        self, *, school_id: UUID, report_id: UUID, parent_user_id: UUID
    ) -> AiReportOut:
        report = await self.repo.get(
            school_id=school_id, report_id=report_id, parent_user_id=parent_user_id
        )
        if not report:
            raise NotFoundError("Report not found")
        return report

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _assert_parent_owns_student(
        self, school_id: UUID, parent_user_id: UUID, student_id: UUID
    ) -> None:
        parent = (
            await self.db.execute(
                select(Parent).where(
                    Parent.school_id == school_id, Parent.user_id == parent_user_id
                )
            )
        ).scalar_one_or_none()
        if not parent:
            raise ForbiddenError("Parent profile required")

        link = (
            await self.db.execute(
                select(StudentParent).where(
                    StudentParent.school_id == school_id,
                    StudentParent.parent_id == parent.id,
                    StudentParent.student_id == student_id,
                )
            )
        ).scalar_one_or_none()
        if not link:
            raise ForbiddenError("Not linked to this student")

    async def _gather_context(
        self,
        school_id: UUID,
        student_id: UUID,
        period_start: date,
        period_end: date,
    ) -> dict:
        """Gather attendance, homework, and exam data for the period."""
        # Student name
        student = await self.db.get(Student, student_id)
        student_name = "Student"
        if student:
            student_user = await self.db.get(User, student.user_id)
            student_name = student_user.full_name if student_user else "Student"

        # Attendance summary
        att_rows = (
            await self.db.execute(
                text(
                    """
                    SELECT status, COUNT(*) as cnt
                    FROM attendance
                    WHERE school_id = :school_id
                      AND student_id = :student_id
                      AND attended_on BETWEEN :start AND :end
                    GROUP BY status
                    """
                ),
                {
                    "school_id": str(school_id),
                    "student_id": str(student_id),
                    "start": period_start,
                    "end": period_end,
                },
            )
        ).mappings().all()
        attendance: dict[str, int] = {r["status"]: r["cnt"] for r in att_rows}

        # Homework completion
        hw_rows = (
            await self.db.execute(
                text(
                    """
                    SELECT h.title, hs.status
                    FROM homework h
                    LEFT JOIN homework_submissions hs ON hs.homework_id = h.id
                                                     AND hs.student_id = :student_id
                    WHERE h.school_id = :school_id
                      AND h.due_at::date BETWEEN :start AND :end
                      AND h.class_id IN (
                          SELECT e.class_id FROM enrollments e
                          WHERE e.student_id = :student_id AND e.status = 'ACTIVE'
                      )
                    ORDER BY h.due_at DESC
                    LIMIT 10
                    """
                ),
                {
                    "school_id": str(school_id),
                    "student_id": str(student_id),
                    "start": period_start,
                    "end": period_end,
                },
            )
        ).mappings().all()
        homework = [dict(r) for r in hw_rows]

        # Exam results (StudentMark.obtained_marks + ExamSubject.exam_date / Exam.starts_on)
        result_rows = (
            await self.db.execute(
                text(
                    """
                    SELECT
                        sub.name AS subject,
                        sm.obtained_marks AS marks_obtained,
                        sm.max_marks,
                        COALESCE(es.exam_date, e.starts_on) AS exam_date
                    FROM student_marks sm
                    JOIN exam_subjects es ON es.id = sm.exam_subject_id
                    JOIN subjects sub ON sub.id = es.subject_id
                    JOIN exams e ON e.id = es.exam_id
                    WHERE e.school_id = :school_id
                      AND sm.student_id = :student_id
                      AND sm.obtained_marks IS NOT NULL
                      AND COALESCE(es.exam_date, e.starts_on, e.ends_on, CURRENT_DATE)
                          BETWEEN :start AND :end
                    ORDER BY COALESCE(es.exam_date, e.starts_on) DESC NULLS LAST
                    LIMIT 10
                    """
                ),
                {
                    "school_id": str(school_id),
                    "student_id": str(student_id),
                    "start": period_start,
                    "end": period_end,
                },
            )
        ).mappings().all()
        results = [dict(r) for r in result_rows]

        # Recent practice attempts (join via student.user_id)
        practice_rows = (
            await self.db.execute(
                text(
                    """
                    SELECT pa.topic, pa.difficulty, pa.score_pct, pa.created_at
                    FROM practice_attempts pa
                    JOIN students st ON st.user_id = pa.student_user_id
                    WHERE st.id = :student_id
                      AND (pa.school_id = :school_id OR pa.school_id IS NULL)
                      AND pa.created_at::date BETWEEN :start AND :end
                    ORDER BY pa.created_at DESC
                    LIMIT 10
                    """
                ),
                {
                    "school_id": str(school_id),
                    "student_id": str(student_id),
                    "start": period_start,
                    "end": period_end,
                },
            )
        ).mappings().all()
        practice = [
            {
                "topic": r["topic"],
                "difficulty": r["difficulty"],
                "score_pct": float(r["score_pct"]) if r["score_pct"] is not None else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in practice_rows
        ]

        return {
            "student_name": student_name,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "attendance": attendance,
            "homework": homework,
            "exam_results": results,
            "practice_attempts": practice,
        }

    async def _call_ai(
        self, ctx: dict, *, bearer_token: str | None = None
    ) -> dict:
        """POST to AI service /reports/weekly. Returns dict with summary + body_markdown."""
        settings = get_settings()
        url = f"{settings.ai_service_url.rstrip('/')}/api/v1/ai/reports/weekly"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                resp = await client.post(url, headers=headers, content=json.dumps(ctx))
            if resp.status_code == 200:
                return resp.json()
        except httpx.HTTPError as exc:
            logger.warning("AI service unreachable for report generation: %s", exc)

        # Fallback: template summary
        return _template_summary(ctx)

    async def _notify_parent(
        self, school_id: UUID, parent_user_id: UUID, title: str
    ) -> None:
        await self.db.execute(
            text(
                """
                INSERT INTO notifications
                    (id, school_id, user_id, title, body, type, priority, read, created_at)
                VALUES
                    (gen_random_uuid(), :school_id, :user_id, :title, :body,
                     'AI_REPORT', 'normal', false, NOW())
                """
            ),
            {
                "school_id": str(school_id),
                "user_id": str(parent_user_id),
                "title": "New AI Report Ready",
                "body": title,
            },
        )
        await self.db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Template fallback
# ──────────────────────────────────────────────────────────────────────────────

def _template_summary(ctx: dict) -> dict:
    name = ctx.get("student_name", "Your child")
    attendance = ctx.get("attendance", {})
    present = attendance.get("PRESENT", 0)
    absent = attendance.get("ABSENT", 0)
    homework = ctx.get("homework", [])
    submitted = sum(1 for h in homework if h.get("status") in ("SUBMITTED", "GRADED"))
    results = ctx.get("exam_results", [])

    lines: list[str] = [f"## Weekly Summary for {name}\n"]
    lines.append(
        f"**Attendance:** {present} days present, {absent} days absent during this period.\n"
    )
    if homework:
        lines.append(
            f"**Homework:** {submitted} of {len(homework)} assignments submitted on time.\n"
        )
    if results:
        subject_scores = ", ".join(
            f"{r['subject']} {r.get('marks_obtained')}/{r.get('max_marks')}"
            for r in results[:5]
            if r.get("marks_obtained") is not None
        )
        if subject_scores:
            lines.append(f"**Exam Results:** {subject_scores}.\n")

    practice = ctx.get("practice_attempts", [])
    if practice:
        avg_practice = [
            float(p["score_pct"])
            for p in practice
            if p.get("score_pct") is not None
        ]
        if avg_practice:
            lines.append(
                f"**AI Practice:** {len(practice)} sessions · avg score "
                f"{round(sum(avg_practice) / len(avg_practice), 1)}%.\n"
            )

    lines.append(
        "\n### Focus Areas\nReview subjects where scores were below 60% and encourage daily practice.\n"
    )
    lines.append(
        "\n### Encouragement\nKeep supporting consistent homework habits and a regular study routine.\n"
    )

    summary = {
        "attendance_present": present,
        "attendance_absent": absent,
        "homework_submitted": submitted,
        "homework_total": len(homework),
        "focus": "Review low-scoring subjects",
        "strengths": "Consistent attendance",
        "encouragement": "Great effort this week!",
    }
    return {"summary": summary, "body_markdown": "".join(lines)}
