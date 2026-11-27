from src.modules.communication.outreach import NOT_CONFIGURED, channel_provider_status
from src.shared.enums import NotificationChannel


def test_in_app_is_always_ready():
    ready, provider = channel_provider_status(NotificationChannel.IN_APP.value)
    assert ready is True
    assert provider == "in_app"


def test_email_and_sms_are_not_configured():
    email_ready, _ = channel_provider_status(NotificationChannel.EMAIL.value)
    sms_ready, _ = channel_provider_status(NotificationChannel.SMS.value)
    assert email_ready is False
    assert sms_ready is False
    assert NOT_CONFIGURED == "not sent: provider not configured"
