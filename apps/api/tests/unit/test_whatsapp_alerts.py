"""Template rendering, audience, opt-out, quiet hours and webhooks. WhatsApp is the only channel."""

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
from src.modules.alerts.credentials import (
    WHATSAPP_NOT_CONFIGURED,
    effective_whatsapp_provider,
    provider_status,
    whatsapp_admin_status,
)
from src.modules.alerts.submit import submit_message_templates, template_payload
from src.modules.alerts.phone import is_opt_out_keyword, mask_phone, normalize_phone
from src.modules.alerts.policy import (
    choose_channel,
    delivery_timing,
    next_status,
    recipient_ids,
    service_window_open,
)
from src.modules.alerts.webhooks import parse_meta_webhook, verify_challenge, verify_meta_signature

ROOT = Path(__file__).resolve().parents[4]
IST = ZoneInfo("Asia/Kolkata")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_every_template_placeholder_matches_variables():
    assert len(DEFAULT_TEMPLATES) == 53
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
        "progress_update",
    }
    for seed in DEFAULT_TEMPLATES:
        numbers = {int(item) for item in re.findall(r"\{\{(\d+)\}\}", seed.body_text)}
        assert numbers == set(range(1, len(seed.variables) + 1))
        assert seed.category == "UTILITY"
        assert seed.locale in {"en", "ta", "hi", "te", "kn", "ml", "mr", "bn", "gu", "pa"}
        rendered = render_message(seed.body_text, seed.variables, SAMPLE_VALUES)
        assert "{{" not in rendered
        payload = meta_submission_payload(seed)
        assert payload["category"] == "UTILITY"
        assert payload["components"][0]["type"] == "BODY"


def test_template_rendering_uses_variable_order():
    seed = next(item for item in DEFAULT_TEMPLATES if item.event_key == "student_absent" and item.locale == "en")
    text = render_message(seed.body_text, seed.variables, {"student_name": "Aarav", "date": "11 Oct", "school_name": "Sybeez"})
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


def test_whatsapp_needs_school_switch_parent_opt_in_no_stop_and_a_phone():
    base = dict(whatsapp_school=True, whatsapp_opt_in=True, whatsapp_opt_out=False, has_phone=True)
    assert choose_channel(**base) == "WHATSAPP"
    assert choose_channel(**{**base, "whatsapp_opt_out": True}) is None
    assert choose_channel(**{**base, "whatsapp_opt_in": False}) is None
    assert choose_channel(**{**base, "whatsapp_school": False}) is None
    assert choose_channel(**{**base, "has_phone": False}) is None
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


def test_missing_credentials_stay_mock_with_admin_status():
    from types import SimpleNamespace

    blank = SimpleNamespace(
        whatsapp_provider="meta_cloud",
        whatsapp_cloud_access_token="",
        whatsapp_cloud_phone_number_id="",
        whatsapp_business_account_id="",
        whatsapp_app_secret="",
        whatsapp_verify_token="",
    )
    status = provider_status(blank)
    assert status["whatsapp_status"] == WHATSAPP_NOT_CONFIGURED
    assert whatsapp_admin_status(blank) == "WhatsApp not configured"
    assert effective_whatsapp_provider(blank) == "mock"
    assert "WHATSAPP_ACCESS_TOKEN" in status["whatsapp_missing"]
    assert "WHATSAPP_BUSINESS_ACCOUNT_ID" in status["whatsapp_missing"]

    ready = SimpleNamespace(
        **{
            **blank.__dict__,
            "whatsapp_cloud_access_token": "token",
            "whatsapp_cloud_phone_number_id": "phone",
            "whatsapp_business_account_id": "waba",
            "whatsapp_app_secret": "secret",
            "whatsapp_verify_token": "verify",
        }
    )
    assert provider_status(ready)["whatsapp_status"] == "configured"
    assert effective_whatsapp_provider(ready) == "meta_cloud"


def test_template_submit_skips_meta_when_unconfigured():
    payload = template_payload(
        name="sybeez_student_absent_en",
        language="en",
        body_text="{{1}} was absent on {{2}}. - {{3}}",
        variables=("student_name", "date", "school_name"),
    )
    calls = []

    def post_json(url, headers, body):
        calls.append(url)
        return {"id": "1", "status": "PENDING"}

    result = submit_message_templates(
        [payload],
        access_token="",
        waba_id="",
        post_json=post_json,
        dry_run=True,
    )
    assert result["status"] == "WhatsApp not configured"
    assert result["submitted"] == []
    assert result["payloads"][0]["name"] == "sybeez_student_absent_en"
    assert calls == []

    sent = submit_message_templates(
        [payload],
        access_token="token",
        waba_id="waba-1",
        post_json=post_json,
    )
    assert sent["status"] == "submitted"
    assert sent["submitted"][0]["id"] == "1"
    assert calls == ["https://graph.facebook.com/v20.0/waba-1/message_templates"]


def test_worker_downgrades_unconfigured_provider_to_mock():
    providers = _load(ROOT / "apps/worker/src/jobs/whatsapp_providers.py", "sybeez_whatsapp_providers_resolve")
    assert (
        providers.resolve_outbound_provider(
            "WHATSAPP",
            "meta_cloud",
            access_token="",
            phone_number_id="",
        )
        == "mock"
    )
    assert (
        providers.resolve_outbound_provider(
            "WHATSAPP",
            "meta_cloud",
            access_token="token",
            phone_number_id="phone",
        )
        == "meta_cloud"
    )


def test_settings_read_whatsapp_from_env(monkeypatch):
    from src.core.config import Settings, get_settings

    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "token-from-env")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "phone-from-env")
    monkeypatch.setenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "waba-from-env")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "secret-from-env")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-from-env")
    monkeypatch.delenv("ALERTS_SECRETS_ARN", raising=False)
    monkeypatch.delenv("WHATSAPP_SECRETS_ARN", raising=False)
    get_settings.cache_clear()
    settings = Settings()
    assert settings.whatsapp_cloud_access_token == "token-from-env"
    assert settings.whatsapp_cloud_phone_number_id == "phone-from-env"
    assert settings.whatsapp_business_account_id == "waba-from-env"
    assert settings.whatsapp_app_secret == "secret-from-env"
    assert settings.whatsapp_verify_token == "verify-from-env"
    assert provider_status(settings)["whatsapp_status"] == "configured"
    get_settings.cache_clear()


def test_docs_list_every_template_and_dlt_text():
    text = (ROOT / "apps/api/docs/WHATSAPP.md").read_text()
    for seed in DEFAULT_TEMPLATES:
        assert seed.whatsapp_template_name in text
        assert seed.body_text in text
    assert "DeliveryLogView" in text
    assert "/api/v1/alerts/deliveries" in text
    assert "WHATSAPP_ACCESS_TOKEN" in text
    assert "WHATSAPP_BUSINESS_ACCOUNT_ID" in text
    assert "WhatsApp not configured" in text
    assert "Sybis Technology Private Limited" in text
    assert "coexistence" in text
    assert "message_templates" in text
    assert "footer" in text
