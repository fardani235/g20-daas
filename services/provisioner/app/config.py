"""Provisioner configuration, from the environment only.

Every value is explicit config with a documented example (see
``docs/on-demand-processing/configuration.md``); there are no silent
defaults for anything that costs money or decides where a node runs (AMI,
instance types, subnet, security group). Secrets accept the ``*_FILE``
convention used across the stack so they can be mounted as Docker secrets.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


class ConfigError(ValueError):
    pass


def env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    """Read ``name``; if ``<name>_FILE`` is set, read the value from that file."""
    file_var = os.environ.get(f"{name}_FILE")
    if file_var:
        try:
            with open(file_var) as fh:
                return fh.read().strip()
        except OSError as e:
            raise ConfigError(f"{name}_FILE={file_var} is not readable: {e.__class__.__name__}") from None
    value = os.environ.get(name)
    if value is None or value == "":
        if required:
            raise ConfigError(f"{name} is required")
        return default
    return value


def env_int(name: str, default: int) -> int:
    raw = env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


def env_bool(name: str, default: bool) -> bool:
    raw = env(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_json(name: str, default):
    raw = env(name)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        raise ConfigError(f"{name} must be JSON, got {raw!r}") from None


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = env(name)
    if raw is None:
        return list(default or [])
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass
class Settings:
    provider: str = "none"
    api_token: str | None = None
    nodeodm_port: int = 3000
    nodeodm_image: str = "opendronemap/nodeodm:latest"
    nodeodm_args: list[str] = field(default_factory=list)
    max_lifetime_seconds: int = 12 * 3600
    probe_timeout_seconds: float = 5.0
    # Free-form provider settings; each provider reads what it needs.
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            provider=(env("PROVISIONER_PROVIDER", "none") or "none").strip().lower(),
            api_token=env("PROVISIONER_API_TOKEN"),
            nodeodm_port=env_int("PROVISIONER_NODEODM_PORT", 3000),
            nodeodm_image=env("PROVISIONER_NODEODM_IMAGE", "opendronemap/nodeodm:latest"),
            nodeodm_args=env_list("PROVISIONER_NODEODM_ARGS"),
            max_lifetime_seconds=env_int("PROVISIONER_MAX_LIFETIME_SECONDS", 12 * 3600),
            probe_timeout_seconds=float(env("PROVISIONER_PROBE_TIMEOUT_SECONDS", "5") or 5),
        )
