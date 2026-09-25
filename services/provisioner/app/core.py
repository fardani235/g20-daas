"""Provider-agnostic core: handles, readiness, lifecycle verbs.

The core owns everything that is the same for every provider:

* **Handles.** ``<provider>:<provider id>`` so the app can store one opaque
  string and the provisioner can route it back without state.
* **Readiness.** A node is ready only once NodeODM actually answers on its
  public endpoint (``GET /info``). No agent runs on the box; the core probes.
  With the per-run token supplied (``X-Node-Token`` on describe) the probe
  requires an authenticated 200; without it, any HTTP answer from the port
  (including 401) proves NodeODM is up.
* **Statelessness.** Nothing is remembered between calls. The app's
  ``WebODM Compute Instance`` records are the lifecycle of record.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime

import requests

from app.config import Settings
from app.providers.base import InstanceSpec, InstanceState, Provider, ProviderError

log = logging.getLogger("provisioner")


class UnknownHandle(ProviderError):
    pass


@dataclass
class Endpoint:
    hostname: str
    port: int


def split_handle(handle: str) -> tuple[str, str]:
    provider, sep, raw = (handle or "").partition(":")
    if not sep or not provider or not raw:
        raise UnknownHandle(f"malformed handle {handle!r}")
    return provider, raw


def probe_nodeodm(hostname: str, port: int, token: str | None, timeout: float) -> tuple[bool, str]:
    """``(ready, detail)`` after one ``GET /info`` against the node."""
    url = f"http://{hostname}:{port}/info"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        return False, f"nodeodm not answering: {e.__class__.__name__}"
    if token:
        if resp.status_code != 200:
            return False, f"nodeodm answered {resp.status_code} to authenticated /info"
        try:
            body = resp.json()
        except ValueError:
            return False, "nodeodm /info returned non-JSON"
        if isinstance(body, dict) and body.get("error"):
            return False, f"nodeodm rejected token: {body.get('error')}"
        return True, "nodeodm answered /info"
    return resp.status_code < 500, f"nodeodm answered {resp.status_code}"


class Core:
    def __init__(self, settings: Settings, provider: Provider | None):
        self.settings = settings
        self.provider = provider

    # -- introspection ----------------------------------------------------

    def describe_provider(self) -> dict:
        if self.provider is None:
            return {"enabled": False, "provider": None, "classes": {}, "default_class": None,
                    "nodeodm_port": self.settings.nodeodm_port,
                    "max_lifetime_seconds": self.settings.max_lifetime_seconds}
        return {
            "enabled": True,
            "provider": self.provider.name,
            "classes": {name: asdict(spec) for name, spec in self.provider.classes.items()},
            "default_class": self.provider.default_class,
            "nodeodm_port": self.settings.nodeodm_port,
            "max_lifetime_seconds": self.settings.max_lifetime_seconds,
        }

    def _require_provider(self) -> Provider:
        if self.provider is None:
            raise ProviderError("no compute provider configured (PROVISIONER_PROVIDER=none)")
        return self.provider

    def _route(self, handle: str) -> tuple[Provider, str]:
        provider = self._require_provider()
        name, raw = split_handle(handle)
        if name != provider.name:
            raise UnknownHandle(f"handle {handle!r} belongs to provider {name!r}, configured is {provider.name!r}")
        return provider, raw

    def _public(self, state: InstanceState, provider: Provider) -> dict:
        return {
            "handle": f"{provider.name}:{state.handle}",
            "provider": provider.name,
            "status": state.status,
            "hostname": state.public_ip,
            "port": self.settings.nodeodm_port,
            "launched_at": _iso(state.launched_at),
            "labels": dict(state.labels),
            "detail": state.detail,
        }

    # -- verbs --------------------------------------------------------------

    def create(self, instance_class: str | None, token: str, labels: dict, max_lifetime_seconds: int | None) -> dict:
        provider = self._require_provider()
        cls_name = instance_class or provider.default_class
        cls = provider.classes.get(cls_name)
        if cls is None:
            raise ProviderError(f"unknown instance class {cls_name!r}; offered: {', '.join(provider.classes)}")
        lifetime = min(int(max_lifetime_seconds or self.settings.max_lifetime_seconds), self.settings.max_lifetime_seconds)
        spec = InstanceSpec(
            instance_class=cls_name, token=token, port=self.settings.nodeodm_port,
            max_lifetime_seconds=lifetime, labels={str(k): str(v) for k, v in (labels or {}).items()},
        )
        state = provider.create(spec)
        log.info("created %s:%s class=%s labels=%s", provider.name, state.handle, cls_name, spec.labels)
        out = self._public(state, provider)
        out.update({"instance_class": cls_name, "hourly_cost": cls.hourly_cost, "status": "pending"})
        return out

    def describe(self, handle: str, token: str | None = None) -> dict:
        provider, raw = self._route(handle)
        state = provider.describe(raw)
        out = self._public(state, provider)
        if state.status == "running":
            if state.public_ip:
                ready, detail = probe_nodeodm(state.public_ip, self.settings.nodeodm_port, token,
                                              self.settings.probe_timeout_seconds)
                out["status"] = "ready" if ready else "pending"
                out["detail"] = detail
            else:
                out["status"] = "pending"
                out["detail"] = "running, no public address yet"
        return out

    def destroy(self, handle: str) -> dict:
        provider, raw = self._route(handle)
        provider.destroy(raw)
        log.info("destroyed %s", handle)
        return {"handle": handle, "status": "terminated"}

    def list_managed(self) -> list[dict]:
        if self.provider is None:
            return []
        return [self._public(s, self.provider) for s in self.provider.list_managed()]


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)
