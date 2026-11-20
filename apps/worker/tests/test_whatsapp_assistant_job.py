import json

from src.jobs.whatsapp_assistant import handle


def test_worker_forwards_the_body_and_does_not_log_it(monkeypatch, caplog):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{}"

    def urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = request.data
        captured["token_header"] = request.get_header("X-internal-token")
        return Response()

    monkeypatch.setattr("src.jobs.whatsapp_assistant.urllib.request.urlopen", urlopen)
    monkeypatch.setattr(
        "src.jobs.whatsapp_assistant.get_settings",
        lambda: type("S", (), {"core_api_url": "http://api.test", "core_internal_token": "internal-token"})(),
    )
    handle({"body": "{\"entry\":[]}"})
    assert captured["url"] == "http://api.test/internal/v1/whatsapp-assistant/inbound"
    assert json.loads(captured["body"])["body"] == "{\"entry\":[]}"
    assert captured["token_header"]
    assert "internal-token" not in caplog.text
