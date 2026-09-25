"""Provider registry: ``PROVISIONER_PROVIDER`` -> implementation.

To add a provider, implement :class:`app.providers.base.Provider` in a new
module and add one entry to ``_FACTORIES``. Nothing else changes.
"""

from __future__ import annotations

from collections.abc import Callable

from app.config import ConfigError, Settings
from app.providers.base import Provider


def _aws(settings: Settings) -> Provider:
    from app.providers.aws import AwsProvider, AwsSettings

    return AwsProvider(
        AwsSettings.from_env(),
        nodeodm_image=settings.nodeodm_image,
        nodeodm_port=settings.nodeodm_port,
        nodeodm_args=settings.nodeodm_args,
    )


def _fixed(settings: Settings) -> Provider:
    from app.providers.fixed import FixedProvider

    return FixedProvider.from_env()


_FACTORIES: dict[str, Callable[[Settings], Provider]] = {
    "aws": _aws,
    "fixed": _fixed,
}

KNOWN_PROVIDERS = tuple(_FACTORIES)


def build_provider(settings: Settings) -> Provider | None:
    """The configured provider, or ``None`` when ``PROVISIONER_PROVIDER=none``."""
    name = (settings.provider or "none").lower()
    if name in ("", "none", "off", "disabled"):
        return None
    factory = _FACTORIES.get(name)
    if factory is None:
        raise ConfigError(f"PROVISIONER_PROVIDER={name!r} is not one of {', '.join(KNOWN_PROVIDERS)} or none")
    return factory(settings)
