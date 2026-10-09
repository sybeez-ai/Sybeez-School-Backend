from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import AppError, ForbiddenError, NotFoundError
from src.db.models import (
    Announcement,
    AnnouncementAudience,
    ClassRoom,
    CommunicationAuditLog,
    Conversation,
    ConversationMessage,
    ConversationParticipant,
    Enrollment,
    Notification,
    NotificationDelivery,
    NotificationPreference,
    Parent,
    Student,
    StudentParent,
    Teacher,
    TeacherAssignment,
    User,
)
from src.modules.communication.schemas import (
    AnnouncementCreate,
    AnnouncementOut,
    AnnouncementUpdate,
    AudienceIn,
    AudienceOut,
    CommunicationStatsOut,
    ConversationCreate,
    ConversationOut,
    MessageContactOut,
    MessageCreate,
    MessageOut,
    NotificationListOut,
    NotificationOut,
    PreferenceItem,
    PreferencesOut,
    PreferencesUpdate,
    UnreadCountOut,
)
from src.modules.notifications.service import create_bulk_notifications, create_in_app_notification
from src.shared.enums import (
    AnnouncementStatus,
    AudienceType,
    ConversationType,
    EnrollmentStatus,
    NotificationPriority,
    NotificationType,
    UserRole,
)

DEFAULT_PREF_TYPES = [
    NotificationType.ATTENDANCE_ABSENCE.value,
    NotificationType.ATTENDANCE_LATE.value,
    NotificationType.LEAVE_SUBMITTED.value,
    NotificationType.LEAVE_APPROVED.value,
    NotificationType.LEAVE_REJECTED.value,
    NotificationType.HOMEWORK_PUBLISHED.value,
    NotificationType.HOMEWORK_GRADED.value,
    NotificationType.HOMEWORK_RETURNED.value,
    NotificationType.EXAM_PUBLISHED.value,
    NotificationType.RESULT_PUBLISHED.value,
    NotificationType.ANNOUNCEMENT.value,
    NotificationType.MESSAGE_RECEIVED.value,
    NotificationType.SYSTEM.value,
    NotificationType.FEE_INVOICE_ISSUED.value,
    NotificationType.FEE_DUE_REMINDER.value,
    NotificationType.FEE_OVERDUE.value,
    NotificationType.FEE_PAYMENT_SUCCESS.value,
    NotificationType.FEE_PAYMENT_FAILED.value,
    NotificationType.FEE_RECEIPT_READY.value,
    NotificationType.FEE_REFUND.value,
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CommunicationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _user_name(self, user_id: UUID | None) -> str | None:
        if not user_id:
            return None
        u = await self.db.get(User, user_id)
        return u.full_name if u else None

    async def _audit(
        self,
        school_id: UUID,
        actor_user_id: UUID | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        meta: dict | None = None,
    ) -> None:
        self.db.add(
            CommunicationAuditLog(
                school_id=school_id,
                actor_user_id=actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                meta_json=json.dumps(meta) if meta else None,
            )
        )

    # ── notifications ────────────────────────────────────────

    async def list_notifications(
        self,
        user_id: UUID,
        *,
        unread: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> NotificationListOut:
        q = select(Notification).where(Notification.user_id == user_id)
        count_q = select(func.count()).select_from(Notification).where(Notification.user_id == user_id)
        if unread is True:
            q = q.where(Notification.is_read.is_(False))
            count_q = count_q.where(Notification.is_read.is_(False))
        total = int((await self.db.execute(count_q)).scalar_one())
        unread_count = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.user_id == user_id, Notification.is_read.is_(False))
                )
            ).scalar_one()
        )
        rows = (
            await self.db.execute(
                q.order_by(Notification.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        return NotificationListOut(
            items=[NotificationOut.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
            unread_count=unread_count,
        )

    async def unread_count(self, user_id: UUID) -> UnreadCountOut:
        count = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.user_id == user_id, Notification.is_read.is_(False))
                )
            ).scalar_one()
        )
        return UnreadCountOut(count=count)

    async def mark_read(self, user_id: UUID, notification_id: UUID) -> NotificationOut:
        row = await self.db.get(Notification, notification_id)
        if not row or row.user_id != user_id:
            raise NotFoundError("Notification not found")
        row.is_read = True
        row.read_at = _utcnow()
        await self.db.commit()
        await self.db.refresh(row)
        return NotificationOut.model_validate(row)

    async def mark_all_read(self, user_id: UUID) -> UnreadCountOut:
        rows = (
            await self.db.execute(
                select(Notification).where(
                    Notification.user_id == user_id, Notification.is_read.is_(False)
                )
            )
        ).scalars().all()
        now = _utcnow()
        for r in rows:
            r.is_read = True
            r.read_at = now
        await self.db.commit()
        return UnreadCountOut(count=0)

    async def delete_notification(self, user_id: UUID, notification_id: UUID) -> None:
        row = await self.db.get(Notification, notification_id)
        if not row or row.user_id != user_id:
            raise NotFoundError("Notification not found")
        await self.db.delete(row)
        await self.db.commit()

    # ── preferences ──────────────────────────────────────────

    async def get_preferences(self, user_id: UUID, school_id: UUID | None) -> PreferencesOut:
        existing = {
            p.notification_type: p
            for p in (
                await self.db.execute(
                    select(NotificationPreference).where(NotificationPreference.user_id == user_id)
                )
            ).scalars().all()
        }
        items: list[PreferenceItem] = []
        for t in DEFAULT_PREF_TYPES:
            p = existing.get(t)
            items.append(
                PreferenceItem(
                    notification_type=t,
                    in_app_enabled=True if p is None else p.in_app_enabled,
                    email_enabled=False if p is None else p.email_enabled,
                    sms_enabled=False if p is None else p.sms_enabled,
                        whatsapp_enabled=False if p is None else getattr(p, "whatsapp_enabled", False),
                )
            )
        return PreferencesOut(items=items)

    async def update_preferences(
        self, user_id: UUID, school_id: UUID | None, data: PreferencesUpdate
    ) -> PreferencesOut:
        for item in data.items:
            if item.notification_type == NotificationType.SYSTEM.value:
                # Cannot disable system
                item.in_app_enabled = True
            row = (
                await self.db.execute(
                    select(NotificationPreference).where(
                        NotificationPreference.user_id == user_id,
                        NotificationPreference.notification_type == item.notification_type,
                    )
                )
            ).scalar_one_or_none()
            if row:
                row.in_app_enabled = item.in_app_enabled
                row.email_enabled = item.email_enabled
                row.sms_enabled = item.sms_enabled
                row.whatsapp_enabled = getattr(item, "whatsapp_enabled", False)
            else:
                self.db.add(
                    NotificationPreference(
                        school_id=school_id,
                        user_id=user_id,
                        notification_type=item.notification_type,
                        in_app_enabled=item.in_app_enabled,
                        email_enabled=item.email_enabled,
                        sms_enabled=item.sms_enabled,
                        whatsapp_enabled=getattr(item, "whatsapp_enabled", False),
                    )
                )
        await self.db.commit()
        return await self.get_preferences(user_id, school_id)

    # ── outbound deliveries (read-only log) ──────────────────

    async def list_deliveries(self, school_id: UUID, *, limit: int = 200) -> list[NotificationDelivery]:
        rows = (
            await self.db.execute(
                select(NotificationDelivery)
                .where(NotificationDelivery.school_id == school_id)
                .order_by(NotificationDelivery.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return list(rows)

    # ── announcements ────────────────────────────────────────

    def _announcement_out(self, a: Announcement) -> AnnouncementOut:
        return AnnouncementOut(
            id=a.id,
            school_id=a.school_id,
            title=a.title,
            content=a.body,
            status=a.status,
            priority=a.priority,
            created_by=a.author_id,
            published_at=a.published_at,
            expires_at=a.expires_at,
            created_at=a.created_at,
            audiences=[AudienceOut.model_validate(x) for x in (a.audiences or [])],
        )

    async def list_announcements(
        self,
        school_id: UUID,
        *,
        user_id: UUID,
        role: str,
        status: str | None = None,
        for_recipient: bool = False,
    ) -> list[AnnouncementOut]:
        q = (
            select(Announcement)
            .where(Announcement.school_id == school_id)
            .options(selectinload(Announcement.audiences))
            .order_by(Announcement.created_at.desc())
        )
        if for_recipient or role in (
            UserRole.STUDENT.value,
            UserRole.PARENT.value,
            UserRole.TEACHER.value,
        ):
            if role != UserRole.SCHOOL_ADMIN.value:
                q = q.where(Announcement.status == AnnouncementStatus.PUBLISHED.value)
        elif status:
            q = q.where(Announcement.status == status)
        rows = (await self.db.execute(q)).scalars().unique().all()
        if role == UserRole.SCHOOL_ADMIN.value and not for_recipient:
            return [self._announcement_out(a) for a in rows]
        # Filter to announcements targeting this user
        out: list[AnnouncementOut] = []
        for a in rows:
            if await self._user_in_audience(school_id, user_id, role, a):
                out.append(self._announcement_out(a))
        return out

    async def get_announcement(self, school_id: UUID, announcement_id: UUID) -> AnnouncementOut:
        a = (
            await self.db.execute(
                select(Announcement)
                .where(Announcement.id == announcement_id, Announcement.school_id == school_id)
                .options(selectinload(Announcement.audiences))
            )
        ).scalar_one_or_none()
        if not a:
            raise NotFoundError("Announcement not found")
        return self._announcement_out(a)

    async def create_announcement(
        self, school_id: UUID, user_id: UUID, data: AnnouncementCreate
    ) -> AnnouncementOut:
        a = Announcement(
            school_id=school_id,
            author_id=user_id,
            title=data.title.strip(),
            body=data.content,
            status=AnnouncementStatus.DRAFT.value,
            priority=data.priority,
            expires_at=data.expires_at,
            is_published=False,
        )
        self.db.add(a)
        await self.db.flush()
        await self._replace_audiences(school_id, a.id, data.audiences)
        await self._audit(school_id, user_id, "ANNOUNCEMENT_CREATED", "announcement", a.id)
        await self.db.commit()
        return await self.get_announcement(school_id, a.id)

    async def update_announcement(
        self, school_id: UUID, user_id: UUID, announcement_id: UUID, data: AnnouncementUpdate
    ) -> AnnouncementOut:
        a = await self.db.get(Announcement, announcement_id)
        if not a or a.school_id != school_id:
            raise NotFoundError("Announcement not found")
        if a.status == AnnouncementStatus.ARCHIVED.value:
            raise AppError("Cannot edit archived announcement", status_code=409)
        payload = data.model_dump(exclude_unset=True)
        audiences = payload.pop("audiences", None)
        if "content" in payload:
            a.body = payload.pop("content")
        if "title" in payload:
            a.title = payload.pop("title")
        for k, v in payload.items():
            setattr(a, k, v)
        if audiences is not None:
            await self._replace_audiences(
                school_id, a.id, [AudienceIn(**x) if isinstance(x, dict) else x for x in audiences]
            )
        await self.db.commit()
        return await self.get_announcement(school_id, a.id)

    async def _replace_audiences(
        self, school_id: UUID, announcement_id: UUID, audiences: list[AudienceIn]
    ) -> None:
        existing = (
            await self.db.execute(
                select(AnnouncementAudience).where(
                    AnnouncementAudience.announcement_id == announcement_id
                )
            )
        ).scalars().all()
        for e in existing:
            await self.db.delete(e)
        for aud in audiences:
            self.db.add(
                AnnouncementAudience(
                    school_id=school_id,
                    announcement_id=announcement_id,
                    audience_type=aud.audience_type,
                    role=aud.role,
                    grade_id=aud.grade_id,
                    section_id=aud.section_id,
                    user_id=aud.user_id,
                )
            )

    async def publish_announcement(
        self, school_id: UUID, user_id: UUID, announcement_id: UUID
    ) -> AnnouncementOut:
        a = (
            await self.db.execute(
                select(Announcement)
                .where(Announcement.id == announcement_id, Announcement.school_id == school_id)
                .options(selectinload(Announcement.audiences))
            )
        ).scalar_one_or_none()
        if not a:
            raise NotFoundError("Announcement not found")
        recipients = await self._resolve_audience_users(school_id, a)
        a.status = AnnouncementStatus.PUBLISHED.value
        a.is_published = True
        a.published_at = _utcnow()
        await create_bulk_notifications(
            self.db,
            school_id=school_id,
            recipient_user_ids=recipients,
            title=a.title,
            body=a.body[:500],
            category=NotificationType.ANNOUNCEMENT.value,
            link_path="/announcements",
            entity_type="announcement",
            entity_id=a.id,
            priority=a.priority or NotificationPriority.NORMAL.value,
            event_key_prefix=f"ANNOUNCEMENT:{a.id}",
        )
        await self._audit(school_id, user_id, "ANNOUNCEMENT_PUBLISHED", "announcement", a.id)
        await self.db.commit()
        return await self.get_announcement(school_id, a.id)

    async def archive_announcement(
        self, school_id: UUID, user_id: UUID, announcement_id: UUID
    ) -> AnnouncementOut:
        a = await self.db.get(Announcement, announcement_id)
        if not a or a.school_id != school_id:
            raise NotFoundError("Announcement not found")
        a.status = AnnouncementStatus.ARCHIVED.value
        a.is_published = False
        await self._audit(school_id, user_id, "ANNOUNCEMENT_ARCHIVED", "announcement", a.id)
        await self.db.commit()
        return await self.get_announcement(school_id, a.id)

    async def delete_announcement(self, school_id: UUID, announcement_id: UUID) -> None:
        a = await self.db.get(Announcement, announcement_id)
        if not a or a.school_id != school_id:
            raise NotFoundError("Announcement not found")
        await self.db.delete(a)
        await self.db.commit()

    async def create_teacher_announcement(
        self,
        school_id: UUID,
        user_id: UUID,
        *,
        class_id: UUID,
        title: str,
        content: str,
        audience_role: str = "BOTH",  # STUDENT|PARENT|BOTH
        priority: str = "NORMAL",
    ) -> AnnouncementOut:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not teacher:
            raise ForbiddenError("Teacher profile required")
        assigned = (
            await self.db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.teacher_id == teacher.id,
                    TeacherAssignment.class_id == class_id,
                )
            )
        ).scalar_one_or_none()
        if not assigned:
            raise ForbiddenError("Not assigned to this class")
        audiences: list[AudienceIn] = []
        if audience_role in ("STUDENT", "BOTH"):
            audiences.append(
                AudienceIn(audience_type=AudienceType.SECTION.value, section_id=class_id, role="STUDENT")
            )
        if audience_role in ("PARENT", "BOTH"):
            audiences.append(
                AudienceIn(audience_type=AudienceType.SECTION.value, section_id=class_id, role="PARENT")
            )
        created = await self.create_announcement(
            school_id,
            user_id,
            AnnouncementCreate(
                title=title,
                content=content,
                priority=priority,
                audiences=audiences,
            ),
        )
        return await self.publish_announcement(school_id, user_id, created.id)

    async def _resolve_audience_users(self, school_id: UUID, a: Announcement) -> list[UUID]:
        users: set[UUID] = set()
        audiences = a.audiences or []
        if not audiences and a.class_id:
            audiences = [
                AnnouncementAudience(
                    school_id=school_id,
                    announcement_id=a.id,
                    audience_type=AudienceType.SECTION.value,
                    section_id=a.class_id,
                )
            ]
        if not audiences:
            # Default: whole school
            for role in (UserRole.STUDENT, UserRole.PARENT, UserRole.TEACHER):
                users |= await self._users_for_role(school_id, role.value)
            return list(users)

        for aud in audiences:
            if aud.audience_type == AudienceType.SCHOOL.value:
                for role in (UserRole.STUDENT, UserRole.PARENT, UserRole.TEACHER):
                    users |= await self._users_for_role(school_id, role.value)
            elif aud.audience_type == AudienceType.ROLE.value and aud.role:
                users |= await self._users_for_role(school_id, aud.role.upper())
            elif aud.audience_type == AudienceType.GRADE.value and aud.grade_id:
                users |= await self._users_for_grade(school_id, aud.grade_id, aud.role)
            elif aud.audience_type == AudienceType.SECTION.value and aud.section_id:
                users |= await self._users_for_section(school_id, aud.section_id, aud.role)
            elif aud.audience_type == AudienceType.USER.value and aud.user_id:
                users.add(aud.user_id)
        return list(users)

    async def _users_for_role(self, school_id: UUID, role: str) -> set[UUID]:
        role = role.upper()
        out: set[UUID] = set()
        if role == UserRole.STUDENT.value:
            rows = (
                await self.db.execute(select(Student).where(Student.school_id == school_id))
            ).scalars().all()
            out = {r.user_id for r in rows}
        elif role == UserRole.PARENT.value:
            rows = (
                await self.db.execute(select(Parent).where(Parent.school_id == school_id))
            ).scalars().all()
            out = {r.user_id for r in rows}
        elif role == UserRole.TEACHER.value:
            rows = (
                await self.db.execute(select(Teacher).where(Teacher.school_id == school_id))
            ).scalars().all()
            out = {r.user_id for r in rows}
        return out

    async def _users_for_section(
        self, school_id: UUID, section_id: UUID, role: str | None
    ) -> set[UUID]:
        out: set[UUID] = set()
        enrollments = (
            await self.db.execute(
                select(Enrollment).where(
                    Enrollment.school_id == school_id,
                    Enrollment.class_id == section_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalars().all()
        student_ids = [e.student_id for e in enrollments]
        want_students = role is None or role.upper() in ("STUDENT", "BOTH")
        want_parents = role is None or role.upper() in ("PARENT", "BOTH")
        if want_students:
            for sid in student_ids:
                st = await self.db.get(Student, sid)
                if st:
                    out.add(st.user_id)
        if want_parents:
            for sid in student_ids:
                links = (
                    await self.db.execute(
                        select(StudentParent).where(
                            StudentParent.school_id == school_id,
                            StudentParent.student_id == sid,
                        )
                    )
                ).scalars().all()
                for link in links:
                    parent = await self.db.get(Parent, link.parent_id)
                    if parent:
                        out.add(parent.user_id)
        return out

    async def _users_for_grade(
        self, school_id: UUID, grade_id: UUID, role: str | None
    ) -> set[UUID]:
        sections = (
            await self.db.execute(
                select(ClassRoom).where(
                    ClassRoom.school_id == school_id, ClassRoom.grade_id == grade_id
                )
            )
        ).scalars().all()
        out: set[UUID] = set()
        for sec in sections:
            out |= await self._users_for_section(school_id, sec.id, role)
        return out

    async def _user_in_audience(
        self, school_id: UUID, user_id: UUID, role: str, a: Announcement
    ) -> bool:
        recipients = await self._resolve_audience_users(school_id, a)
        return user_id in recipients

    # ── messaging ────────────────────────────────────────────

    async def list_conversations(self, school_id: UUID, user_id: UUID) -> list[ConversationOut]:
        parts = (
            await self.db.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.school_id == school_id,
                    ConversationParticipant.user_id == user_id,
                )
            )
        ).scalars().all()
        out: list[ConversationOut] = []
        for p in parts:
            conv = await self.db.get(Conversation, p.conversation_id)
            if not conv:
                continue
            others = (
                await self.db.execute(
                    select(ConversationParticipant).where(
                        ConversationParticipant.conversation_id == conv.id,
                        ConversationParticipant.user_id != user_id,
                    )
                )
            ).scalars().all()
            other = others[0] if others else None
            last = (
                await self.db.execute(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.conversation_id == conv.id,
                        ConversationMessage.deleted_at.is_(None),
                    )
                    .order_by(ConversationMessage.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            unread = 0
            if p.last_read_at:
                unread = int(
                    (
                        await self.db.execute(
                            select(func.count())
                            .select_from(ConversationMessage)
                            .where(
                                ConversationMessage.conversation_id == conv.id,
                                ConversationMessage.sender_user_id != user_id,
                                ConversationMessage.created_at > p.last_read_at,
                                ConversationMessage.deleted_at.is_(None),
                            )
                        )
                    ).scalar_one()
                )
            else:
                unread = int(
                    (
                        await self.db.execute(
                            select(func.count())
                            .select_from(ConversationMessage)
                            .where(
                                ConversationMessage.conversation_id == conv.id,
                                ConversationMessage.sender_user_id != user_id,
                                ConversationMessage.deleted_at.is_(None),
                            )
                        )
                    ).scalar_one()
                )
            out.append(
                ConversationOut(
                    id=conv.id,
                    school_id=conv.school_id,
                    conversation_type=conv.conversation_type,
                    subject=conv.subject,
                    related_student_id=conv.related_student_id,
                    other_user_id=other.user_id if other else None,
                    other_user_name=await self._user_name(other.user_id) if other else None,
                    last_message=last.message if last else None,
                    last_message_at=last.created_at if last else None,
                    unread_count=unread,
                    updated_at=conv.updated_at,
                )
            )
        out.sort(key=lambda c: c.last_message_at or c.updated_at, reverse=True)
        return out

    async def start_conversation(
        self, school_id: UUID, user_id: UUID, role: str, data: ConversationCreate
    ) -> ConversationOut:
        await self._assert_can_message(
            school_id, user_id, role, data.participant_user_id, data.related_student_id
        )
        # Reuse existing 1:1 conversation if present
        existing = await self._find_direct_conversation(school_id, user_id, data.participant_user_id)
        if existing:
            await self.send_message(school_id, user_id, existing.id, MessageCreate(message=data.initial_message))
            convs = await self.list_conversations(school_id, user_id)
            return next(c for c in convs if c.id == existing.id)

        conv = Conversation(
            school_id=school_id,
            conversation_type=ConversationType.DIRECT.value,
            subject=data.subject,
            related_student_id=data.related_student_id,
        )
        self.db.add(conv)
        await self.db.flush()
        for uid in (user_id, data.participant_user_id):
            self.db.add(
                ConversationParticipant(
                    school_id=school_id,
                    conversation_id=conv.id,
                    user_id=uid,
                    joined_at=_utcnow(),
                )
            )
        msg = ConversationMessage(
            school_id=school_id,
            conversation_id=conv.id,
            sender_user_id=user_id,
            message=data.initial_message,
            message_type="TEXT",
        )
        self.db.add(msg)
        await create_in_app_notification(
            self.db,
            school_id=school_id,
            user_id=data.participant_user_id,
            title="New message",
            body=data.initial_message[:200],
            category=NotificationType.MESSAGE_RECEIVED.value,
            link_path=f"/messages/{conv.id}",
            entity_type="conversation",
            entity_id=conv.id,
            event_key=f"MESSAGE:{msg.id}:{data.participant_user_id}",
        )
        await self._audit(school_id, user_id, "MESSAGE_SENT", "conversation", conv.id)
        await self.db.commit()
        convs = await self.list_conversations(school_id, user_id)
        return next(c for c in convs if c.id == conv.id)

    async def _find_direct_conversation(
        self, school_id: UUID, user_a: UUID, user_b: UUID
    ) -> Conversation | None:
        parts_a = (
            await self.db.execute(
                select(ConversationParticipant.conversation_id).where(
                    ConversationParticipant.school_id == school_id,
                    ConversationParticipant.user_id == user_a,
                )
            )
        ).scalars().all()
        if not parts_a:
            return None
        for cid in parts_a:
            other = (
                await self.db.execute(
                    select(ConversationParticipant).where(
                        ConversationParticipant.conversation_id == cid,
                        ConversationParticipant.user_id == user_b,
                    )
                )
            ).scalar_one_or_none()
            if other:
                return await self.db.get(Conversation, cid)
        return None

    async def list_messages(
        self, school_id: UUID, user_id: UUID, conversation_id: UUID
    ) -> list[MessageOut]:
        await self._assert_participant(school_id, user_id, conversation_id)
        rows = (
            await self.db.execute(
                select(ConversationMessage)
                .where(
                    ConversationMessage.conversation_id == conversation_id,
                    ConversationMessage.deleted_at.is_(None),
                )
                .order_by(ConversationMessage.created_at.asc())
            )
        ).scalars().all()
        # mark read
        part = (
            await self.db.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conversation_id,
                    ConversationParticipant.user_id == user_id,
                )
            )
        ).scalar_one()
        part.last_read_at = _utcnow()
        await self.db.commit()
        return [
            MessageOut(
                id=m.id,
                conversation_id=m.conversation_id,
                sender_user_id=m.sender_user_id,
                sender_name=await self._user_name(m.sender_user_id),
                message=m.message,
                message_type=m.message_type,
                created_at=m.created_at,
                deleted_at=m.deleted_at,
            )
            for m in rows
        ]

    async def send_message(
        self, school_id: UUID, user_id: UUID, conversation_id: UUID, data: MessageCreate
    ) -> MessageOut:
        await self._assert_participant(school_id, user_id, conversation_id)
        msg = ConversationMessage(
            school_id=school_id,
            conversation_id=conversation_id,
            sender_user_id=user_id,
            message=data.message,
            message_type="TEXT",
        )
        self.db.add(msg)
        await self.db.flush()
        others = (
            await self.db.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conversation_id,
                    ConversationParticipant.user_id != user_id,
                )
            )
        ).scalars().all()
        sender_name = await self._user_name(user_id) or "Someone"
        for other in others:
            await create_in_app_notification(
                self.db,
                school_id=school_id,
                user_id=other.user_id,
                title=f"New message from {sender_name}",
                body=data.message[:200],
                category=NotificationType.MESSAGE_RECEIVED.value,
                link_path=f"/messages/{conversation_id}",
                entity_type="conversation",
                entity_id=conversation_id,
                event_key=f"MESSAGE:{msg.id}:{other.user_id}",
            )
        await self._audit(school_id, user_id, "MESSAGE_SENT", "message", msg.id)
        await self.db.commit()
        await self.db.refresh(msg)
        return MessageOut(
            id=msg.id,
            conversation_id=msg.conversation_id,
            sender_user_id=msg.sender_user_id,
            sender_name=sender_name,
            message=msg.message,
            message_type=msg.message_type,
            created_at=msg.created_at,
            deleted_at=msg.deleted_at,
        )

    async def _assert_participant(
        self, school_id: UUID, user_id: UUID, conversation_id: UUID
    ) -> Conversation:
        conv = await self.db.get(Conversation, conversation_id)
        if not conv or conv.school_id != school_id:
            raise NotFoundError("Conversation not found")
        part = (
            await self.db.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conversation_id,
                    ConversationParticipant.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if not part:
            raise ForbiddenError("Not a participant")
        return conv

    async def _assert_can_message(
        self,
        school_id: UUID,
        sender_user_id: UUID,
        sender_role: str,
        recipient_user_id: UUID,
        related_student_id: UUID | None,
    ) -> None:
        if sender_user_id == recipient_user_id:
            raise AppError("Cannot message yourself", status_code=422)

        if sender_role == UserRole.PARENT.value:
            parent = (
                await self.db.execute(
                    select(Parent).where(
                        Parent.school_id == school_id, Parent.user_id == sender_user_id
                    )
                )
            ).scalar_one_or_none()
            if not parent:
                raise ForbiddenError("Parent profile required")
            teacher = (
                await self.db.execute(
                    select(Teacher).where(
                        Teacher.school_id == school_id, Teacher.user_id == recipient_user_id
                    )
                )
            ).scalar_one_or_none()
            if not teacher:
                raise ForbiddenError("Can only message teachers")
            # Must share a child in teacher's assigned class
            links = (
                await self.db.execute(
                    select(StudentParent).where(
                        StudentParent.school_id == school_id,
                        StudentParent.parent_id == parent.id,
                    )
                )
            ).scalars().all()
            child_ids = [l.student_id for l in links]
            if related_student_id and related_student_id not in child_ids:
                raise ForbiddenError("Student not linked to parent")
            ok = False
            for cid in child_ids if not related_student_id else [related_student_id]:
                enr = (
                    await self.db.execute(
                        select(Enrollment)
                        .where(
                            Enrollment.student_id == cid,
                            Enrollment.status == EnrollmentStatus.ACTIVE.value,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if not enr:
                    continue
                assigned = (
                    await self.db.execute(
                        select(TeacherAssignment).where(
                            TeacherAssignment.teacher_id == teacher.id,
                            TeacherAssignment.class_id == enr.class_id,
                        )
                    )
                ).scalar_one_or_none()
                if assigned:
                    ok = True
                    break
            if not ok:
                raise ForbiddenError("Teacher is not assigned to your child's class")
            return

        if sender_role == UserRole.TEACHER.value:
            teacher = (
                await self.db.execute(
                    select(Teacher).where(
                        Teacher.school_id == school_id, Teacher.user_id == sender_user_id
                    )
                )
            ).scalar_one_or_none()
            if not teacher:
                raise ForbiddenError("Teacher profile required")
            assignments = (
                await self.db.execute(
                    select(TeacherAssignment).where(TeacherAssignment.teacher_id == teacher.id)
                )
            ).scalars().all()
            class_ids = {a.class_id for a in assignments}
            # Parent of student in assigned class?
            parent = (
                await self.db.execute(
                    select(Parent).where(
                        Parent.school_id == school_id, Parent.user_id == recipient_user_id
                    )
                )
            ).scalar_one_or_none()
            if parent:
                links = (
                    await self.db.execute(
                        select(StudentParent).where(
                            StudentParent.school_id == school_id,
                            StudentParent.parent_id == parent.id,
                        )
                    )
                ).scalars().all()
                for link in links:
                    enr = (
                        await self.db.execute(
                            select(Enrollment)
                            .where(
                                Enrollment.student_id == link.student_id,
                                Enrollment.status == EnrollmentStatus.ACTIVE.value,
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if enr and enr.class_id in class_ids:
                        return
                raise ForbiddenError("Parent is not linked to your assigned students")
            # Student in assigned class?
            student = (
                await self.db.execute(
                    select(Student).where(
                        Student.school_id == school_id, Student.user_id == recipient_user_id
                    )
                )
            ).scalar_one_or_none()
            if student:
                enr = (
                    await self.db.execute(
                        select(Enrollment)
                        .where(
                            Enrollment.student_id == student.id,
                            Enrollment.status == EnrollmentStatus.ACTIVE.value,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if enr and enr.class_id in class_ids:
                    return
                raise ForbiddenError("Student is not in your assigned classes")
            raise ForbiddenError("Can only message assigned parents or students")

        if sender_role == UserRole.STUDENT.value:
            student = (
                await self.db.execute(
                    select(Student).where(
                        Student.school_id == school_id, Student.user_id == sender_user_id
                    )
                )
            ).scalar_one_or_none()
            if not student:
                raise ForbiddenError("Student profile required")
            teacher = (
                await self.db.execute(
                    select(Teacher).where(
                        Teacher.school_id == school_id, Teacher.user_id == recipient_user_id
                    )
                )
            ).scalar_one_or_none()
            if not teacher:
                raise ForbiddenError("Can only message assigned teachers")
            enr = (
                await self.db.execute(
                    select(Enrollment)
                    .where(
                        Enrollment.student_id == student.id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not enr:
                raise ForbiddenError("No active enrollment")
            assigned = (
                await self.db.execute(
                    select(TeacherAssignment).where(
                        TeacherAssignment.teacher_id == teacher.id,
                        TeacherAssignment.class_id == enr.class_id,
                    )
                )
            ).scalar_one_or_none()
            if not assigned:
                raise ForbiddenError("Teacher is not assigned to your class")
            return

        if sender_role == UserRole.SCHOOL_ADMIN.value:
            return

        raise ForbiddenError("Messaging not allowed for this role")

    async def list_message_contacts(
        self, school_id: UUID, user_id: UUID, role: str
    ) -> list[MessageContactOut]:
        contacts: list[MessageContactOut] = []
        seen: set[UUID] = set()

        async def add(
            *,
            contact_user_id: UUID,
            full_name: str | None,
            contact_role: str,
            detail: str | None = None,
            related_student_id: UUID | None = None,
        ) -> None:
            if contact_user_id == user_id or contact_user_id in seen:
                return
            seen.add(contact_user_id)
            contacts.append(
                MessageContactOut(
                    user_id=contact_user_id,
                    full_name=full_name or "User",
                    role=contact_role,
                    detail=detail,
                    related_student_id=related_student_id,
                )
            )

        if role == UserRole.STUDENT.value:
            student = (
                await self.db.execute(
                    select(Student).where(
                        Student.school_id == school_id, Student.user_id == user_id
                    )
                )
            ).scalar_one_or_none()
            if not student:
                return []
            enr = (
                await self.db.execute(
                    select(Enrollment)
                    .where(
                        Enrollment.student_id == student.id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not enr:
                return []
            assignments = (
                await self.db.execute(
                    select(TeacherAssignment).where(TeacherAssignment.class_id == enr.class_id)
                )
            ).scalars().all()
            for a in assignments:
                teacher = await self.db.get(Teacher, a.teacher_id)
                if not teacher:
                    continue
                name = await self._user_name(teacher.user_id)
                await add(
                    contact_user_id=teacher.user_id,
                    full_name=name,
                    contact_role="TEACHER",
                    detail="Assigned teacher",
                )
            return contacts

        if role == UserRole.PARENT.value:
            parent = (
                await self.db.execute(
                    select(Parent).where(
                        Parent.school_id == school_id, Parent.user_id == user_id
                    )
                )
            ).scalar_one_or_none()
            if not parent:
                return []
            links = (
                await self.db.execute(
                    select(StudentParent).where(
                        StudentParent.school_id == school_id,
                        StudentParent.parent_id == parent.id,
                    )
                )
            ).scalars().all()
            for link in links:
                student = await self.db.get(Student, link.student_id)
                if not student:
                    continue
                enr = (
                    await self.db.execute(
                        select(Enrollment)
                        .where(
                            Enrollment.student_id == student.id,
                            Enrollment.status == EnrollmentStatus.ACTIVE.value,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if not enr:
                    continue
                classroom = await self.db.get(ClassRoom, enr.class_id)
                class_label = classroom.name if classroom else "Class"
                assignments = (
                    await self.db.execute(
                        select(TeacherAssignment).where(
                            TeacherAssignment.class_id == enr.class_id
                        )
                    )
                ).scalars().all()
                student_name = await self._user_name(student.user_id) or "Child"
                for a in assignments:
                    teacher = await self.db.get(Teacher, a.teacher_id)
                    if not teacher:
                        continue
                    name = await self._user_name(teacher.user_id)
                    await add(
                        contact_user_id=teacher.user_id,
                        full_name=name,
                        contact_role="TEACHER",
                        detail=f"{class_label} · {student_name}",
                        related_student_id=student.id,
                    )
            return contacts

        if role == UserRole.TEACHER.value:
            teacher = (
                await self.db.execute(
                    select(Teacher).where(
                        Teacher.school_id == school_id, Teacher.user_id == user_id
                    )
                )
            ).scalar_one_or_none()
            if not teacher:
                return []
            assignments = (
                await self.db.execute(
                    select(TeacherAssignment).where(TeacherAssignment.teacher_id == teacher.id)
                )
            ).scalars().all()
            class_ids = {a.class_id for a in assignments}
            if not class_ids:
                return []
            enrollments = (
                await self.db.execute(
                    select(Enrollment).where(
                        Enrollment.class_id.in_(class_ids),
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                )
            ).scalars().all()
            for enr in enrollments:
                student = await self.db.get(Student, enr.student_id)
                if not student:
                    continue
                classroom = await self.db.get(ClassRoom, enr.class_id)
                class_label = classroom.name if classroom else "Class"
                sname = await self._user_name(student.user_id)
                await add(
                    contact_user_id=student.user_id,
                    full_name=sname,
                    contact_role="STUDENT",
                    detail=class_label,
                    related_student_id=student.id,
                )
                links = (
                    await self.db.execute(
                        select(StudentParent).where(
                            StudentParent.school_id == school_id,
                            StudentParent.student_id == student.id,
                        )
                    )
                ).scalars().all()
                for link in links:
                    parent = await self.db.get(Parent, link.parent_id)
                    if not parent:
                        continue
                    pname = parent.full_name or await self._user_name(parent.user_id)
                    await add(
                        contact_user_id=parent.user_id,
                        full_name=pname,
                        contact_role="PARENT",
                        detail=f"Parent of {sname or 'student'}",
                        related_student_id=student.id,
                    )
            return contacts

        if role == UserRole.SCHOOL_ADMIN.value:
            teachers = (
                await self.db.execute(select(Teacher).where(Teacher.school_id == school_id))
            ).scalars().all()
            for t in teachers:
                name = await self._user_name(t.user_id)
                await add(
                    contact_user_id=t.user_id,
                    full_name=name,
                    contact_role="TEACHER",
                    detail=t.employee_code or t.teacher_code,
                )
            parents = (
                await self.db.execute(select(Parent).where(Parent.school_id == school_id))
            ).scalars().all()
            for p in parents:
                pname = p.full_name or await self._user_name(p.user_id)
                await add(
                    contact_user_id=p.user_id,
                    full_name=pname,
                    contact_role="PARENT",
                    detail=p.email,
                )
            students = (
                await self.db.execute(select(Student).where(Student.school_id == school_id))
            ).scalars().all()
            for s in students:
                sname = await self._user_name(s.user_id)
                await add(
                    contact_user_id=s.user_id,
                    full_name=sname,
                    contact_role="STUDENT",
                    detail=s.admission_no,
                )
            return contacts

        return []

    async def stats(self, school_id: UUID) -> CommunicationStatsOut:
        ann_total = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(Announcement)
                    .where(Announcement.school_id == school_id)
                )
            ).scalar_one()
        )
        ann_active = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(Announcement)
                    .where(
                        Announcement.school_id == school_id,
                        Announcement.status == AnnouncementStatus.PUBLISHED.value,
                    )
                )
            ).scalar_one()
        )
        notif_sent = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.school_id == school_id)
                )
            ).scalar_one()
        )
        notif_unread = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(
                        Notification.school_id == school_id,
                        Notification.is_read.is_(False),
                    )
                )
            ).scalar_one()
        )
        convs = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(Conversation)
                    .where(Conversation.school_id == school_id)
                )
            ).scalar_one()
        )
        return CommunicationStatsOut(
            announcements_total=ann_total,
            announcements_active=ann_active,
            notifications_sent=notif_sent,
            notifications_unread=notif_unread,
            conversations=convs,
        )
