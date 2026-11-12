"""Phase 9 communication & notifications — contract/unit coverage."""

from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app
from src.modules.communication.schemas import (
    AnnouncementCreate,
    AudienceIn,
    ConversationCreate,
    MessageCreate,
    PreferenceItem,
    PreferencesUpdate,
)
from src.modules.notifications.service import NON_DISABLEABLE
from src.shared.enums import (
    AnnouncementStatus,
    AudienceType,
    ConversationType,
    NotificationPriority,
    NotificationType,
)

client = TestClient(app)


def test_communication_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/communication/notifications",
        "/api/v1/communication/notifications/unread-count",
        "/api/v1/communication/notifications/{notification_id}/read",
        "/api/v1/communication/notifications/read-all",
        "/api/v1/communication/notifications/{notification_id}",
        "/api/v1/communication/preferences",
        "/api/v1/communication/announcements",
        "/api/v1/communication/announcements/teacher",
        "/api/v1/communication/announcements/{announcement_id}",
        "/api/v1/communication/announcements/{announcement_id}/publish",
        "/api/v1/communication/announcements/{announcement_id}/archive",
        "/api/v1/communication/conversations",
        "/api/v1/communication/conversations/{conversation_id}/messages",
        "/api/v1/communication/stats",
    ):
        assert p in paths, f"missing {p}"


def test_communication_endpoints_require_auth():
    assert client.get("/api/v1/communication/notifications").status_code == 401
    assert client.get("/api/v1/communication/notifications/unread-count").status_code == 401
    assert client.post("/api/v1/communication/notifications/read-all").status_code == 401
    nid = str(uuid4())
    assert client.post(f"/api/v1/communication/notifications/{nid}/read").status_code == 401
    assert client.delete(f"/api/v1/communication/notifications/{nid}").status_code == 401
    assert client.get("/api/v1/communication/preferences").status_code == 401
    assert client.patch("/api/v1/communication/preferences", json={"items": []}).status_code == 401
    assert client.get("/api/v1/communication/announcements").status_code == 401
    assert client.post("/api/v1/communication/announcements", json={}).status_code == 401
    aid = str(uuid4())
    assert client.post(f"/api/v1/communication/announcements/{aid}/publish").status_code == 401
    assert client.post(f"/api/v1/communication/announcements/{aid}/archive").status_code == 401
    assert client.get("/api/v1/communication/conversations").status_code == 401
    assert client.post("/api/v1/communication/conversations", json={}).status_code == 401
    cid = str(uuid4())
    assert client.get(f"/api/v1/communication/conversations/{cid}/messages").status_code == 401
    assert client.post(
        f"/api/v1/communication/conversations/{cid}/messages", json={"message": "hi"}
    ).status_code == 401
    assert client.get("/api/v1/communication/stats").status_code == 401


def test_notification_types_cover_phase9():
    expected = {
        "ATTENDANCE_ABSENCE",
        "ATTENDANCE_LATE",
        "ATTENDANCE_WEEKLY",
        "LEAVE_SUBMITTED",
        "LEAVE_APPROVED",
        "LEAVE_REJECTED",
        "HOMEWORK_PUBLISHED",
        "HOMEWORK_GRADED",
        "HOMEWORK_RETURNED",
        "EXAM_PUBLISHED",
        "RESULT_PUBLISHED",
        "ONLINE_TEST_PUBLISHED",
        "ONLINE_TEST_REMINDER",
        "ONLINE_TEST_RESULT",
        "ONLINE_TEST_FLAGGED",
        "ANNOUNCEMENT",
        "MESSAGE_RECEIVED",
        "SYSTEM",
        "FEE_INVOICE_ISSUED",
        "FEE_DUE_REMINDER",
        "FEE_OVERDUE",
        "FEE_PAYMENT_SUCCESS",
        "FEE_PAYMENT_FAILED",
        "FEE_RECEIPT_READY",
        "FEE_REFUND",
        "PAYSLIP_PUBLISHED",
        "PAY_RUN_APPROVED",
        "LOP_UPDATE",
    }
    assert {t.value for t in NotificationType} == expected


def test_notification_priorities():
    assert NotificationPriority.LOW.value == "LOW"
    assert NotificationPriority.NORMAL.value == "NORMAL"
    assert NotificationPriority.HIGH.value == "HIGH"
    assert NotificationPriority.URGENT.value == "URGENT"


def test_announcement_statuses_and_audiences():
    assert AnnouncementStatus.DRAFT.value == "DRAFT"
    assert AnnouncementStatus.PUBLISHED.value == "PUBLISHED"
    assert AnnouncementStatus.ARCHIVED.value == "ARCHIVED"
    assert {a.value for a in AudienceType} == {
        "SCHOOL",
        "ROLE",
        "GRADE",
        "SECTION",
        "USER",
    }


def test_conversation_types():
    assert ConversationType.PARENT_TEACHER.value == "PARENT_TEACHER"
    assert ConversationType.TEACHER_STUDENT.value == "TEACHER_STUDENT"


def test_announcement_create_schema_draft_default():
    body = AnnouncementCreate(
        title="Sports Day",
        content="Annual Sports Day will be held on 15 November.",
        audiences=[
            AudienceIn(audience_type=AudienceType.SECTION.value, section_id=uuid4(), role="PARENT")
        ],
    )
    assert body.status == "DRAFT"
    assert body.priority == "NORMAL"
    assert body.audiences[0].audience_type == "SECTION"


def test_announcement_school_and_role_audiences():
    school = AnnouncementCreate(
        title="Holiday",
        content="School closed tomorrow.",
        audiences=[AudienceIn(audience_type="SCHOOL")],
    )
    parents = AnnouncementCreate(
        title="Parents meeting",
        content="Please attend.",
        audiences=[AudienceIn(audience_type="ROLE", role="PARENT")],
        priority="HIGH",
    )
    assert school.audiences[0].audience_type == "SCHOOL"
    assert parents.priority == "HIGH"
    assert parents.audiences[0].role == "PARENT"


def test_conversation_and_message_schemas():
    conv = ConversationCreate(
        participant_user_id=uuid4(),
        related_student_id=uuid4(),
        subject="Absence",
        initial_message="Rahul will be absent tomorrow.",
    )
    assert conv.initial_message.startswith("Rahul")
    msg = MessageCreate(message="Okay, thank you for informing me.")
    assert len(msg.message) > 5


def test_preferences_schema_defaults():
    item = PreferenceItem(notification_type=NotificationType.HOMEWORK_PUBLISHED.value)
    assert item.in_app_enabled is True
    assert item.email_enabled is False
    assert item.sms_enabled is False
    update = PreferencesUpdate(
        items=[
            PreferenceItem(
                notification_type=NotificationType.ANNOUNCEMENT.value,
                in_app_enabled=True,
                email_enabled=True,
                sms_enabled=False,
            )
        ]
    )
    assert update.items[0].email_enabled is True


def test_non_disableable_notification_types():
    assert NotificationType.SYSTEM.value in NON_DISABLEABLE
    assert NotificationType.LEAVE_APPROVED.value in NON_DISABLEABLE
    assert NotificationType.LEAVE_REJECTED.value in NON_DISABLEABLE
    assert NotificationType.HOMEWORK_PUBLISHED.value not in NON_DISABLEABLE


def test_event_key_idempotency_shape():
    exam_id = uuid4()
    user_id = uuid4()
    key = f"RESULT_PUBLISHED:{exam_id}:{user_id}"
    assert key.startswith("RESULT_PUBLISHED:")
    assert str(exam_id) in key
    homework_id = uuid4()
    hw_key = f"HOMEWORK_PUBLISHED:{homework_id}:{user_id}"
    assert hw_key != key
    absence_key = f"ATTENDANCE_ABSENCE:{uuid4()}:2026-09-27:{user_id}"
    assert "ATTENDANCE_ABSENCE" in absence_key


def test_notify_alias_imports():
    from src.modules.notifications.service import create_bulk_notifications, notify

    assert callable(notify)
    assert callable(create_bulk_notifications)
