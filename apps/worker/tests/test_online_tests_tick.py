import time

from src import main as worker_main


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


def _settings(monkeypatch):
    class S:
        core_api_url = "http://core.test"
        core_internal_token = "tok"

    monkeypatch.setattr(worker_main, "get_settings", lambda: S())


def test_tick_calls_internal_endpoint_with_token_and_respects_interval(monkeypatch):
    _settings(monkeypatch)
    seen = []

    def fake_open(request, timeout=0):
        seen.append((request.full_url, request.get_header("X-internal-token")))
        return _Resp()

    monkeypatch.setattr(worker_main.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(worker_main, "_last_online_tests", 0.0)
    worker_main._online_tests_tick()
    worker_main._online_tests_tick()  # inside the interval: no second call
    assert seen == [("http://core.test/internal/v1/online-tests/maintenance", "tok")]


def test_tick_never_raises_when_core_is_down(monkeypatch):
    _settings(monkeypatch)

    def boom(request, timeout=0):
        raise OSError("down")

    monkeypatch.setattr(worker_main.urllib.request, "urlopen", boom)
    monkeypatch.setattr(worker_main, "_last_online_tests", time.time() - 1000)
    worker_main._online_tests_tick()
