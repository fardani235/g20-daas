"""HTTP contract the Frappe app depends on, exercised with the fixed provider."""

import pytest
from fastapi.testclient import TestClient

from app import core as core_mod
from app.config import Settings
from app.main import build_app
from app.providers.fixed import FixedProvider


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(core_mod, "probe_nodeodm", lambda *a, **k: (True, "ok"))
    settings = Settings(provider="fixed", nodeodm_port=3000, max_lifetime_seconds=600)
    app = build_app(settings, FixedProvider("nodeodm:3000"))
    return TestClient(app)


def test_health_and_provider(client):
    assert client.get("/health").json()["provider"] == "fixed"
    info = client.get("/provider").json()
    assert info["enabled"] is True
    assert "cpu" in info["classes"]
    assert info["max_lifetime_seconds"] == 600


def test_full_lifecycle(client):
    r = client.post("/instances", json={"instance_class": "cpu", "token": "t" * 32,
                                         "labels": {"task": "T1", "org": "acme"}})
    assert r.status_code == 201, r.text
    body = r.json()
    handle = body["handle"]
    assert handle.startswith("fixed:")
    assert body["status"] == "pending"

    listing = client.get("/instances").json()["instances"]
    assert [i["handle"] for i in listing] == [handle]
    assert listing[0]["labels"]["task"] == "T1"

    d = client.get(f"/instances/{handle}", headers={"X-Node-Token": "t" * 32}).json()
    assert d["status"] == "ready"
    assert d["hostname"] == "nodeodm" and d["port"] == 3000

    assert client.delete(f"/instances/{handle}").json()["status"] == "terminated"
    assert client.get(f"/instances/{handle}").json()["status"] == "terminated"
    assert client.get("/instances").json()["instances"] == []


def test_validation_errors(client):
    assert client.post("/instances", json={"token": "short"}).status_code == 422
    r = client.post("/instances", json={"token": "t" * 32, "instance_class": "gpu"})
    assert r.status_code == 400
    assert "unknown instance class" in r.json()["detail"]
    assert client.get("/instances/aws:i-123").status_code == 404
    assert client.get("/instances/garbage").status_code == 404


def test_disabled_provider(monkeypatch):
    app = build_app(Settings(provider="none"), None)
    c = TestClient(app)
    assert c.get("/provider").json()["enabled"] is False
    assert c.post("/instances", json={"token": "t" * 32}).status_code == 400
    assert c.get("/instances").json() == {"instances": []}


def test_api_token_required_when_configured(monkeypatch):
    monkeypatch.setattr(core_mod, "probe_nodeodm", lambda *a, **k: (True, "ok"))
    settings = Settings(provider="fixed", api_token="s3cret")
    c = TestClient(build_app(settings, FixedProvider("nodeodm:3000")))
    assert c.get("/health").status_code == 200  # health stays open for docker healthchecks
    assert c.get("/provider").status_code == 401
    assert c.get("/provider", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/provider", headers={"Authorization": "Bearer s3cret"}).status_code == 200
