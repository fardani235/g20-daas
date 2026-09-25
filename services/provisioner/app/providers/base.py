"""The provider boundary.

A provider's whole job is to give us a machine that will run NodeODM and to
take it away again. Nothing above this interface knows or cares which cloud
produced the node: the core adds readiness probing, handle prefixing and the
HTTP API on top, and the app only ever sees handles and endpoints.

Adding a provider (vast.ai next) means implementing this ABC and registering
it in :mod:`app.providers` — no change to the core, the app, its data model
or the storage layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


class ProviderError(Exception):
    """The provider rejected the request (bad class, quota, invalid handle...)."""


class ProviderUnavailable(ProviderError):
    """The provider's API could not be reached. Usually transient."""


@dataclass(frozen=True)
class ClassSpec:
    """One instance class the provider offers, as advertised to the app.

    ``name`` is provider-neutral (``cpu``, ``cpu-large``, later ``gpu``);
    ``flavor`` is the provider's own name for it (an EC2 instance type, a
    vast.ai offer filter...). ``hourly_cost`` feeds the stored estimate only.
    """

    name: str
    flavor: str
    hourly_cost: float = 0.0
    description: str = ""


@dataclass
class InstanceSpec:
    """What the core asks a provider to create."""

    instance_class: str
    token: str                      # NodeODM bearer token baked in per run
    port: int                       # NodeODM port the node must expose
    max_lifetime_seconds: int       # budget; providers may use it for self-shutdown
    labels: dict[str, str] = field(default_factory=dict)  # task / org / site


@dataclass
class InstanceState:
    """What a provider reports about one instance.

    ``status`` is normalised to one of: ``pending`` (exists, not running yet),
    ``running`` (machine up; NodeODM readiness is the core's job),
    ``terminated`` (gone or going), ``failed`` (the provider says it will
    never run).
    """

    handle: str                     # provider-local id (no prefix)
    status: str
    public_ip: str | None = None
    launched_at: datetime | None = None
    labels: dict[str, str] = field(default_factory=dict)
    detail: str = ""


class Provider(ABC):
    """Create / describe / destroy / list. That is the whole contract."""

    #: short, stable name used as the handle prefix (``aws:i-0123``)
    name: str = "base"

    @property
    @abstractmethod
    def classes(self) -> dict[str, ClassSpec]:
        """Instance classes this provider can create, keyed by class name."""

    @property
    def default_class(self) -> str:
        return next(iter(self.classes))

    @abstractmethod
    def create(self, spec: InstanceSpec) -> InstanceState:
        """Launch an instance. Returns its state (at least ``handle``)."""

    @abstractmethod
    def describe(self, handle: str) -> InstanceState:
        """Current state of ``handle``. A vanished instance is ``terminated``."""

    @abstractmethod
    def destroy(self, handle: str) -> None:
        """Terminate ``handle``. Must be idempotent: an unknown handle is a no-op."""

    @abstractmethod
    def list_managed(self) -> list[InstanceState]:
        """Every non-terminated instance this deployment created (by tag/label)."""
