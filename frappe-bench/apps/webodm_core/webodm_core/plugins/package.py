"""Validation of uploaded user plugin packages.

A package is a zip with ``plugin.json`` at its root describing the plugin in the
same terms the geospatial catalog uses (label, version, inputs, params schema,
output kind, render kind), plus an ``entrypoint`` script. Everything here runs
at upload time, before a row is created, so a malformed or hostile package is
rejected with a clear message and never reaches the sandbox. The sandbox
re-checks the structural parts defensively (see services/plugin-runner).
"""

import hashlib
import json
import os
import re
import zipfile

MANIFEST_NAME = "plugin.json"

# Large enough for a plugin to ship a CPU segmentation/detection model
# (tens to a few hundred MB of ONNX weights); the runner enforces the same
# limits again before extracting.
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 500

# Lowercase slug, 2-64 chars; namespaced with the organization slug on install.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}([-+][0-9A-Za-z.-]+)?$")

OUTPUT_KINDS = ("raster", "vector")
# How the tiles proxy renders a raster output (see api/tiles.py); vector
# render kinds are free-form and fall back to the default map style.
RASTER_RENDER_KINDS = ("dem", "orthophoto")

MIN_TIMEOUT, MAX_TIMEOUT, DEFAULT_TIMEOUT = 10, 3600, 300


class PackageError(ValueError):
    """The package or its manifest is invalid."""


def _member_is_safe(name: str) -> bool:
    if not name or name.startswith(("/", "\\")):
        return False
    return ".." not in name.replace("\\", "/").split("/")


def _require(cond: bool, message: str):
    if not cond:
        raise PackageError(message)


def validate_manifest(manifest, members: set[str]) -> dict:
    """Return a normalized manifest or raise ``PackageError``.

    ``members`` is the set of file names in the package, used to check that the
    entrypoint exists.
    """
    from webodm_core.plugins.runner import DATASET_FIELDS

    _require(isinstance(manifest, dict), f"{MANIFEST_NAME} must be a JSON object")

    plugin_id = manifest.get("id")
    _require(isinstance(plugin_id, str) and _ID_RE.match(plugin_id),
             "'id' must be 2-64 lowercase letters, digits or hyphens")

    version = manifest.get("version")
    _require(isinstance(version, str) and _VERSION_RE.match(version),
             "'version' must be a dotted version string such as 1.0.0")

    label = manifest.get("label") or plugin_id
    _require(isinstance(label, str) and 0 < len(label) <= 140, "'label' must be a short string")

    description = manifest.get("description") or ""
    _require(isinstance(description, str) and len(description) <= 2000,
             "'description' must be a string of at most 2000 characters")

    entrypoint = manifest.get("entrypoint") or "main.py"
    _require(isinstance(entrypoint, str) and _member_is_safe(entrypoint) and entrypoint.endswith(".py"),
             "'entrypoint' must be a relative .py path inside the package")
    _require(entrypoint in members, f"entrypoint '{entrypoint}' not found in package")

    raw_inputs = manifest.get("inputs")
    _require(isinstance(raw_inputs, list) and raw_inputs, "'inputs' must be a non-empty list")
    inputs = []
    seen = set()
    for spec in raw_inputs:
        _require(isinstance(spec, dict), "each input must be an object")
        name = spec.get("name")
        datasets = spec.get("datasets")
        optional = spec.get("optional", False)
        _require(isinstance(name, str) and _ID_RE.match(name), "input 'name' must be a slug")
        _require(name not in seen, f"duplicate input '{name}'")
        seen.add(name)
        _require(isinstance(datasets, list) and datasets, f"input '{name}' needs a 'datasets' list")
        for ds in datasets:
            _require(ds in DATASET_FIELDS,
                     f"input '{name}': unknown dataset '{ds}' (one of {', '.join(DATASET_FIELDS)})")
        _require(isinstance(optional, bool), f"input '{name}': 'optional' must be true or false")
        # An optional input is skipped when the task lacks it (or the user
        # leaves it out); a required one blocks the run. Only these keys are
        # kept so the stored spec is exactly what the run API interprets.
        normalized = {"name": name, "datasets": list(datasets)}
        if optional:
            normalized["optional"] = True
        if isinstance(spec.get("label"), str) and 0 < len(spec["label"]) <= 140:
            normalized["label"] = spec["label"]
        inputs.append(normalized)

    schema = manifest.get("params_schema") or {"type": "object", "properties": {}}
    _require(isinstance(schema, dict), "'params_schema' must be a JSON schema object")
    _require(schema.get("type", "object") == "object", "'params_schema' must describe an object")
    properties = schema.get("properties", {})
    _require(isinstance(properties, dict), "'params_schema.properties' must be an object")
    for key, spec in properties.items():
        _require(isinstance(spec, dict), f"parameter '{key}' must be an object")
    required = schema.get("required", [])
    _require(isinstance(required, list) and all(r in properties for r in required),
             "'params_schema.required' must list declared properties")

    output_kind = manifest.get("output_kind") or "raster"
    _require(output_kind in OUTPUT_KINDS, f"'output_kind' must be one of {', '.join(OUTPUT_KINDS)}")

    render_kind = manifest.get("render_kind") or (output_kind if output_kind == "vector" else "dem")
    _require(isinstance(render_kind, str) and 0 < len(render_kind) <= 40, "'render_kind' must be a short string")
    if output_kind == "raster":
        _require(render_kind in RASTER_RENDER_KINDS,
                 f"raster 'render_kind' must be one of {', '.join(RASTER_RENDER_KINDS)}")

    timeout = manifest.get("timeout_seconds", DEFAULT_TIMEOUT)
    _require(isinstance(timeout, int) and not isinstance(timeout, bool)
             and MIN_TIMEOUT <= timeout <= MAX_TIMEOUT,
             f"'timeout_seconds' must be an integer between {MIN_TIMEOUT} and {MAX_TIMEOUT}")

    return {
        "id": plugin_id,
        "label": label,
        "version": version,
        "description": description,
        "entrypoint": entrypoint,
        "inputs": inputs,
        "params_schema": {"type": "object", "properties": properties, "required": required},
        "output_kind": output_kind,
        "render_kind": render_kind,
        "timeout_seconds": timeout,
    }


def inspect_package(path: str) -> dict:
    """Validate the zip at ``path`` and return its normalized manifest.

    Checks size limits, member paths (no absolute paths, ``..`` or symlinks),
    the presence of ``plugin.json`` and the manifest contents.
    """
    _require(os.path.getsize(path) <= MAX_PACKAGE_BYTES,
             f"package exceeds {MAX_PACKAGE_BYTES // (1024 * 1024)} MB")
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise PackageError(f"not a valid zip file: {e}") from e

    with zf:
        infos = zf.infolist()
        _require(len(infos) <= MAX_MEMBERS, f"package has more than {MAX_MEMBERS} files")
        total = 0
        for info in infos:
            _require(_member_is_safe(info.filename), f"unsafe path in package: {info.filename!r}")
            _require((info.external_attr >> 16) & 0o170000 != 0o120000,
                     f"symlinks are not allowed: {info.filename!r}")
            total += info.file_size
            _require(total <= MAX_EXTRACTED_BYTES,
                     f"package extracts to more than {MAX_EXTRACTED_BYTES // (1024 * 1024)} MB")
        members = {i.filename for i in infos}
        _require(MANIFEST_NAME in members, f"package has no {MANIFEST_NAME} at its root")
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise PackageError(f"{MANIFEST_NAME} is not valid JSON: {e}") from e

    return validate_manifest(manifest, members)


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def namespaced_id(org_slug: str, manifest_id: str) -> str:
    """Catalog id of a user plugin: ``<org-slug>.<id>``.

    The dot keeps user ids from ever colliding with system op ids (plain
    slugs) or with the same manifest id uploaded by another organization.
    """
    return f"{org_slug}.{manifest_id}"
