"""Mobile app: push-token registration."""

import pytest

from src.modules.devices.router import PushTokenIn

def test_push_token_requires_auth_and_validates(client):
    assert client.post("/api/v1/devices/push-token", json={"token": "ExponentPushToken[abc]", "platform": "ios"}).status_code == 401
    with pytest.raises(ValueError):
        PushTokenIn(token="ExponentPushToken[abc]", platform="symbian")
    assert PushTokenIn(token="ExponentPushToken[abc]", platform="android").provider == "expo"
