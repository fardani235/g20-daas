"""Artifact kinds: what a marketplace product delivers and how it is installed.

A kind is a small record of two callables: ``inspect(path) -> dict``
validates an artifact at publish time and returns the metadata stored on the
release (the normalized manifest for plugins), and ``install(release, org,
path) -> dict`` installs a verified local copy of the artifact into an
organization. Kinds without an installer are declared here so products can
name them, but ``WebODM Product`` refuses to publish one until an installer
exists — the catalog never lists something that cannot be acquired.

Adding a kind = adding an entry to ``KINDS``; no new tables.
"""

from dataclasses import dataclass
from typing import Callable

from webodm_core.plugins import package as package_mod


class KindError(ValueError):
    """The artifact does not satisfy its kind's rules."""


class NotInstallable(KindError):
    """The kind has no installer in this version."""


@dataclass(frozen=True)
class ArtifactKind:
    key: str
    label: str
    file_extensions: tuple[str, ...]
    inspect: Callable[[str], dict] | None = None
    install: Callable[..., dict] | None = None

    @property
    def installable(self) -> bool:
        return self.install is not None


def _inspect_plugin(path: str) -> dict:
    try:
        return package_mod.inspect_package(path)
    except package_mod.PackageError as e:
        raise KindError(f"invalid plugin package: {e}") from e


def _install_plugin(release, org: str, path: str) -> dict:
    """Install a plugin artifact for ``org`` through the user-plugin seam.

    ``path`` is a verified private copy that ``install_user_plugin`` consumes.
    Imported lazily: ``api.plugins`` imports frappe request machinery that
    this registry should not need just to be enumerated.
    """
    from webodm_core.api import plugins as plugins_api

    return plugins_api.install_user_plugin(
        path, org, source="Marketplace", product=release.product, release=release.name,
    )


def _inspect_generic(path: str) -> dict:
    return {}


KINDS: dict[str, ArtifactKind] = {
    "plugin": ArtifactKind("plugin", "Analysis plugin", (".zip",), _inspect_plugin, _install_plugin),
    "preset": ArtifactKind("preset", "Processing preset", (".json",), _inspect_generic, None),
    "basemap": ArtifactKind("basemap", "Basemap", (".json",), _inspect_generic, None),
    "model": ArtifactKind("model", "Model weights", (".zip", ".onnx"), _inspect_generic, None),
}


def get(kind: str) -> ArtifactKind:
    try:
        return KINDS[kind]
    except KeyError:
        raise KindError(f"unknown artifact kind '{kind}'") from None


def is_installable(kind: str) -> bool:
    return kind in KINDS and KINDS[kind].installable


def inspect(kind: str, path: str) -> dict:
    """Validate ``path`` as an artifact of ``kind``; return release metadata."""
    k = get(kind)
    return k.inspect(path) if k.inspect else {}


def install(kind: str, release, org: str, path: str) -> dict:
    k = get(kind)
    if not k.install:
        raise NotInstallable(f"{k.label} products cannot be installed yet")
    return k.install(release, org, path)


def label(kind: str) -> str:
    return KINDS[kind].label if kind in KINDS else kind
