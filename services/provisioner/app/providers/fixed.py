"""``fixed`` provider: pretends to provision, always hands back one endpoint.

For development and tests only. It lets the whole lifecycle — Provisioning
state, readiness polling, dispatch to the handle's endpoint, release, the
sweep — run against the stack's own NodeODM container without any cloud
account, and it doubles as the proof that nothing above the provider seam
depends on AWS. It is *not* a fallback path for production: the app's static
processing node already covers "no provider configured".

Config: ``PROVISIONER_FIXED_ENDPOINT=host:port`` (e.g. ``nodeodm:3000``).
The token in the request is ignored: the fixed node runs without one unless
``PROVISIONER_FIXED_TOKEN`` says otherwise — that is why it is dev-only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.config import ConfigError, env
from app.providers.base import ClassSpec, InstanceSpec, InstanceState, Provider, ProviderError


class FixedProvider(Provider):
    name = "fixed"

    def __init__(self, endpoint: str, classes: list[str] | None = None):
        host, _, port = endpoint.rpartition(":")
        if not host or not port.isdigit():
            raise ConfigError("PROVISIONER_FIXED_ENDPOINT must be host:port")
        self.host, self.port = host, int(port)
        names = classes or ["cpu"]
        self._classes = {n: ClassSpec(name=n, flavor=f"fixed {endpoint}", hourly_cost=0.0,
                                      description="development endpoint") for n in names}
        # Handles issued by this process. Pretend-instances live until destroyed
        # or until the process restarts (the app's sweep tolerates both).
        self._live: dict[str, InstanceState] = {}

    @classmethod
    def from_env(cls) -> FixedProvider:
        endpoint = env("PROVISIONER_FIXED_ENDPOINT", required=True)
        classes = [c.strip() for c in (env("PROVISIONER_FIXED_CLASSES", "cpu") or "cpu").split(",") if c.strip()]
        return cls(endpoint, classes)

    @property
    def classes(self) -> dict[str, ClassSpec]:
        return self._classes

    def create(self, spec: InstanceSpec) -> InstanceState:
        if spec.instance_class not in self._classes:
            raise ProviderError(f"unknown instance class {spec.instance_class!r}")
        handle = uuid.uuid4().hex[:12]
        state = InstanceState(handle=handle, status="running", public_ip=self.host,
                              launched_at=datetime.now(timezone.utc), labels=dict(spec.labels))
        self._live[handle] = state
        return state

    def describe(self, handle: str) -> InstanceState:
        state = self._live.get(handle)
        if state is None:
            return InstanceState(handle=handle, status="terminated", detail="unknown handle")
        return state

    def destroy(self, handle: str) -> None:
        self._live.pop(handle, None)

    def list_managed(self) -> list[InstanceState]:
        return list(self._live.values())
