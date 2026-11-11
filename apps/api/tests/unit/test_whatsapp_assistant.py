"""Identity, scope, sending switch, and the WhatsApp webhook queue."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from src.core.config import Settings, get_settings
from src.db.models.whatsapp_assistant import WhatsAppMessage, WhatsAppSession, WhatsAppUsage
from src.modules.alerts.webhooks import InboundUpdate
from src.modules.whatsapp_assistant.conversation import (
    ChildChoice,
    SessionState,
    UNKNOWN_REPLY,
    decide_turn,
)
from src.modules.whatsapp_assistant.identity import (
    PhoneHit,
    ProfileChoice,
    hash_link_code,
    link_code_matches,
    matching_profiles,
    to_e164,
)
from src.modules.whatsapp_assistant.planner import ToolCall, _calls_from_message, allowed_tools, local_plan, plan_tools
from src.modules.whatsapp_assistant.scope import DataScope, build_tool_query, transcript_school
from src.modules.whatsapp_assistant.sender import deliver, sending_decision, text_payload
from src.modules.whatsapp_assistant.service import WhatsAppAssistant
from src.modules.whatsapp_assistant.tools import attendance_statement, homework_statement

SCHOOL_A = uuid.uuid4()
SCHOOL_B = uuid.uuid4()
PARENT = uuid.uuid4()
OTHER_PARENT = uuid.uuid4()
CHILD = uuid.uuid4()
OTHER_CHILD = uuid.uuid4()
CLASS_A = uuid.uuid4()
CLASS_B = uuid.uuid4()
TEACHER = uuid.uuid4()
EMPLOYEE = uuid.uuid4()


def _parent_scope(**overrides) -> DataScope:
    base = dict(
        user_id=PARENT,
        school_id=SCHOOL_A,
        role="PARENT",
        permissions=frozenset(
            {"attendance.read", "homework.read", "results.read", "timetable.read", "announcements.read", "ai.use"}
        ),
        student_ids=frozenset({CHILD}),
        class_ids=frozenset({CLASS_A}),
        selected_student_id=CHILD,
    )
    base.update(overrides)
    return DataScope(**base)


def _teacher_scope(**overrides) -> DataScope:
    base = dict(
        user_id=TEACHER,
        school_id=SCHOOL_A,
        role="TEACHER",
        permissions=frozenset({"attendance.read", "homework.read", "results.read", "timetable.read", "announcements.read", "ai.use"}),
        student_ids=frozenset(),
        class_ids=frozenset({CLASS_A}),
        employee_id=EMPLOYEE,
        teacher_id=uuid.uuid4(),
    )
    base.update(overrides)
    return DataScope(**base)


def test_e164_treats_indian_mobiles_and_keeps_other_country_codes():
    assert to_e164("+91 90921 10315") == "919092110315"
    assert to_e164("9092110315") == "919092110315"
    assert to_e164("919092110315") == "919092110315"
    assert to_e164("09092110315") == "919092110315"
    assert to_e164("00919092110315") == "919092110315"
    assert to_e164("+1 415 555 1212") == "14155551212"


def test_profile_phone_match_is_automatic_and_country_sensitive():
    user = uuid.uuid4()
    hits = [
        PhoneHit("+91 90921 10315", user, SCHOOL_A, "PARENT", "Priya — Parent — Green Valley"),
        PhoneHit("+1 415 555 1212", uuid.uuid4(), SCHOOL_B, "PARENT", "Someone else"),
    ]
    matched = matching_profiles("9092110315", hits)
    assert [item.user_id for item in matched] == [user]
    assert matching_profiles("4155551212", hits) == []
    assert matching_profiles("+1 415 555 1212", hits)[0].school_id == SCHOOL_B


def test_one_number_with_two_profiles_stays_two_profiles():
    user = uuid.uuid4()
    hits = [
        PhoneHit("919000000000", user, SCHOOL_A, "PARENT", "Priya — Parent — Green Valley"),
        PhoneHit("919000000000", user, SCHOOL_B, "TEACHER", "Priya — Teacher — Hill School"),
        PhoneHit("919000000000", uuid.uuid4(), SCHOOL_A, "STUDENT", "Aarav — Student — Green Valley", student_id=CHILD),
    ]
    matched = matching_profiles("+91 90000 00000", hits)
    assert len(matched) == 3


def test_unknown_number_gets_no_profile_and_no_phone_in_the_reply():
    decision = decide_turn("attendance", profiles=[], children=[], session=SessionState(), opted_out=False)
    assert decision.run_tools is False
    assert decision.kind == "unknown"
    assert decision.text == UNKNOWN_REPLY
    assert "90000" not in decision.text


def test_ambiguous_profile_and_child_are_asked_before_any_tool():
    parent = PhoneHit("919000000000", PARENT, SCHOOL_A, "PARENT", "Priya — Parent — Green Valley")
    teacher = PhoneHit("919000000000", TEACHER, SCHOOL_A, "TEACHER", "Priya — Teacher — Green Valley")
    profiles = matching_profiles("919000000000", [parent, teacher])
    first = decide_turn("show attendance", profiles=profiles, children=[], session=SessionState(), opted_out=False)
    assert first.kind == "ask_profile"
    assert first.run_tools is False
    assert "Green Valley" in first.text
    chosen = next(item for item in profiles if item.role == "PARENT")
    state = SessionState(
        pending="profile",
        options=first.options,
        pending_text=first.pending_text,
    )
    picked = decide_turn("1", profiles=profiles, children=[], session=state, opted_out=False)
    assert picked.profile.user_id == chosen.user_id
    children = [ChildChoice(CHILD, "Aarav — 5A"), ChildChoice(OTHER_CHILD, "Meera — 2B")]
    asked = decide_turn(
        "attendance",
        profiles=[chosen],
        children=children,
        session=SessionState(user_id=chosen.user_id, school_id=SCHOOL_A, role="PARENT"),
        opted_out=False,
    )
    assert asked.kind == "ask_child"
    assert asked.run_tools is False
    assert "Aarav" in asked.text and "Meera" in asked.text
    follow = decide_turn(
        "2",
        profiles=[chosen],
        children=children,
        session=SessionState(
            user_id=chosen.user_id,
            school_id=SCHOOL_A,
            role="PARENT",
            pending="child",
            options=asked.options,
            pending_text="attendance",
        ),
        opted_out=False,
    )
    assert follow.run_tools is True
    assert follow.child.student_id == OTHER_CHILD
    assert follow.text == "attendance"


def test_opt_out_does_not_read_data():
    profile = matching_profiles(
        "919000000000",
        [PhoneHit("919000000000", PARENT, SCHOOL_A, "PARENT", "Priya — Parent")],
    )
    stop = decide_turn("STOP", profiles=profile, children=[], session=SessionState(), opted_out=False)
    assert stop.run_tools is False
    assert stop.send is True
    silent = decide_turn("attendance", profiles=profile, children=[], session=SessionState(), opted_out=True)
    assert silent.run_tools is False
    assert silent.send is False


def test_link_code_is_keyed_and_not_the_raw_code():
    user = uuid.uuid4()
    stored = hash_link_code("123456", user, "server-secret")
    assert stored != "123456"
    assert link_code_matches("123456", user, "server-secret", stored)
    assert not link_code_matches("123456", uuid.uuid4(), "server-secret", stored)
    assert not link_code_matches("000000", user, "server-secret", stored)


def test_parent_cannot_read_another_child_or_another_school():
    denied = build_tool_query("attendance", _parent_scope(), {"student_id": str(OTHER_CHILD), "school_id": str(SCHOOL_B)})
    assert denied.denied
    allowed = build_tool_query("attendance", _parent_scope(), {"student_id": str(CHILD), "school_id": str(SCHOOL_B)})
    assert allowed.denied is None
    assert allowed.school_id == SCHOOL_A
    assert allowed.student_ids == frozenset({CHILD})
    params = list(attendance_statement(allowed).compile(dialect=postgresql.dialect()).params.values())
    flat = []
    for value in params:
        flat.extend(value if isinstance(value, (list, tuple, set)) else [value])
    assert SCHOOL_A in flat
    assert CHILD in flat
    assert SCHOOL_B not in flat
    assert OTHER_CHILD not in flat


def test_student_results_are_only_their_own_published_rows():
    student = uuid.uuid4()
    scope = DataScope(
        user_id=student,
        school_id=SCHOOL_A,
        role="STUDENT",
        permissions=frozenset({"results.read", "ai.use", "attendance.read"}),
        student_ids=frozenset({CHILD}),
        class_ids=frozenset({CLASS_A}),
        selected_student_id=CHILD,
    )
    query = build_tool_query("results", scope, {"student_id": str(OTHER_CHILD)})
    assert query.denied
    own = build_tool_query("results", scope, {})
    assert own.student_ids == frozenset({CHILD})
    assert own.published_only is True
    assert own.school_id == SCHOOL_A


def test_teacher_is_limited_to_assigned_classes_and_cannot_read_fees_or_other_payslips():
    scope = _teacher_scope()
    attendance = build_tool_query("attendance", scope, {"student_id": str(OTHER_CHILD), "school_id": str(SCHOOL_B)})
    assert attendance.school_id == SCHOOL_A
    assert attendance.class_ids == frozenset({CLASS_A})
    assert attendance.student_ids == frozenset({OTHER_CHILD})
    params = list(attendance_statement(attendance).compile(dialect=postgresql.dialect()).params.values())
    flat = []
    for value in params:
        flat.extend(value if isinstance(value, (list, tuple, set)) else [value])
    assert SCHOOL_B not in flat
    assert CLASS_B not in flat
    assert build_tool_query("fees_due", scope, {"student_id": str(CHILD)}).denied
    payslip = build_tool_query("payslips", scope, {"employee_name": "Other Teacher"})
    assert payslip.own_only is True
    assert payslip.employee_id == EMPLOYEE
    assert payslip.search_name is None
    leave = build_tool_query("leave_balance", scope, {"employee_name": "Other Teacher"})
    assert leave.own_only is True
    assert leave.user_id == TEACHER
    empty = build_tool_query("homework", _teacher_scope(class_ids=frozenset()), {})
    assert empty.denied


def test_finance_and_hr_stay_inside_their_school_permissions():
    finance = DataScope(
        user_id=uuid.uuid4(),
        school_id=SCHOOL_A,
        role="ACCOUNTANT",
        permissions=frozenset({"finance.read", "announcements.read"}),
        student_ids=frozenset(),
        class_ids=frozenset(),
    )
    fees = build_tool_query("fees_due", finance, {"school_id": str(SCHOOL_B)})
    assert fees.denied is None
    assert fees.school_id == SCHOOL_A
    assert fees.student_ids is None
    assert build_tool_query("attendance", finance, {}).denied
    hr = DataScope(
        user_id=uuid.uuid4(),
        school_id=SCHOOL_A,
        role="HR_MANAGER",
        permissions=frozenset({"hr.read", "payroll.read", "announcements.read"}),
        student_ids=frozenset(),
        class_ids=frozenset(),
        employee_id=EMPLOYEE,
    )
    named = build_tool_query("leave_balance", hr, {"employee_name": "Meera Iyer"})
    assert named.own_only is False
    assert named.school_id == SCHOOL_A
    own = build_tool_query("payslips", hr, {})
    assert own.own_only is True
    staff = DataScope(
        user_id=uuid.uuid4(),
        school_id=SCHOOL_A,
        role="STAFF",
        permissions=frozenset({"announcements.read"}),
        student_ids=frozenset(),
        class_ids=frozenset(),
        employee_id=EMPLOYEE,
    )
    assert build_tool_query("payslips", staff, {"employee_name": "Someone Else"}).own_only is True
    assert build_tool_query("fees_due", staff, {}).denied


def test_leave_requests_are_off_and_cannot_target_another_user():
    scope = _teacher_scope()
    blocked = build_tool_query(
        "leave_request",
        scope,
        {"user_id": str(OTHER_PARENT), "start_date": "2026-11-20", "end_date": "2026-11-21", "reason": "Family function"},
        leave_enabled=False,
    )
    assert blocked.denied
    opened = build_tool_query("leave_request", scope, {"user_id": str(OTHER_PARENT)}, leave_enabled=True)
    assert opened.denied is None
    assert opened.user_id == TEACHER
    assert opened.own_only is True
    assert opened.school_id == SCHOOL_A


def test_school_admin_cannot_point_a_transcript_query_at_another_school():
    with pytest.raises(PermissionError):
        transcript_school(actor_role="SCHOOL_ADMIN", actor_school=SCHOOL_A, requested=SCHOOL_B, is_platform_admin=False)
    assert transcript_school(actor_role="SCHOOL_ADMIN", actor_school=SCHOOL_A, requested=None, is_platform_admin=False) == SCHOOL_A
    assert transcript_school(actor_role="PLATFORM_ADMIN", actor_school=None, requested=SCHOOL_B, is_platform_admin=True) == SCHOOL_B
    assert transcript_school(actor_role="PLATFORM_ADMIN", actor_school=None, requested=None, is_platform_admin=True) is None


def test_sending_is_off_by_default_and_never_calls_meta_until_both_switches_are_on(monkeypatch):
    monkeypatch.delenv("WHATSAPP_SENDING_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.whatsapp_sending_enabled is False
    monkeypatch.setenv("WHATSAPP_SENDING_ENABLED", "false")
    assert Settings(_env_file=None).whatsapp_sending_enabled is False
    monkeypatch.setenv("WHATSAPP_SENDING_ENABLED", "true")
    assert Settings(_env_file=None).whatsapp_sending_enabled is True
    called = {"n": 0}

    def post(_payload):
        called["n"] += 1
        return "wamid"

    decision = sending_decision(
        sending_enabled=False,
        school_enabled=True,
        configured=True,
        may_notify=True,
        rate_ok=True,
        under_cap=True,
    )
    status, message_id = deliver(decision, text_payload("919000000000", "hello"), url="https://graph.facebook.com/v20.0/phone/messages", access_token="token", post=post)
    assert status == "suppressed"
    assert message_id is None
    assert called["n"] == 0
    school_off = sending_decision(
        sending_enabled=True,
        school_enabled=False,
        configured=True,
        may_notify=True,
        rate_ok=True,
        under_cap=True,
    )
    assert deliver(school_off, text_payload("919000000000", "hello"), url="https://example.test", access_token="token", post=post)[0] == "suppressed"
    assert called["n"] == 0
    allowed = sending_decision(
        sending_enabled=True,
        school_enabled=True,
        configured=True,
        may_notify=True,
        rate_ok=True,
        under_cap=True,
    )
    assert deliver(allowed, text_payload("919000000000", "hello"), url="https://example.test", access_token="token", post=post) == ("sent", "wamid")
    assert called["n"] == 1


def test_local_planner_picks_a_read_tool_and_drops_unknown_tools():
    assert local_plan("What is my attendance?", "AI assistant", include_leave=False) == [ToolCall("attendance", {})]
    assert local_plan("explain photosynthesis", "AI teacher", include_leave=False) == [ToolCall("lesson_material", {})]
    assert local_plan("request leave", "AI assistant", include_leave=False) == []
    dropped = _calls_from_message(
        {"tool_calls": [{"function": {"name": "delete_school", "arguments": "{\"school_id\": \"x\"}"}}]},
        allowed_tools(include_leave=False),
    )
    assert dropped == []


@pytest.mark.asyncio
async def test_ai_service_tool_call_is_filtered_and_failures_use_the_local_plan():
    async def post(_url, _headers, _body):
        return {"tool_calls": [{"name": "attendance", "arguments": {"student_id": str(OTHER_CHILD)}}, {"name": "drop_database", "arguments": {}}]}

    calls = await plan_tools(
        [{"role": "user", "content": "attendance"}],
        "AI assistant",
        include_leave=False,
        provider="ai_service",
        ai_service_url="http://ai.invalid",
        ai_service_token="test-token",
        post=post,
    )
    assert calls == [ToolCall("attendance", {"student_id": str(OTHER_CHILD)})]

    async def broken(_url, _headers, _body):
        raise RuntimeError("down")

    fallback = await plan_tools(
        [{"role": "user", "content": "show my homework"}],
        "AI assistant",
        include_leave=False,
        provider="ai_service",
        ai_service_url="http://ai.invalid",
        ai_service_token="test-token",
        post=broken,
    )
    assert fallback == [ToolCall("homework", {})]


@pytest.mark.asyncio
async def test_webhook_verifies_signature_and_queues_without_sending(monkeypatch):
    """Call the handlers directly so the suite's event loop is not closed by TestClient."""
    from src.core.exceptions import UnauthorizedError
    from src.modules.alerts.router import meta_verify, meta_webhook

    queued = []

    async def fake_enqueue(job_type, payload):
        queued.append((job_type, payload))

    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-app-secret")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "test-verify")
    monkeypatch.delenv("WHATSAPP_SENDING_ENABLED", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr("src.core.jobs.enqueue_job", fake_enqueue)
    body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
    good = "sha256=" + hmac.new(b"test-app-secret", body, hashlib.sha256).hexdigest()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    def make_request(signature: str):
        from starlette.requests import Request

        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/webhooks/whatsapp/meta",
                "headers": [(b"x-hub-signature-256", signature.encode())],
                "query_string": b"",
            },
            receive,
        )

    with pytest.raises(UnauthorizedError):
        await meta_webhook(make_request("sha256=deadbeef"))
    assert queued == []
    accepted = await meta_webhook(make_request(good))
    assert accepted == {"ok": True}
    assert queued[0][0] == "whatsapp_assistant"
    assert queued[0][1]["body"] == body.decode()
    challenge = await meta_verify(hub_mode="subscribe", hub_verify_token="test-verify", hub_challenge="12345")
    assert challenge.body == b"12345"
    get_settings.cache_clear()


class _MemoryDb:
    """Records objects the assistant stores. Selects come back empty, which is a new month."""

    def __init__(self):
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def execute(self, _statement):
        return _EmptyResult()

    async def get(self, _model, _key):
        return None

    async def flush(self) -> None:
        return None


class _EmptyResult:
    def scalar_one_or_none(self):
        return None

    def first(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


def _statement_values(statement) -> tuple[str, list]:
    compiled = statement.compile(dialect=postgresql.dialect())
    values: list = []
    for value in compiled.params.values():
        if isinstance(value, (list, tuple, set, frozenset)):
            values.extend(value)
        else:
            values.append(value)
    return str(compiled), values


def test_parent_homework_query_excludes_drafts():
    query = build_tool_query("homework", _parent_scope(), {})
    assert query.denied is None
    assert query.role == "PARENT"
    sql, values = _statement_values(homework_statement(query))
    assert "homework.status" in sql
    assert "PUBLISHED" in values
    assert "DRAFT" not in values
    assert "DRAFT" not in sql


@pytest.mark.asyncio
async def test_first_message_of_the_month_from_a_known_parent_is_stored(monkeypatch):
    """A matched parent has a school, so the month's usage row is created on this message."""
    monkeypatch.setenv("WHATSAPP_SENDING_ENABLED", "false")
    get_settings.cache_clear()
    parent = ProfileChoice(PARENT, SCHOOL_A, "PARENT", "Priya — Parent — Green Valley")
    child = ChildChoice(CHILD, "Aarav — 5A")
    db = _MemoryDb()
    assistant = WhatsAppAssistant(db)

    async def profiles(_phone):
        return [parent]

    async def children(_user_id, _school_id):
        return [child]

    async def opted_out(_phone):
        return False

    monkeypatch.setattr(assistant, "_profiles", profiles)
    monkeypatch.setattr(assistant, "_children", children)
    monkeypatch.setattr(assistant, "_opted_out", opted_out)

    await assistant.handle_inbound(
        InboundUpdate(provider_message_id="wamid.first-of-month", from_phone="919000000000", body="hello", opt_out=False)
    )

    usage = [row for row in db.added if isinstance(row, WhatsAppUsage)]
    assert len(usage) == 1
    assert usage[0].school_id == SCHOOL_A
    assert usage[0].year_month
    assert usage[0].inbound_count == 1
    assert usage[0].generated_count == 1
    assert usage[0].sent_count == 0
    assert usage[0].suppressed_count == 1
    sessions = [row for row in db.added if isinstance(row, WhatsAppSession)]
    assert len(sessions) == 1
    assert sessions[0].school_id == SCHOOL_A
    assert sessions[0].user_id == PARENT
    assert sessions[0].role == "PARENT"
    messages = [row for row in db.added if isinstance(row, WhatsAppMessage)]
    assert [row.direction for row in messages] == ["in", "out"]
    assert messages[0].body == "hello"
    assert messages[0].user_id == PARENT
    assert messages[1].school_id == SCHOOL_A
    assert messages[1].body
    get_settings.cache_clear()
