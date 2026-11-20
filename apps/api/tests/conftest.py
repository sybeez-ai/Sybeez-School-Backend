import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client():
    with TestClient(app, headers={"Origin": "http://testserver"}) as c:
        yield c
