from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import storage
from src.modules.schools.public import router


def test_logo_key_becomes_public_path():
    sid = uuid4()
    assert storage.school_logo_path(f"school-logos/{sid}.webp") == f"/api/v1/public/school-logos/{sid}"
    assert storage.school_logo_path("https://cdn.example/x.png") == "https://cdn.example/x.png"
    assert storage.school_logo_path(None) is None


def test_public_route_serves_stored_logo(monkeypatch):
    sid = uuid4()
    blobs = {f"school-logos/{sid}.png": b"\x89PNG-data"}

    def fake_read(key):
        if key in blobs:
            return blobs[key]
        raise FileNotFoundError(key)

    monkeypatch.setattr("src.modules.schools.public.read_object", fake_read)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    ok = client.get(f"/public/school-logos/{sid}")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png" and ok.content == blobs[f"school-logos/{sid}.png"]
    import pytest

    from src.core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        client.get(f"/public/school-logos/{uuid4()}")
    assert client.get("/public/school-logos/not-a-uuid").status_code == 422
