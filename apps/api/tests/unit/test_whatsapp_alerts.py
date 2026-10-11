"""Template rendering, audience, opt-out, quiet hours, webhooks, and SMS fallback."""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.core.jobs import jobs_backend
from src.modules.alerts.catalog import DEFAULT_TEMPLATES, SAMPLE_VALUES, meta_submission_payload, render_message, template_components
from src.modules.alerts.phone import is_opt_out_keyword, mask_phone, normalize_phone
from src.modules.alerts.policy import (
    choose_channel,
    delivery_timing,
    next_status,
    recipient_ids,
    service_window_open,
    should_sms_fallback,
)
from src.modules.alerts.webhooks import parse_meta_webhook, parse_msg91_webhook, verify_challenge, verify_meta_signature

ROOT = Path(__file__).resolve().parents[4]
IST = ZoneInfo("Asia/Kolkata")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_every_template_placeholder_matches_variables():
    assert len(DEFAULT_TEMPLATES) == 33
    keys = {seed.event_key for seed in DEFAULT_TEMPLATES}
    assert keys == {
        "student_absent",
        "student_late",
        "attendance_weekly_summary",
        "fee_due",
        "fee_overdue",
        "payment_receipt",
        "payslip_published",
        "pay_run_approved",
        "lop_update",
        "leave_approved",
        "announcement",
    }
    for seed in DEFAULT_TEMPLATES:
        numbers = {int(item) for item in re.findall(r"\{\{(\d+)\}\}", seed.body_text)}
        assert numbers == set(range(1, len(seed.variables) + 1))
        assert seed.sms_text.count("{#var#}") == len(seed.variables)
        assert seed.category == "UTILITY"
        assert seed.locale in {"en", "ta", "hi"}
        rendered = render_message(seed.body_text, seed.variables, SAMPLE_VALUES)
        assert "{{" not in rendered
        payload = meta_submission_payload(seed)
        assert payload["category"] == "UTILITY"
        assert payload["components"][0]["type"] == "BODY"


def test_template_rendering_uses_variable_order():
    seed = next(item for item in DEFAULT_TEMPLATES if item.event_key == "student_absent" and item.locale == "en")
    text = render_message(seed.sms_text, seed.variables, {"student_name": "Aarav", "date": "11 Oct", "school_name": "Sybeez"})
    assert text == "Aarav was absent on 11 Oct. - Sybeez"
    components = template_components(seed.variables, {"student_name": "Aarav", "date": "11 Oct", "school_name": "Sybeez"})
    assert components == [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": "Aarav"},
                {"type": "text", "text": "11 Oct"},
                {"type": "text", "text": "Sybeez"},
            ],
        }
    ]


def test_parents_receive_student_events_and_staff_receive_payroll():
    parents = ["parent"]
    staff = ["teacher"]
    assert recipient_ids("student_absent", parent_ids=parents, staff_ids=staff) == parents
    assert recipient_ids("fee_due", parent_ids=parents, staff_ids=staff) == parents
    assert recipient_ids("payslip_published", parent_ids=parents, staff_ids=staff) == staff
    assert recipient_ids("pay_run_approved", parent_ids=parents, staff_ids=staff) == staff
    assert recipient_ids("lop_update", parent_ids=parents, staff_ids=staff) == staff
    assert recipient_ids("leave_approved", parent_ids=parents, staff_ids=staff, student_context=False) == staff
    assert recipient_ids("leave_approved", parent_ids=parents, staff_ids=staff, student_context=True) == parents
    assert recipient_ids("announcement", parent_ids=parents, staff_ids=staff, explicit_ids=["both"]) == ["both"]


def test_opt_out_blocks_a_channel_even_when_the_user_opted_in():
    channel, fallback = choose_channel(
        whatsapp_school=True,
        sms_school=True,
        whatsapp_opt_in=True,
        sms_opt_in=True,
        whatsapp_opt_out=True,
        sms_opt_out=False,
        has_phone=True,
    )
    assert channel == "SMS"
    assert fallback is False
    channel, fallback = choose_channel(
        whatsapp_school=True,
        sms_school=True,
        whatsapp_opt_in=True,
        sms_opt_in=True,
        whatsapp_opt_out=False,
        sms_opt_out=True,
        has_phone=True,
    )
    assert channel == "WHATSAPP"
    assert fallback is False
    assert is_opt_out_keyword(" stop ")
    assert is_opt_out_keyword("OPT OUT")
    assert not is_opt_out_keyword("hello")
    assert normalize_phone("9876543210") == "919876543210"


def test_quiet_hours_queue_until_morning_except_urgent():
    evening = datetime(2026, 10, 11, 22, 30, tzinfo=IST)
    early = datetime(2026, 10, 11, 2, 15, tzinfo=IST)
    morning = datetime(2026, 10, 11, 10, 0, tzinfo=IST)
    start, end = time(21, 0), time(7, 0)
    status, when = delivery_timing(now=evening, start=start, end=end, tz_name="Asia/Kolkata", urgent=False)
    assert status == "SCHEDULED"
    local = when.astimezone(IST)
    assert (local.day, local.hour, local.minute) == (12, 7, 0)
    status, when = delivery_timing(now=early, start=start, end=end, tz_name="Asia/Kolkata", urgent=False)
    assert status == "SCHEDULED"
    assert when.astimezone(IST).hour == 7
    assert when.astimezone(IST).day == 11
    status, when = delivery_timing(now=evening, start=start, end=end, tz_name="Asia/Kolkata", urgent=True)
    assert status == "PENDING"
    assert when is None
    status, when = delivery_timing(now=morning, start=start, end=end, tz_name="Asia/Kolkata", urgent=False)
    assert status == "PENDING"


def test_webhook_signature_and_status_transitions():
    body = b'{"entry":[]}'
    secret = "app-secret-value"
    import hashlib
    import hmac

    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_meta_signature(body, good, secret)
    assert not verify_meta_signature(body, "sha256=deadbeef", secret)
    assert not verify_meta_signature(body, good, "")
    assert verify_challenge("subscribe", "token", "token")
    assert not verify_challenge("subscribe", "nope", "token")
    assert next_status("PENDING", "SENT") == "SENT"
    assert next_status("SENT", "DELIVERED") == "DELIVERED"
    assert next_status("DELIVERED", "READ") == "READ"
    assert next_status("SENT", "READ") == "READ"
    assert next_status("READ", "DELIVERED") is None
    assert next_status("SENT", "FAILED") == "FAILED"
    assert next_status("FAILED", "SENT") is None
    assert next_status("PENDING", "SCHEDULED") == "SCHEDULED"
    statuses, inbound = parse_meta_webhook(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "statuses": [
                                    {"id": "wamid.1", "status": "delivered", "recipient_id": "9198"},
                                    {"id": "wamid.2", "status": "failed", "errors": [{"code": 131026, "title": "not on whatsapp"}]},
                                ],
                                "messages": [{"from": "919876543210", "id": "wamid.in", "type": "text", "text": {"body": "STOP"}}],
                            }
                        }
                    ]
                }
            ]
        }
    )
    assert [item.status for item in statuses] == ["DELIVERED", "FAILED"]
    assert statuses[1].error_code == "131026"
    assert inbound[0].opt_out is True
    updates = parse_msg91_webhook({"requestId": "req-1", "status": "DELIVERED", "number": "919876543210"})
    assert updates[0].status == "DELIVERED"
    assert updates[0].provider_message_id == "req-1"


def test_sms_fallback_when_whatsapp_fails():
    assert should_sms_fallback(channel="WHATSAPP", status="FAILED", sms_fallback_allowed=True, already_fallback=False)
    assert not should_sms_fallback(channel="WHATSAPP", status="FAILED", sms_fallback_allowed=False, already_fallback=False)
    assert not should_sms_fallback(channel="WHATSAPP", status="FAILED", sms_fallback_allowed=True, already_fallback=True)
    assert not should_sms_fallback(channel="SMS", status="FAILED", sms_fallback_allowed=True, already_fallback=False)
    assert not should_sms_fallback(
        channel="WHATSAPP", status="FAILED", sms_fallback_allowed=True, already_fallback=False, error_code="rate_limited"
    )
    assert not should_sms_fallback(channel="WHATSAPP", status="SENT", sms_fallback_allowed=True, already_fallback=False)


def test_service_window_and_phone_mask():
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    assert service_window_open(now - __import__("datetime").timedelta(hours=23), now)
    assert not service_window_open(now - __import__("datetime").timedelta(hours=25), now)
    assert not service_window_open(None, now)
    assert mask_phone("+919876543210") == "********3210"


def test_meta_cloud_is_the_default_template_sender_and_stubs_do_not_send():
    providers = _load(ROOT / "apps/worker/src/jobs/whatsapp_providers.py", "sybeez_wa_providers")
    captured = {}

    def post_json(url, headers, body):
        captured["url"] = url
        captured["body"] = body
        captured["auth"] = headers["Authorization"]
        return {"messages": [{"id": "wamid.abc"}]}

    sender = providers.get_whatsapp_provider(
        None, post_json=post_json, phone_number_id="123", access_token="token"
    )
    assert sender.name == "meta_cloud"
    message = providers.OutboundMessage(
        to="+919876543210",
        body="ignored",
        message_mode="template",
        template_name="sybeez_student_absent_en",
        language_code="en",
        components=[{"type": "body", "parameters": [{"type": "text", "text": "Aarav"}]}],
    )
    assert sender.send(message) == "wamid.abc"
    assert captured["body"]["type"] == "template"
    assert captured["body"]["template"]["name"] == "sybeez_student_absent_en"
    assert captured["auth"] == "Bearer token"
    with pytest.raises(providers.DeliveryRejected):
        providers.meta_request_body(
            providers.OutboundMessage(to="9198", body="hello", message_mode="text", service_window_open=False)
        )
    allowed = providers.meta_request_body(
        providers.OutboundMessage(to="9198", body="hello", message_mode="text", service_window_open=True)
    )
    assert allowed["type"] == "text"
    with pytest.raises(providers.DeliveryRejected):
        providers.GupshupProvider().send(message)
    with pytest.raises(providers.DeliveryRejected):
        providers.TwilioProvider().send(message)


def test_provider_rate_limit():
    limiter = _load(ROOT / "apps/worker/src/jobs/rate_limit.py", "sybeez_rate_limit")

    class FakeRedis:
        def __init__(self):
            self.values = {}

        def incr(self, key):
            self.values[key] = self.values.get(key, 0) + 1
            return self.values[key]

        def expire(self, key, ttl):
            return True

    redis = FakeRedis()
    assert limiter.allow_provider_send(redis, "meta_cloud", 2, now=1_700_000_000)
    assert limiter.allow_provider_send(redis, "meta_cloud", 2, now=1_700_000_000)
    assert not limiter.allow_provider_send(redis, "meta_cloud", 2, now=1_700_000_010)


def test_jobs_backend_reads_env(monkeypatch):
    monkeypatch.setenv("JOBS_BACKEND", "sqs")
    assert jobs_backend() == "sqs"
    monkeypatch.setenv("JOBS_BACKEND", "redis")
    assert jobs_backend() == "redis"


def test_docs_list_every_template_and_dlt_text():
    text = (ROOT / "apps/api/docs/WHATSAPP.md").read_text()
    for seed in DEFAULT_TEMPLATES:
        assert seed.whatsapp_template_name in text
        assert seed.body_text in text
        assert seed.sms_text in text
    assert "DeliveryLogView" in text
    assert "/api/v1/alerts/deliveries" in text
    assert "WHATSAPP_ACCESS_TOKEN" in text
    assert "MSG91_AUTH_KEY" in text
