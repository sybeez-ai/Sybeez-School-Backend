"""Employee self-service HR requests: submit, review, decide, comment, with an append-only history."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, NotFoundError
from src.db.models import HrRequest, HrRequestEvent
from src.db.models.hr import HR_REQUEST_DECIDED_STATUSES, HR_REQUEST_OPEN_STATUSES
from src.modules.employees.lookup import (
    employee_briefs,
    employee_for_user,
    get_employee_brief,
    user_names,
)
from src.modules.employees.service import EmployeeService
from src.modules.hr_notifications.service import HR_REQUEST, HrNotifier
from src.modules.hr_requests.schemas import (
    PROFILE_FIELDS,
    AdminRequestCreate,
    MyProfileOut,
    RequestComment,
    RequestCreate,
    RequestDecision,
    RequestEventOut,
    RequestOut,
)

REQUEST_LABEL = {
    "PROFILE_UPDATE": "profile update request",
    "DOCUMENT_REQUEST": "document request",
    "LETTER_REQUEST": "letter request",
    "HR_QUERY": "HR question",
    "OTHER": "HR request",
}
MAX_OPEN_PER_EMPLOYEE = 20
ALLOWED_TRANSITIONS = {
    "PENDING": {"IN_REVIEW", "APPROVED", "REJECTED", "RESOLVED"},
    "IN_REVIEW": {"APPROVED", "REJECTED", "RESOLVED"},
}


class HrRequestService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── employee side ──────────────────────────────────────────────────────

    async def my_profile(self, school_id: UUID, user_id: UUID) -> MyProfileOut:
        brief = await employee_for_user(self.db, school_id, user_id)
        emp = await EmployeeService(self.db).get(school_id, brief.id)
        return MyProfileOut.model_validate(emp.model_dump())

    async def my_requests(self, school_id: UUID, user_id: UUID) -> list[RequestOut]:
        emp = await employee_for_user(self.db, school_id, user_id)
        return await self.list(school_id, employee_id=emp.id)

    async def my_request(
        self, school_id: UUID, user_id: UUID, request_id: UUID
    ) -> RequestOut:
        emp = await employee_for_user(self.db, school_id, user_id)
        req = await self._get(school_id, request_id)
        if req.employee_id != emp.id:
            raise NotFoundError("Request not found")
        return await self._detail(school_id, req)

    async def submit_own(
        self, school_id: UUID, user_id: UUID, data: RequestCreate
    ) -> RequestOut:
        emp = await employee_for_user(self.db, school_id, user_id)
        return await self._create(school_id, emp.id, user_id, data, source="EMPLOYEE")

    async def cancel_own(
        self, school_id: UUID, user_id: UUID, request_id: UUID
    ) -> RequestOut:
        emp = await employee_for_user(self.db, school_id, user_id)
        req = await self._get(school_id, request_id)
        if req.employee_id != emp.id:
            raise NotFoundError("Request not found")
        self._assert_open(req, "withdrawn")
        previous = req.status
        req.status = "CANCELLED"
        req.cancelled_at = datetime.now(UTC)
        ev = self._event(req, user_id, "CANCELLED", previous, "CANCELLED", None)
        await self.db.flush()
        await self._notify(req, user_id, "CANCELLED", event_id=ev)
        return await self._detail(school_id, req)

    async def comment_own(
        self, school_id: UUID, user_id: UUID, request_id: UUID, data: RequestComment
    ) -> RequestOut:
        emp = await employee_for_user(self.db, school_id, user_id)
        req = await self._get(school_id, request_id)
        if req.employee_id != emp.id:
            raise NotFoundError("Request not found")
        return await self._comment(school_id, req, user_id, data)

    # ── admin side ─────────────────────────────────────────────────────────

    async def list(
        self,
        school_id: UUID,
        *,
        status: str | None = None,
        request_type: str | None = None,
        employee_id: UUID | None = None,
        q: str | None = None,
    ) -> list[RequestOut]:
        stmt = select(HrRequest).where(HrRequest.school_id == school_id)
        if status:
            s = status.upper()
            stmt = stmt.where(
                HrRequest.status.in_(HR_REQUEST_OPEN_STATUSES)
                if s == "OPEN"
                else HrRequest.status == s
            )
        if request_type:
            stmt = stmt.where(HrRequest.request_type == request_type.upper())
        if employee_id:
            stmt = stmt.where(HrRequest.employee_id == employee_id)
        rows = list(
            (
                await self.db.execute(
                    stmt.order_by(HrRequest.created_at.desc()).limit(500)
                )
            ).scalars()
        )
        out = await self._many_out(school_id, rows)
        if q:
            needle = q.strip().lower()
            out = [
                o
                for o in out
                if needle in o.subject.lower()
                or needle in o.details.lower()
                or needle in o.employee_name.lower()
                or needle in (o.employee_code or "").lower()
            ]
        return out

    async def get(self, school_id: UUID, request_id: UUID) -> RequestOut:
        return await self._detail(school_id, await self._get(school_id, request_id))

    async def submit_for(
        self, school_id: UUID, user_id: UUID, data: AdminRequestCreate
    ) -> RequestOut:
        emp = await get_employee_brief(self.db, school_id, data.employee_id)
        return await self._create(
            school_id,
            emp.id,
            user_id,
            RequestCreate.model_validate(data.model_dump(exclude={"employee_id"})),
            source="ADMIN",
        )

    async def decide(
        self, school_id: UUID, user_id: UUID, request_id: UUID, data: RequestDecision
    ) -> RequestOut:
        req = await self._get(school_id, request_id)
        self._assert_open(req, "updated")
        if data.status not in ALLOWED_TRANSITIONS[req.status]:
            raise AppError(
                f"A {req.status.lower()} request cannot move to {data.status.lower()}",
                status_code=422,
            )
        previous = req.status
        req.status = data.status
        if data.status in HR_REQUEST_DECIDED_STATUSES:
            req.decided_at = datetime.now(UTC)
            req.decided_by = user_id
            req.decision_note = data.note or None
        ev = self._event(
            req, user_id, "STATUS_CHANGED", previous, data.status, data.note or None
        )
        await self.db.flush()
        await self._notify(req, user_id, "STATUS_CHANGED", data.note, event_id=ev)
        return await self._detail(school_id, req)

    async def comment(
        self, school_id: UUID, user_id: UUID, request_id: UUID, data: RequestComment
    ) -> RequestOut:
        return await self._comment(
            school_id, await self._get(school_id, request_id), user_id, data
        )

    # ── internals ──────────────────────────────────────────────────────────

    async def _create(
        self,
        school_id: UUID,
        employee_id: UUID,
        user_id: UUID,
        data: RequestCreate,
        *,
        source: str,
    ) -> RequestOut:
        open_count = (
            await self.db.execute(
                select(func.count()).where(
                    HrRequest.school_id == school_id,
                    HrRequest.employee_id == employee_id,
                    HrRequest.status.in_(HR_REQUEST_OPEN_STATUSES),
                )
            )
        ).scalar_one()
        if open_count >= MAX_OPEN_PER_EMPLOYEE:
            raise AppError(
                f"There are already {open_count} open requests for this employee; resolve some first",
                status_code=422,
            )
        req = HrRequest(
            school_id=school_id,
            employee_id=employee_id,
            request_type=data.request_type,
            subject=data.subject,
            details=data.details,
            requested_changes=data.requested_changes,
            status="PENDING",
            source=source,
            submitted_by=user_id,
        )
        self.db.add(req)
        await self.db.flush()
        ev = self._event(req, user_id, "SUBMITTED", None, "PENDING", None)
        await self.db.flush()
        await self._notify(req, user_id, "SUBMITTED", event_id=ev)
        return await self._detail(school_id, req)

    async def _comment(
        self, school_id: UUID, req: HrRequest, user_id: UUID, data: RequestComment
    ) -> RequestOut:
        self._assert_open(req, "commented on")
        ev = self._event(req, user_id, "COMMENT", None, None, data.message)
        req.updated_at = datetime.now(UTC)
        await self.db.flush()
        await self._notify(req, user_id, "COMMENT", data.message, event_id=ev)
        return await self._detail(school_id, req)

    async def _notify(
        self,
        req: HrRequest,
        actor: UUID,
        event: str,
        message: str | None = None,
        *,
        event_id: UUID,
    ) -> None:
        emp = await get_employee_brief(self.db, req.school_id, req.employee_id)
        by_employee = bool(emp.user_id and actor == emp.user_id)
        label = REQUEST_LABEL.get(req.request_type, "HR request")
        notifier = HrNotifier(self.db)
        common = {
            "kind": "request",
            "category": HR_REQUEST,
            "entity_id": req.id,
            "event": f"{event.lower()}:{event_id}",
            "actor": actor,
        }
        if by_employee:
            title = {
                "SUBMITTED": f"New {label} from {emp.full_name}",
                "COMMENT": f"{emp.full_name} commented on a request",
                "CANCELLED": f"{emp.full_name} withdrew a request",
            }.get(event)
            if title:
                body = req.subject + (f" — {message}" if message else "")
                await notifier.to_admins(req.school_id, title=title, body=body, **common)
            return
        title = {
            "SUBMITTED": f"HR logged a request for you ({label})",
            "COMMENT": "HR replied to your request",
            "STATUS_CHANGED": f"Your request is {req.status.replace('_', ' ').lower()}",
        }.get(event)
        if title:
            body = req.subject + (f" — {message}" if message else "")
            await notifier.to_employee(
                req.school_id, emp.user_id, title=title, body=body, **common
            )

    def _event(
        self,
        req: HrRequest,
        user_id: UUID,
        event_type: str,
        from_status: str | None,
        to_status: str | None,
        message: str | None,
    ) -> UUID:
        event_id = uuid4()
        self.db.add(
            HrRequestEvent(
                id=event_id,
                school_id=req.school_id,
                request_id=req.id,
                actor_user_id=user_id,
                event_type=event_type,
                from_status=from_status,
                to_status=to_status,
                message=message,
                created_at=datetime.now(UTC),
            )
        )
        return event_id

    @staticmethod
    def _assert_open(req: HrRequest, verb: str) -> None:
        if req.status not in HR_REQUEST_OPEN_STATUSES:
            raise AppError(
                f"This request is {req.status.lower()} and can no longer be {verb}",
                status_code=422,
            )

    async def _get(self, school_id: UUID, request_id: UUID) -> HrRequest:
        req = (
            await self.db.execute(
                select(HrRequest)
                .where(HrRequest.id == request_id, HrRequest.school_id == school_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if req is None:
            raise NotFoundError("Request not found")
        return req

    async def _detail(self, school_id: UUID, req: HrRequest) -> RequestOut:
        fresh = await self._get(school_id, req.id)
        out = (await self._many_out(school_id, [fresh], with_events=True))[0]
        if fresh.request_type == "PROFILE_UPDATE" and fresh.requested_changes:
            emp = await EmployeeService(self.db).get(school_id, fresh.employee_id)
            data = emp.model_dump()
            out.current_values = {
                k: data.get(k) for k in PROFILE_FIELDS if k in fresh.requested_changes
            }
        return out

    async def _many_out(
        self, school_id: UUID, rows: list[HrRequest], *, with_events: bool = False
    ) -> list[RequestOut]:
        if not rows:
            return []
        events: dict[UUID, list[HrRequestEvent]] = defaultdict(list)
        if with_events:
            for ev in (
                await self.db.execute(
                    select(HrRequestEvent)
                    .where(
                        HrRequestEvent.request_id.in_([r.id for r in rows]),
                        HrRequestEvent.school_id == school_id,
                    )
                    .order_by(HrRequestEvent.created_at, HrRequestEvent.id)
                )
            ).scalars():
                events[ev.request_id].append(ev)
        emps = await employee_briefs(self.db, school_id, {r.employee_id for r in rows})
        users = await user_names(
            self.db,
            {u for r in rows for u in (r.submitted_by, r.decided_by)}
            | {e.actor_user_id for evs in events.values() for e in evs},
        )
        out: list[RequestOut] = []
        for r in rows:
            emp = emps.get(r.employee_id)
            out.append(
                RequestOut(
                    id=r.id,
                    employee_id=r.employee_id,
                    employee_name=emp.full_name if emp else "—",
                    employee_code=emp.employee_code if emp else None,
                    department_name=emp.department_name if emp else None,
                    request_type=r.request_type,
                    subject=r.subject,
                    details=r.details,
                    requested_changes=r.requested_changes,
                    status=r.status,
                    source=r.source,
                    submitted_by_name=users.get(r.submitted_by)
                    if r.submitted_by
                    else None,
                    decision_note=r.decision_note,
                    decided_by_name=users.get(r.decided_by) if r.decided_by else None,
                    decided_at=r.decided_at,
                    cancelled_at=r.cancelled_at,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    events=[
                        RequestEventOut(
                            id=e.id,
                            event_type=e.event_type,
                            from_status=e.from_status,
                            to_status=e.to_status,
                            message=e.message,
                            actor_name=users.get(e.actor_user_id)
                            if e.actor_user_id
                            else None,
                            actor_is_employee=bool(
                                emp and emp.user_id and e.actor_user_id == emp.user_id
                            ),
                            created_at=e.created_at,
                        )
                        for e in events.get(r.id, [])
                    ]
                    if with_events
                    else None,
                )
            )
        return out
