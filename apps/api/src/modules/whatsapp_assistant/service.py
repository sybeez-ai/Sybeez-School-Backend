"""Turn a WhatsApp message into a scoped reply, then store it. Sending is a separate decision."""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.core.exceptions import AppError
from src.core.redis import get_redis
from src.db.models import (
    AlertChannelOptOut,
    ClassRoom,
    Employee,
    Enrollment,
    Parent,
    School,
    SchoolMembership,
    Student,
    StudentParent,
    Teacher,
    TeacherAssignment,
    User,
    WhatsAppAssistantSettings,
    WhatsAppLink,
    WhatsAppLinkCode,
    WhatsAppMessage,
    WhatsAppSession,
    WhatsAppUsage,
)
from src.modules.access.deps import effective_permissions
from src.modules.alerts.service import apply_provider_status, enable_rls_bypass, store_inbound
from src.modules.alerts.webhooks import InboundUpdate, loads_json, parse_meta_webhook
from src.modules.fees.tenant import bind_tenant
from src.modules.whatsapp_assistant.conversation import (
    ChildChoice,
    Decision,
    SessionState,
    decide_turn,
    link_code_in,
    persona_for,
)
from src.modules.whatsapp_assistant.identity import PhoneHit, hash_link_code, link_code_matches, matching_profiles, new_link_code, to_e164
from src.modules.whatsapp_assistant.planner import phrase_reply, plan_tools, summarize
from src.modules.whatsapp_assistant.scope import DataScope, build_tool_query
from src.modules.whatsapp_assistant.sender import deliver, graph_url, sending_decision, template_payload, text_payload
from src.modules.whatsapp_assistant.tools import run_tool
from src.shared.enums import MembershipStatus, UserRole

logger = logging.getLogger(__name__)

_MEDIA = frozenset({"image", "audio", "video", "document", "sticker", "location", "contacts", "unsupported"})
_MEDIA_REPLY = "I can read text messages. Send your question as text."
_LINK_MINUTES = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _phone_sql(column, last10: str):
    digits = func.right(func.regexp_replace(column, r"\D", "", "g"), 10)
    return digits == last10


class WhatsAppAssistant:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def handle_meta_body(self, raw: str) -> dict:
        await enable_rls_bypass(self.db)
        payload = loads_json(raw.encode("utf-8"))
        statuses, inbound = parse_meta_webhook(payload)
        for item in statuses:
            await apply_provider_status(self.db, item, channel="WHATSAPP")
        handled = 0
        for item in inbound:
            if item.provider_message_id and await self._seen(item.provider_message_id):
                continue
            try:
                async with self.db.begin_nested():
                    await store_inbound(self.db, item, channel="WHATSAPP", raw=raw)
                    await self.handle_inbound(item)
                handled += 1
            except IntegrityError:
                continue
            except Exception as exc:
                logger.warning("whatsapp assistant message failed: %s", type(exc).__name__)
        return {"ok": True, "messages": handled}

    async def handle_inbound(self, item: InboundUpdate) -> None:
        phone = to_e164(item.from_phone)
        if not phone:
            return
        session_row = await self._session(phone)
        state = _state_from(session_row)
        text = (item.body or "").strip()
        if text.lower() in _MEDIA:
            decision = Decision(kind="media", text=_MEDIA_REPLY, run_tools=False)
            profiles = []
            children = []
            link_status = None
        else:
            link_status = None
            code = link_code_in(text)
            if code:
                link_status = await self._consume_code(code, phone)
            profiles = await self._profiles(phone)
            opted_out = await self._opted_out(phone)
            children = await self._children_for(state, profiles)
            decision = decide_turn(
                text,
                profiles=profiles,
                children=children,
                session=state,
                opted_out=opted_out and link_status is None,
                link_status=link_status,
            )
            if decision.kind == "no_child" and decision.profile and decision.profile.school_id:
                children = await self._children(decision.profile.user_id, decision.profile.school_id)
                if children:
                    decision = decide_turn(
                        text,
                        profiles=profiles,
                        children=children,
                        session=state,
                        opted_out=False,
                        link_status=link_status,
                    )
        await self._remember_inbound(session_row, item, decision)
        reply, persona, trace = await self._reply(decision, session_row, text)
        self._apply_session(session_row, decision, persona)
        status, reason, provider_id = await self._send(session_row, decision, reply)
        self.db.add(
            WhatsAppMessage(
                session_id=session_row.id,
                school_id=session_row.school_id,
                user_id=session_row.user_id,
                direction="out",
                provider_message_id=provider_id,
                body=reply,
                status=status,
                persona=persona,
                tool_trace=trace,
                send_reason=reason,
            )
        )
        await self._usage(session_row.school_id, inbound=1, generated=1, sent=status == "sent", suppressed=status != "sent")
        logger.info("whatsapp assistant stored status=%s reason=%s", status, reason or "ok")

    async def _reply(self, decision: Decision, session_row: WhatsAppSession, original: str) -> tuple[str, str | None, list]:
        if not decision.run_tools or decision.profile is None or decision.profile.school_id is None:
            persona = persona_for(decision.profile.role) if decision.profile else None
            return decision.text, persona, []
        profile = decision.profile
        persona = persona_for(profile.role)
        scope = await self._scope(profile, decision.child)
        settings_row = await self.db.get(WhatsAppAssistantSettings, profile.school_id)
        leave_enabled = bool(settings_row and settings_row.leave_requests_enabled)
        history = await self._history(session_row.id)
        if history and history[-1]["role"] == "user" and history[-1]["content"] == (original or ""):
            history = history[:-1]
        messages = history + [{"role": "user", "content": decision.text or original}]
        settings = get_settings()
        from src.core.security import create_access_token

        access_token = create_access_token(
            subject=profile.user_id, role=profile.role, school_id=profile.school_id
        )
        calls = await plan_tools(
            messages,
            persona,
            include_leave=leave_enabled,
            provider=settings.whatsapp_assistant_provider,
            ai_service_url=settings.ai_service_url,
            ai_service_token=settings.ai_service_token,
            access_token=access_token,
        )
        await bind_tenant(self.db, profile.school_id, user_id=profile.user_id, role=profile.role)
        outcomes = []
        try:
            for call in calls:
                query = build_tool_query(call.name, scope, call.arguments, leave_enabled=leave_enabled)
                if query.denied:
                    outcomes.append({"tool": call.name, "error": query.denied})
                    continue
                if call.name in {"fees_due", "fee_receipts"} and scope.role in {"PARENT", "STUDENT"}:
                    from src.modules.fees.service import FeeService

                    try:
                        allowed_ids = set(
                            await FeeService(self.db)._linked_student_ids(scope.school_id, scope.user_id, scope.role)
                        )
                    except AppError as exc:
                        outcomes.append({"tool": call.name, "error": exc.message})
                        continue
                    current_ids = query.student_ids if query.student_ids is not None else frozenset(allowed_ids)
                    narrowed = frozenset(current_ids) & frozenset(allowed_ids)
                    if not narrowed:
                        outcomes.append({"tool": call.name, "error": "That student is not on your account."})
                        continue
                    query = replace(query, student_ids=narrowed)
                try:
                    result = await run_tool(self.db, query, call.arguments)
                except AppError as exc:
                    result = {"text": exc.message}
                outcomes.append({"tool": call.name, "result": result})
        finally:
            await enable_rls_bypass(self.db)
        summary = summarize(outcomes, persona)
        text = await phrase_reply(
            summary,
            provider=settings.whatsapp_assistant_provider,
            persona=persona,
            ai_service_url=settings.ai_service_url,
            ai_service_token=settings.ai_service_token,
            access_token=access_token,
        )
        trace = [{"tool": item.get("tool"), "ok": "error" not in item} for item in outcomes]
        return text, persona, trace

    async def _send(self, session_row: WhatsAppSession, decision: Decision, reply: str) -> tuple[str, str, str | None]:
        settings = get_settings()
        school_enabled = True
        if session_row.school_id is not None:
            row = await self.db.get(WhatsAppAssistantSettings, session_row.school_id)
            school_enabled = bool(row and row.assistant_enabled)
        configured = bool(settings.whatsapp_cloud_access_token and settings.whatsapp_cloud_phone_number_id)
        pre = sending_decision(
            sending_enabled=settings.whatsapp_sending_enabled,
            school_enabled=school_enabled if session_row.school_id is not None else True,
            configured=configured,
            may_notify=decision.send,
            rate_ok=True,
            under_cap=True,
        )
        if not pre.allow:
            return "suppressed", pre.reason, None
        rate_ok = await self._rate_ok(session_row.school_id, settings.whatsapp_assistant_rate_limit_per_minute)
        under_cap = await self._under_cap(session_row.school_id, settings.whatsapp_assistant_monthly_cap)
        decision_send = sending_decision(
            sending_enabled=settings.whatsapp_sending_enabled,
            school_enabled=school_enabled if session_row.school_id is not None else True,
            configured=configured,
            may_notify=decision.send,
            rate_ok=rate_ok,
            under_cap=under_cap,
        )
        status, provider_id = deliver(
            decision_send,
            text_payload(session_row.phone_e164, reply),
            url=graph_url(settings.whatsapp_cloud_phone_number_id),
            access_token=settings.whatsapp_cloud_access_token,
        )
        if status == "sent":
            return status, "ok", provider_id
        return status, decision_send.reason if status == "suppressed" else "send_failed", provider_id

    async def issue_link_code(self, user: CurrentUser) -> dict:
        await bind_tenant(self.db, user.school_id, user_id=user.id, role=user.role.value)
        secret = get_settings().jwt_secret
        code = new_link_code()
        now = _now()
        existing = (
            await self.db.execute(
                select(WhatsAppLinkCode).where(
                    WhatsAppLinkCode.user_id == user.id,
                    WhatsAppLinkCode.consumed_at.is_(None),
                )
            )
        ).scalars().all()
        for row in existing:
            row.consumed_at = now
        self.db.add(
            WhatsAppLinkCode(
                user_id=user.id,
                code_hash=hash_link_code(code, user.id, secret),
                expires_at=now + timedelta(minutes=_LINK_MINUTES),
            )
        )
        await self.db.flush()
        return {
            "code": code,
            "expires_in_seconds": _LINK_MINUTES * 60,
            "instructions": "From the WhatsApp number you want to add, send LINK followed by this code. Your profile phone already works without a code.",
        }

    async def unlink(self, user: CurrentUser) -> dict:
        await bind_tenant(self.db, user.school_id, user_id=user.id, role=user.role.value)
        rows = (await self.db.execute(select(WhatsAppLink).where(WhatsAppLink.user_id == user.id))).scalars().all()
        for row in rows:
            await self.db.delete(row)
        return {"removed": len(rows)}

    async def _consume_code(self, code: str, phone: str) -> str:
        now = _now()
        rows = (
            await self.db.execute(
                select(WhatsAppLinkCode).where(
                    WhatsAppLinkCode.consumed_at.is_(None),
                    WhatsAppLinkCode.expires_at > now,
                )
            )
        ).scalars().all()
        secret = get_settings().jwt_secret
        match = next((row for row in rows if link_code_matches(code, row.user_id, secret, row.code_hash)), None)
        if match is None:
            return "invalid"
        match.consumed_at = now
        existing = (
            await self.db.execute(
                select(WhatsAppLink).where(WhatsAppLink.user_id == match.user_id, WhatsAppLink.phone_e164 == phone)
            )
        ).scalar_one_or_none()
        if existing is None:
            self.db.add(WhatsAppLink(user_id=match.user_id, phone_e164=phone))
        return "linked"

    async def _profiles(self, phone: str) -> list:
        last10 = phone[-10:]
        hits: list[PhoneHit] = []
        names: dict[uuid.UUID, str] = {}

        async def school_name(school_id: uuid.UUID) -> str:
            if school_id not in names:
                school = await self.db.get(School, school_id)
                names[school_id] = school.name if school else "School"
            return names[school_id]

        async def add_memberships(user: User, saved_phone: str) -> None:
            memberships = (
                await self.db.execute(
                    select(SchoolMembership).where(
                        SchoolMembership.user_id == user.id,
                        SchoolMembership.status == MembershipStatus.ACTIVE.value,
                    )
                )
            ).scalars().all()
            if memberships:
                for membership in memberships:
                    name = await school_name(membership.school_id)
                    role = membership.role.replace("_", " ").title()
                    hits.append(
                        PhoneHit(
                            phone=saved_phone,
                            user_id=user.id,
                            school_id=membership.school_id,
                            role=membership.role,
                            label=f"{user.full_name} — {role} — {name}",
                        )
                    )
                return
            if user.is_platform_admin:
                hits.append(
                    PhoneHit(
                        phone=saved_phone,
                        user_id=user.id,
                        school_id=None,
                        role="PLATFORM_ADMIN",
                        label=f"{user.full_name} — Platform",
                    )
                )

        users = (await self.db.execute(select(User).where(User.phone.is_not(None), _phone_sql(User.phone, last10)))).scalars().all()
        for user in users:
            await add_memberships(user, user.phone or "")
        parents = (
            await self.db.execute(select(Parent).where(Parent.phone.is_not(None), _phone_sql(Parent.phone, last10)))
        ).scalars().all()
        for parent in parents:
            user = await self.db.get(User, parent.user_id)
            if user is None:
                continue
            name = await school_name(parent.school_id)
            hits.append(
                PhoneHit(
                    phone=parent.phone or "",
                    user_id=parent.user_id,
                    school_id=parent.school_id,
                    role="PARENT",
                    label=f"{user.full_name} — Parent — {name}",
                )
            )
        students = (
            await self.db.execute(select(Student).where(Student.phone.is_not(None), _phone_sql(Student.phone, last10)))
        ).scalars().all()
        for student in students:
            user = await self.db.get(User, student.user_id)
            if user is None:
                continue
            name = await school_name(student.school_id)
            hits.append(
                PhoneHit(
                    phone=student.phone or "",
                    user_id=student.user_id,
                    school_id=student.school_id,
                    role="STUDENT",
                    student_id=student.id,
                    label=f"{user.full_name} — Student — {name}",
                )
            )
        teachers = (
            await self.db.execute(select(Teacher).where(Teacher.phone.is_not(None), _phone_sql(Teacher.phone, last10)))
        ).scalars().all()
        for teacher in teachers:
            user = await self.db.get(User, teacher.user_id)
            if user is None:
                continue
            name = await school_name(teacher.school_id)
            hits.append(
                PhoneHit(
                    phone=teacher.phone or "",
                    user_id=teacher.user_id,
                    school_id=teacher.school_id,
                    role="TEACHER",
                    label=f"{user.full_name} — Teacher — {name}",
                )
            )
        employees = (
            await self.db.execute(select(Employee).where(Employee.phone.is_not(None), _phone_sql(Employee.phone, last10)))
        ).scalars().all()
        for employee in employees:
            if employee.user_id is None:
                continue
            user = await self.db.get(User, employee.user_id)
            if user is None:
                continue
            await add_memberships(user, employee.phone or "")
            if not any(hit.user_id == user.id and hit.school_id == employee.school_id for hit in hits):
                name = await school_name(employee.school_id)
                hits.append(
                    PhoneHit(
                        phone=employee.phone or "",
                        user_id=user.id,
                        school_id=employee.school_id,
                        role="STAFF",
                        label=f"{employee.full_name or user.full_name} — Staff — {name}",
                    )
                )
        links = (
            await self.db.execute(select(WhatsAppLink).where(_phone_sql(WhatsAppLink.phone_e164, last10)))
        ).scalars().all()
        for link in links:
            user = await self.db.get(User, link.user_id)
            if user is not None:
                await add_memberships(user, link.phone_e164)
        return matching_profiles(phone, hits)

    async def _children_for(self, state: SessionState, profiles) -> list[ChildChoice]:
        selected = _selected(state, profiles)
        profile = selected
        if profile is None and len(profiles) == 1:
            profile = profiles[0]
        if profile is None or profile.role != "PARENT" or profile.school_id is None:
            return []
        return await self._children(profile.user_id, profile.school_id)

    async def _children(self, user_id: uuid.UUID, school_id: uuid.UUID) -> list[ChildChoice]:
        parent = (
            await self.db.execute(select(Parent).where(Parent.user_id == user_id, Parent.school_id == school_id))
        ).scalar_one_or_none()
        if parent is None:
            return []
        rows = (
            await self.db.execute(
                select(Student.id, User.full_name, ClassRoom.name)
                .join(StudentParent, StudentParent.student_id == Student.id)
                .join(User, User.id == Student.user_id)
                .outerjoin(
                    Enrollment,
                    (Enrollment.student_id == Student.id)
                    & (Enrollment.school_id == school_id)
                    & (Enrollment.status == "ACTIVE"),
                )
                .outerjoin(ClassRoom, ClassRoom.id == Enrollment.class_id)
                .where(StudentParent.parent_id == parent.id, StudentParent.school_id == school_id)
            )
        ).all()
        found: dict[uuid.UUID, ChildChoice] = {}
        for student_id, full_name, class_name in rows:
            label = full_name if not class_name else f"{full_name} — {class_name}"
            current = found.get(student_id)
            if current is None or (class_name and "—" not in current.label):
                found[student_id] = ChildChoice(student_id, label)
        return list(found.values())

    async def _scope(self, profile, child: ChildChoice | None) -> DataScope:
        assert profile.school_id is not None
        membership = (
            await self.db.execute(
                select(SchoolMembership).where(
                    SchoolMembership.user_id == profile.user_id,
                    SchoolMembership.school_id == profile.school_id,
                    SchoolMembership.role == profile.role,
                    SchoolMembership.status == MembershipStatus.ACTIVE.value,
                )
            )
        ).scalar_one_or_none()
        role = _role(profile.role)
        current = CurrentUser(
            id=profile.user_id,
            email="",
            full_name=profile.label,
            role=role,
            school_id=profile.school_id,
            membership_id=None if membership is None else membership.id,
            is_platform_admin=profile.role == "PLATFORM_ADMIN",
        )
        permissions = frozenset(await effective_permissions(self.db, current))
        student_ids: set[uuid.UUID] = set()
        class_ids: set[uuid.UUID] = set()
        if profile.role == "PARENT":
            for item in await self._children(profile.user_id, profile.school_id):
                student_ids.add(item.student_id)
            if child is not None:
                student_ids &= {child.student_id}
        elif profile.role == "STUDENT":
            student = await self._student_for_user(profile.user_id, profile.school_id)
            if student is not None:
                student_ids.add(student.id)
            elif profile.student_id is not None:
                student_ids.add(profile.student_id)
        if student_ids:
            enrolled = (
                await self.db.execute(
                    select(Enrollment.class_id).where(
                        Enrollment.school_id == profile.school_id,
                        Enrollment.student_id.in_(student_ids),
                        Enrollment.status == "ACTIVE",
                    )
                )
            ).scalars().all()
            class_ids.update(enrolled)
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.user_id == profile.user_id, Teacher.school_id == profile.school_id)
            )
        ).scalar_one_or_none()
        if profile.role == "TEACHER" and teacher is not None:
            assigned = (
                await self.db.execute(
                    select(TeacherAssignment.class_id).where(
                        TeacherAssignment.teacher_id == teacher.id,
                        TeacherAssignment.school_id == profile.school_id,
                    )
                )
            ).scalars().all()
            class_ids.update(assigned)
            led = (
                await self.db.execute(
                    select(ClassRoom.id).where(
                        ClassRoom.class_teacher_id == teacher.id,
                        ClassRoom.school_id == profile.school_id,
                    )
                )
            ).scalars().all()
            class_ids.update(led)
        employee = (
            await self.db.execute(
                select(Employee).where(Employee.user_id == profile.user_id, Employee.school_id == profile.school_id)
            )
        ).scalar_one_or_none()
        return DataScope(
            user_id=profile.user_id,
            school_id=profile.school_id,
            role=profile.role,
            permissions=permissions,
            student_ids=frozenset(student_ids),
            class_ids=frozenset(class_ids),
            selected_student_id=None if child is None else child.student_id,
            employee_id=None if employee is None else employee.id,
            teacher_id=None if teacher is None else teacher.id,
        )

    async def _student_for_user(self, user_id: uuid.UUID, school_id: uuid.UUID) -> Student | None:
        return (
            await self.db.execute(select(Student).where(Student.user_id == user_id, Student.school_id == school_id))
        ).scalar_one_or_none()

    async def _session(self, phone: str) -> WhatsAppSession:
        row = (
            await self.db.execute(select(WhatsAppSession).where(WhatsAppSession.phone_e164 == phone))
        ).scalar_one_or_none()
        if row is None:
            row = WhatsAppSession(phone_e164=phone)
            self.db.add(row)
            await self.db.flush()
        return row

    async def _seen(self, provider_message_id: str) -> bool:
        row = (
            await self.db.execute(
                select(WhatsAppMessage.id).where(WhatsAppMessage.provider_message_id == provider_message_id).limit(1)
            )
        ).first()
        return row is not None

    async def _opted_out(self, phone: str) -> bool:
        row = (
            await self.db.execute(
                select(AlertChannelOptOut.id).where(
                    AlertChannelOptOut.channel == "WHATSAPP",
                    AlertChannelOptOut.phone.in_({phone, f"+{phone}"}),
                ).limit(1)
            )
        ).first()
        return row is not None

    async def _remember_inbound(self, session_row: WhatsAppSession, item: InboundUpdate, decision: Decision) -> None:
        self.db.add(
            WhatsAppMessage(
                session_id=session_row.id,
                school_id=None if decision.profile is None else decision.profile.school_id,
                user_id=None if decision.profile is None else decision.profile.user_id,
                direction="in",
                provider_message_id=item.provider_message_id,
                body=item.body or "",
                status="received",
            )
        )
        await self.db.flush()

    async def _history(self, session_id: uuid.UUID) -> list[dict]:
        rows = (
            await self.db.execute(
                select(WhatsAppMessage)
                .where(WhatsAppMessage.session_id == session_id)
                .order_by(WhatsAppMessage.created_at.desc())
                .limit(12)
            )
        ).scalars().all()
        ordered = list(reversed(rows))
        return [
            {"role": "assistant" if row.direction == "out" else "user", "content": row.body}
            for row in ordered
            if row.direction in {"in", "out"}
        ]

    def _apply_session(self, session_row: WhatsAppSession, decision: Decision, persona: str | None) -> None:
        if decision.clear_selection:
            session_row.user_id = None
            session_row.school_id = None
            session_row.role = None
            session_row.student_id = None
            session_row.pending = None
            session_row.options_json = None
            session_row.pending_text = None
            return
        if decision.profile is not None and decision.kind not in {"ask_profile", "ask_child", "no_school"}:
            session_row.user_id = decision.profile.user_id
            session_row.school_id = decision.profile.school_id
            session_row.role = decision.profile.role
            session_row.student_id = decision.child.student_id if decision.child else decision.profile.student_id
            session_row.pending = None
            session_row.options_json = None
            session_row.pending_text = None
            session_row.persona = persona or persona_for(decision.profile.role)
            return
        if decision.pending == "profile":
            session_row.user_id = None
            session_row.school_id = None
            session_row.role = None
            session_row.student_id = None
            session_row.pending = "profile"
            session_row.options_json = decision.options
            session_row.pending_text = decision.pending_text
            return
        if decision.pending:
            session_row.pending = decision.pending
            session_row.options_json = decision.options
            session_row.pending_text = decision.pending_text
            if decision.profile is not None:
                session_row.user_id = decision.profile.user_id
                session_row.school_id = decision.profile.school_id
                session_row.role = decision.profile.role
                session_row.student_id = decision.child.student_id if decision.child else None

    async def _usage(self, school_id: uuid.UUID | None, *, inbound: int, generated: int, sent: bool, suppressed: bool) -> None:
        if school_id is None:
            return
        period = _now().strftime("%Y-%m")
        row = (
            await self.db.execute(
                select(WhatsAppUsage).where(WhatsAppUsage.school_id == school_id, WhatsAppUsage.year_month == period)
            )
        ).scalar_one_or_none()
        if row is None:
            # Python column defaults are applied at flush, not when the object is built.
            # Leaving the counters as None makes the increments below raise TypeError,
            # and the savepoint then drops the session and the transcript.
            row = WhatsAppUsage(
                school_id=school_id,
                year_month=period,
                inbound_count=0,
                generated_count=0,
                sent_count=0,
                suppressed_count=0,
            )
            self.db.add(row)
        row.inbound_count += inbound
        row.generated_count += generated
        if sent:
            row.sent_count += 1
        if suppressed:
            row.suppressed_count += 1

    async def _rate_ok(self, school_id: uuid.UUID | None, limit: int) -> bool:
        if limit <= 0:
            return True
        key = f"rl:whatsapp_assistant:{school_id or 'unknown'}:{int(_now().timestamp()) // 60}"
        try:
            client = await get_redis()
            count = int(await client.incr(key))
            if count == 1:
                await client.expire(key, 70)
        except Exception:
            logger.warning("whatsapp assistant rate limit unavailable")
            return False
        return count <= limit

    async def _under_cap(self, school_id: uuid.UUID | None, cap: int) -> bool:
        if school_id is None or cap <= 0:
            return True
        period = _now().strftime("%Y-%m")
        sent = (
            await self.db.execute(
                select(WhatsAppUsage.sent_count).where(
                    WhatsAppUsage.school_id == school_id,
                    WhatsAppUsage.year_month == period,
                )
            )
        ).scalar_one_or_none()
        return int(sent or 0) < cap


def _state_from(row: WhatsAppSession) -> SessionState:
    return SessionState(
        user_id=row.user_id,
        school_id=row.school_id,
        role=row.role,
        student_id=row.student_id,
        pending=row.pending,
        options=list(row.options_json or []),
        pending_text=row.pending_text,
    )


def _selected(state: SessionState, profiles) -> object | None:
    for profile in profiles:
        if profile.user_id == state.user_id and profile.school_id == state.school_id and profile.role == state.role:
            return profile
    return None


def _role(value: str) -> UserRole:
    try:
        return UserRole(value)
    except ValueError:
        return UserRole.STAFF


def send_template(
    *,
    to: str,
    template_name: str,
    language_code: str,
    components: list | None,
    sending_enabled: bool,
    school_enabled: bool,
    configured: bool,
    access_token: str,
    phone_number_id: str,
    post=None,
) -> tuple[str, str | None]:
    """Approved template send. The same global and per-school switches apply. No SMS."""
    decision = sending_decision(
        sending_enabled=sending_enabled,
        school_enabled=school_enabled,
        configured=configured,
        may_notify=True,
        rate_ok=True,
        under_cap=True,
    )
    return deliver(
        decision,
        template_payload(to, template_name, language_code, components),
        url=graph_url(phone_number_id),
        access_token=access_token,
        post=post,
    )
