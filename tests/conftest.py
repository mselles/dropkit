import os

# Must be set before the app is imported.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SCHEDULER_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from app import senders
from app.db import engine
from app.main import app


@pytest.fixture
def client():
    SQLModel.metadata.drop_all(engine)
    senders.outbox.clear()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def artist(client):
    r = client.post("/auth/signup", json={
        "email": "miles@example.com", "password": "supersecret", "name": "Test Artist", "handle": "testartist",
    })
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
