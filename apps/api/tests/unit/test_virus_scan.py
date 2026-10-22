import socket
import struct
import threading

import pytest

from src.core import storage, virus_scan
from src.core.config import get_settings
from src.core.exceptions import ValidationError


def test_reply_parsing():
    assert virus_scan.parse_clamd_reply("stream: OK\x00") == (True, None)
    assert virus_scan.parse_clamd_reply("stream: Eicar-Test-Signature FOUND\x00") == (False, "Eicar-Test-Signature")
    with pytest.raises(ValueError):
        virus_scan.parse_clamd_reply("INSTREAM size limit exceeded. ERROR")


def _fake_clamd(reply: bytes):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    got = {}

    def run():
        c, _ = srv.accept()
        buf = b""
        while not buf.endswith(struct.pack("!I", 0)):
            part = c.recv(65536)
            if not part:
                break
            buf += part
        got["buf"] = buf
        c.sendall(reply)
        c.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1], got


def _on(monkeypatch, port=None, fail_closed=True, provider="clamav"):
    s = get_settings()
    monkeypatch.setattr(s, "virus_scan_provider", provider)
    monkeypatch.setattr(s, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(s, "clamav_port", port or 1)
    monkeypatch.setattr(s, "virus_scan_fail_closed", fail_closed)
    monkeypatch.setattr(s, "clamav_timeout_seconds", 3)


def test_off_by_default():
    virus_scan.scan_bytes(b"anything")  # no provider set: nothing happens


def test_clean_and_infected(monkeypatch):
    port, got = _fake_clamd(b"stream: OK\x00")
    _on(monkeypatch, port)
    virus_scan.scan_bytes(b"hello world")
    assert b"\x00\x00\x00\x0bhello world" in got["buf"]
    port, _ = _fake_clamd(b"stream: Eicar-Test-Signature FOUND\x00")
    _on(monkeypatch, port)
    with pytest.raises(ValidationError):
        virus_scan.scan_bytes(b"X5O!P%@AP")


def test_unreachable_fail_closed_and_open(monkeypatch):
    _on(monkeypatch, 1, fail_closed=True)
    with pytest.raises(virus_scan.ScannerUnavailableError):
        virus_scan.scan_bytes(b"x")
    _on(monkeypatch, 1, fail_closed=False)
    virus_scan.scan_bytes(b"x")


def test_infected_never_reaches_storage_and_delete_object(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "_MEDIA_ROOT", tmp_path)
    s = get_settings()
    monkeypatch.setattr(s, "s3_bucket", "")
    key = storage.put_object(key="schools/a/x.txt", data=b"fine")
    assert (tmp_path / key).is_file()
    port, _ = _fake_clamd(b"stream: Bad FOUND\x00")
    _on(monkeypatch, port)
    with pytest.raises(ValidationError):
        storage.put_object(key="schools/a/bad.txt", data=b"bad")
    assert not (tmp_path / "schools/a/bad.txt").exists()
    assert storage.delete_object(key) is True and not (tmp_path / key).exists()
    assert storage.delete_object(key) is True and storage.delete_object(None) is True
    assert storage.delete_object("../etc/passwd") is False
