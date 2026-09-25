"""Provider-agnostic behaviour: handles, readiness probing, lifecycle verbs.

Uses a fake provider so nothing here depends on AWS. That is the point: a
second provider must be able to pass these tests untouched.
"""

from datetime import datetime, timezone

import pytest

from app import core as core_mod
from app.config import Settings
from app.core import Core, UnknownHandle, split_handle
from app.providers.base import ClassSpec, InstanceSpec, InstanceState, Provider, ProviderError


class FakeProvider(Provider):
    name = "fake"

    def __init__(self):
        self.created: list[InstanceSpec] = []
        self.destroyed: list[str] = []
        self.states: dict[str, InstanceState] = {}

    @property
    def classes(self):
        return {"cpu": ClassSpec("cpu", "small", 0.5), "cpu-large": ClassSpec("cpu-large", "big", 2.0)}

    def create(self, spec):
        self.created.append(spec)
        handle = f"h{len(self.created)}"
        self.states[handle] = InstanceState(handle=handle, status="pending", labels=dict(spec.labels),
                                            launched_at=datetime(2026, 9, 25, tzinfo=timezone.utc))
        return self.states[handle]

    def describe(self, handle):
        return self.states.get(handle) or InstanceState(handle=handle, status="terminated")

    def destroy(self, handle):
        self.destroyed.append(handle)
        self.states.pop(handle, None)

    def list_managed(self):
        return list(self.states.values())


@pytest.fixture
def setup():
    provider = FakeProvider()
    settings = Settings(provider="fake", nodeodm_port=3001, max_lifetime_seconds=3600)
    return Core(settings, provider), provider


def test_split_handle_requires_prefix():
    assert split_handle("aws:i-123") == ("aws", "i-123")
    with pytest.raises(UnknownHandle):
        split_handle("i-123")
    with pytest.raises(UnknownHandle):
        split_handle("aws:")


def test_describe_provider_advertises_classes(setup):
    core, _ = setup
    info = core.describe_provider()
    assert info["enabled"] is True
    assert info["provider"] == "fake"
    assert set(info["classes"]) == {"cpu", "cpu-large"}
    assert info["classes"]["cpu"]["hourly_cost"] == 0.5
    assert info["default_class"] == "cpu"
    assert info["nodeodm_port"] == 3001


def test_no_provider_is_disabled_not_an_error():
    core = Core(Settings(provider="none"), None)
    assert core.describe_provider()["enabled"] is False
    assert core.list_managed() == []
    with pytest.raises(ProviderError):
        core.create("cpu", "t" * 32, {}, None)


def test_create_prefixes_handle_and_passes_spec(setup):
    core, provider = setup
    out = core.create("cpu-large", "tok" * 8, {"task": "T1", "org": "acme"}, 99999)
    assert out["handle"] == "fake:h1"
    assert out["status"] == "pending"
    assert out["instance_class"] == "cpu-large"
    assert out["hourly_cost"] == 2.0
    assert out["port"] == 3001
    spec = provider.created[0]
    assert spec.token == "tok" * 8
    assert spec.port == 3001
    assert spec.labels == {"task": "T1", "org": "acme"}
    # lifetime is capped by the provisioner's own budget
    assert spec.max_lifetime_seconds == 3600


def test_create_defaults_class_and_rejects_unknown(setup):
    core, provider = setup
    core.create(None, "t" * 32, {}, None)
    assert provider.created[-1].instance_class == "cpu"
    with pytest.raises(ProviderError):
        core.create("gpu", "t" * 32, {}, None)


def test_describe_pending_until_nodeodm_answers(setup, monkeypatch):
    core, provider = setup
    core.create("cpu", "t" * 32, {}, None)
    # machine not up yet
    assert core.describe("fake:h1")["status"] == "pending"
    # machine running but NodeODM not answering -> still pending
    provider.states["h1"].status = "running"
    provider.states["h1"].public_ip = "203.0.113.5"
    monkeypatch.setattr(core_mod, "probe_nodeodm", lambda *a, **k: (False, "refused"))
    out = core.describe("fake:h1")
    assert out["status"] == "pending"
    assert out["hostname"] == "203.0.113.5"
    # NodeODM answers -> ready, endpoint reported
    seen = {}

    def probe(host, port, token, timeout):
        seen.update(host=host, port=port, token=token)
        return True, "ok"

    monkeypatch.setattr(core_mod, "probe_nodeodm", probe)
    out = core.describe("fake:h1", token="secret-token")
    assert out["status"] == "ready"
    assert out["hostname"] == "203.0.113.5" and out["port"] == 3001
    assert seen == {"host": "203.0.113.5", "port": 3001, "token": "secret-token"}


def test_describe_running_without_ip_is_pending(setup, monkeypatch):
    core, provider = setup
    core.create("cpu", "t" * 32, {}, None)
    provider.states["h1"].status = "running"
    monkeypatch.setattr(core_mod, "probe_nodeodm", lambda *a, **k: pytest.fail("must not probe without ip"))
    assert core.describe("fake:h1")["status"] == "pending"


def test_describe_unknown_handle_is_terminated(setup):
    core, _ = setup
    assert core.describe("fake:nope")["status"] == "terminated"


def test_handle_for_other_provider_is_rejected(setup):
    core, _ = setup
    with pytest.raises(UnknownHandle):
        core.describe("aws:i-1")
    with pytest.raises(UnknownHandle):
        core.destroy("aws:i-1")


def test_destroy_is_idempotent(setup):
    core, provider = setup
    core.create("cpu", "t" * 32, {}, None)
    assert core.destroy("fake:h1")["status"] == "terminated"
    assert core.destroy("fake:h1")["status"] == "terminated"
    assert provider.destroyed == ["h1", "h1"]


def test_list_managed_prefixes_handles(setup):
    core, _ = setup
    core.create("cpu", "t" * 32, {"task": "A"}, None)
    core.create("cpu", "t" * 32, {"task": "B"}, None)
    handles = {i["handle"] for i in core.list_managed()}
    assert handles == {"fake:h1", "fake:h2"}
    assert all(i["launched_at"].startswith("2026-09-25") for i in core.list_managed())


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def test_probe_semantics(monkeypatch):
    import requests

    calls = {}

    def fake_get(url, headers=None, timeout=None):
        calls["headers"] = headers
        return calls["resp"]

    monkeypatch.setattr(requests, "get", fake_get)
    # without token: any HTTP answer < 500 means NodeODM is up (401 included)
    calls["resp"] = _Resp(401, {"error": "Invalid authentication token"})
    assert core_mod.probe_nodeodm("h", 3000, None, 1)[0] is True
    assert calls["headers"] == {}
    # with token: must be an authenticated 200 with no error body
    calls["resp"] = _Resp(401, {"error": "Invalid authentication token"})
    assert core_mod.probe_nodeodm("h", 3000, "tok", 1)[0] is False
    calls["resp"] = _Resp(200, {"error": "Invalid authentication token"})
    assert core_mod.probe_nodeodm("h", 3000, "tok", 1)[0] is False
    calls["resp"] = _Resp(200, {"version": "2.2.4"})
    assert core_mod.probe_nodeodm("h", 3000, "tok", 1)[0] is True
    assert calls["headers"] == {"Authorization": "Bearer tok"}

    def boom(url, headers=None, timeout=None):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", boom)
    ready, detail = core_mod.probe_nodeodm("h", 3000, None, 1)
    assert ready is False and "not answering" in detail
