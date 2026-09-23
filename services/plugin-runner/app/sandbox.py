"""Execute one user plugin package as a resource-limited subprocess.

A user plugin is a zip with ``plugin.json`` at its root and a Python entrypoint
(default ``main.py``). The contract between the runner and the plugin is a
single JSON file, so a plugin needs no SDK and no imports from this service:

1. The runner extracts the package into a per-run directory and writes
   ``request.json``::

       {"inputs": {"raster": "/sandbox/runs/<id>/inputs/raster.tif"},
        "params": {"threshold": 120.0},
        "output_path": "/sandbox/runs/<id>/output.tif",
        "result_path": "/sandbox/runs/<id>/result.json",
        "work_dir":    "/sandbox/runs/<id>/work"}

2. It runs ``python -E -s -B <entrypoint> request.json`` with the plugin
   directory as the working directory.
3. The plugin writes its artifact to ``output_path`` and may write
   ``{"metadata": {...}}`` to ``result_path``. Exit code 0 means success.

``request.json`` also carries an optional ``context`` object (task name, CRS,
ODM processing options) that Frappe supplies so a plugin can adapt to how the
task was processed without any access to the database.

Output kinds: ``raster`` (GeoTIFF), ``vector`` (GeoJSON) and ``model`` (a
glTF binary / GLB carrying its georeferencing in ``extras.webodm_georef``).

Isolation is layered: the container this runs in is unprivileged, read-only and
on an internal-only network with nothing but the sandbox volume mounted (see
docker-compose.yml); on top of that every plugin process gets a scrubbed
environment, an address-space / CPU-time / file-size / process-count rlimit and
a wall-clock kill. A plugin that crashes, hangs or leaks therefore fails its own
run and nothing else.
"""

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import zipfile

MANIFEST_NAME = "plugin.json"
OUTPUT_KINDS = ("raster", "vector", "model")

SANDBOX_DIR = os.path.realpath(os.environ.get("SANDBOX_DIR", "/sandbox"))
PLUGIN_PYTHON = os.environ.get("PLUGIN_PYTHON", sys.executable)

DEFAULT_TIMEOUT = int(os.environ.get("PLUGIN_DEFAULT_TIMEOUT", 300))
MAX_TIMEOUT = int(os.environ.get("PLUGIN_MAX_TIMEOUT", 3600))
MEMORY_BYTES = int(os.environ.get("PLUGIN_MEMORY_MB", 2048)) * 1024 * 1024
MAX_OUTPUT_BYTES = int(os.environ.get("PLUGIN_MAX_OUTPUT_MB", 4096)) * 1024 * 1024
MAX_PROCESSES = int(os.environ.get("PLUGIN_MAX_PROCESSES", 128))

# Package limits: compressed size, extracted size and member count. Frappe
# applies the same limits at upload; they are re-checked here so a bug on the
# other side can never turn into a zip bomb in the sandbox.
MAX_PACKAGE_BYTES = int(os.environ.get("PLUGIN_MAX_PACKAGE_MB", 256)) * 1024 * 1024
MAX_EXTRACTED_BYTES = int(os.environ.get("PLUGIN_MAX_EXTRACTED_MB", 1024)) * 1024 * 1024
MAX_MEMBERS = 500

# Bytes of stdout/stderr kept for error messages and the run log.
LOG_TAIL_BYTES = 8 * 1024
MAX_RESULT_BYTES = 64 * 1024


class SandboxError(Exception):
    """The request or the runner's own state is invalid (not the plugin's fault)."""


class PluginError(Exception):
    """The plugin package is malformed or its process failed."""


# --------------------------------------------------------------------------
# Package handling
# --------------------------------------------------------------------------

def _member_is_safe(name: str) -> bool:
    if not name or name.startswith(("/", "\\")):
        return False
    parts = name.replace("\\", "/").split("/")
    return ".." not in parts and not any(p.startswith("/") for p in parts)


def safe_extract(package_path: str, dest: str) -> None:
    """Extract ``package_path`` into ``dest`` refusing traversal, symlinks and bombs."""
    if os.path.getsize(package_path) > MAX_PACKAGE_BYTES:
        raise PluginError("package exceeds the maximum compressed size")
    try:
        zf = zipfile.ZipFile(package_path)
    except zipfile.BadZipFile as e:
        raise PluginError(f"package is not a valid zip: {e}") from e

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise PluginError(f"package has more than {MAX_MEMBERS} members")
        total = 0
        for info in infos:
            if not _member_is_safe(info.filename):
                raise PluginError(f"unsafe path in package: {info.filename!r}")
            # Symlinks (S_IFLNK in the external attributes) could point outside
            # the plugin directory once extracted.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise PluginError(f"symlink in package: {info.filename!r}")
            total += info.file_size
            if total > MAX_EXTRACTED_BYTES:
                raise PluginError("package exceeds the maximum extracted size")
        os.makedirs(dest, exist_ok=True)
        zf.extractall(dest)


def load_manifest(plugin_dir: str) -> dict:
    """Read and minimally validate ``plugin.json``; return it with ``entrypoint`` resolved."""
    path = os.path.join(plugin_dir, MANIFEST_NAME)
    if not os.path.isfile(path):
        raise PluginError(f"package has no {MANIFEST_NAME} at its root")
    try:
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as e:
        raise PluginError(f"{MANIFEST_NAME} is not valid JSON: {e}") from e
    if not isinstance(manifest, dict):
        raise PluginError(f"{MANIFEST_NAME} must be a JSON object")

    entrypoint = manifest.get("entrypoint") or "main.py"
    if not isinstance(entrypoint, str) or not _member_is_safe(entrypoint):
        raise PluginError("entrypoint must be a relative path inside the package")
    entry_abs = os.path.realpath(os.path.join(plugin_dir, entrypoint))
    if not entry_abs.startswith(os.path.realpath(plugin_dir) + os.sep):
        raise PluginError("entrypoint escapes the package directory")
    if not os.path.isfile(entry_abs):
        raise PluginError(f"entrypoint not found in package: {entrypoint}")
    manifest["entrypoint"] = entrypoint
    manifest["_entrypoint_abs"] = entry_abs
    return manifest


# --------------------------------------------------------------------------
# Process limits
# --------------------------------------------------------------------------

def _set_limits(cpu_seconds: int):
    """preexec_fn: cap the plugin process before it starts."""
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _plugin_env(work_dir: str) -> dict:
    """A minimal environment: no service secrets/config, single-threaded BLAS."""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": work_dir,
        "TMPDIR": work_dir,
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        # Thread pools reserve address space per thread; with RLIMIT_AS in
        # place a many-core host would otherwise fail at `import numpy`.
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "GDAL_NUM_THREADS": "1",
        # lazrs (LAZ decompression) builds a rayon pool sized to the host's
        # cores at import; under RLIMIT_AS/NPROC that fails with EAGAIN.
        "RAYON_NUM_THREADS": "1",
    }
    # Library data dirs, when the image sets them (rasterio wheels bundle
    # their own, so these are usually unset).
    for key in ("PROJ_DATA", "PROJ_LIB", "GDAL_DATA", "VIRTUAL_ENV"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _tail(path: str, limit: int = LOG_TAIL_BYTES) -> str:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > limit:
                f.seek(size - limit)
            return f.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _read_result(result_path: str) -> dict:
    """The plugin's optional ``result.json``; only its ``metadata`` object is kept."""
    if not os.path.isfile(result_path):
        return {}
    if os.path.getsize(result_path) > MAX_RESULT_BYTES:
        raise PluginError("result.json is too large")
    try:
        with open(result_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise PluginError(f"result.json is not valid JSON: {e}") from e
    metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
    if not isinstance(metadata, dict):
        raise PluginError("result.json 'metadata' must be an object")
    return metadata


# --------------------------------------------------------------------------
# Output georeferencing (trusted code, so plugins need not compute it)
# --------------------------------------------------------------------------

def _polygon(minx, miny, maxx, maxy) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny],
        ]],
    }


def raster_georef(path: str) -> dict:
    """``extent`` / ``bounds_4326`` / ``epsg`` / size of a raster, as the map expects."""
    import rasterio
    from rasterio.warp import transform_bounds

    with rasterio.open(path) as ds:
        out = {
            "width": int(ds.width), "height": int(ds.height),
            "band_count": int(ds.count), "epsg": None, "extent": None, "bounds_4326": None,
        }
        if ds.crs is None:
            return out
        epsg = ds.crs.to_epsg()
        out["epsg"] = int(epsg) if epsg is not None else None
        b = ds.bounds
        minx, miny, maxx, maxy = transform_bounds(
            ds.crs, "EPSG:4326", b.left, b.bottom, b.right, b.top, densify_pts=21
        )
        out["bounds_4326"] = [minx, miny, maxx, maxy]
        out["extent"] = _polygon(minx, miny, maxx, maxy)
        return out


def geojson_georef(path: str) -> dict:
    """Feature count and 4326 bounds of a GeoJSON FeatureCollection."""
    from shapely.geometry import shape

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise PluginError("vector output must be a GeoJSON FeatureCollection")
    features = data.get("features") or []
    out = {"feature_count": len(features), "epsg": 4326, "extent": None, "bounds_4326": None}
    bounds = None
    for feat in features:
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        if not geom:
            continue
        b = shape(geom).bounds
        bounds = b if bounds is None else (
            min(bounds[0], b[0]), min(bounds[1], b[1]), max(bounds[2], b[2]), max(bounds[3], b[3])
        )
    if bounds:
        out["bounds_4326"] = list(bounds)
        out["extent"] = _polygon(*bounds)
    return out


_GLB_MAGIC = 0x46546C67
_GLB_JSON_CHUNK = 0x4E4F534A


def glb_georef(path: str) -> dict:
    """Structure check of a GLB plus the georeferencing its producer recorded.

    A ``model`` output is a glTF 2.0 binary. The plugin (trusted to know its
    CRS) writes ``extras.webodm_georef`` — ``epsg``, ``origin``, ``bounds`` (in
    that CRS) and ``bounds_4326`` — and this turns it into the ``extent`` /
    ``bounds_4326`` / ``epsg`` metadata the map footprint and viewer expect,
    alongside cheap statistics read from the glTF JSON (triangles, textures).
    """
    import struct

    size = os.path.getsize(path)
    with open(path, "rb") as f:
        header = f.read(12)
        if len(header) < 12 or struct.unpack_from("<I", header, 0)[0] != _GLB_MAGIC:
            raise PluginError("model output is not a GLB (glTF binary) file")
        version, length = struct.unpack_from("<II", header, 4)
        if version != 2:
            raise PluginError(f"model output is glTF version {version}; version 2 is required")
        if length != size:
            raise PluginError("model output GLB is truncated or has a wrong length header")
        chunk_len, chunk_type = struct.unpack("<II", f.read(8))
        if chunk_type != _GLB_JSON_CHUNK:
            raise PluginError("model output GLB has no JSON chunk")
        try:
            root = json.loads(f.read(chunk_len).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise PluginError(f"model output GLB has an invalid JSON chunk: {e}") from e
    if not isinstance(root, dict) or not root.get("meshes"):
        raise PluginError("model output GLB contains no meshes")

    accessors = root.get("accessors", [])
    triangles = vertices = 0
    for mesh in root["meshes"]:
        for prim in mesh.get("primitives", []):
            pos = prim.get("attributes", {}).get("POSITION")
            if pos is not None and pos < len(accessors):
                vertices += int(accessors[pos].get("count", 0))
                if "indices" in prim and prim["indices"] < len(accessors):
                    triangles += int(accessors[prim["indices"]].get("count", 0)) // 3
                else:
                    triangles += int(accessors[pos].get("count", 0)) // 3

    georef = (root.get("extras") or {}).get("webodm_georef") or {}
    out = {
        "format": "glb", "bytes": size, "triangles": triangles, "vertices": vertices,
        "textures": len(root.get("textures", [])), "meshes": len(root["meshes"]),
        "extensions": sorted(root.get("extensionsUsed", [])),
        "epsg": None, "extent": None, "bounds_4326": None,
        "origin": georef.get("origin"), "model_bounds": georef.get("bounds"),
        "up_axis": georef.get("up_axis", "Z"),
    }
    epsg = georef.get("epsg")
    if isinstance(epsg, int):
        out["epsg"] = epsg
    b = georef.get("bounds_4326")
    if isinstance(b, list) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
        out["bounds_4326"] = [float(v) for v in b]
        out["extent"] = _polygon(*out["bounds_4326"])
    return out


def output_georef(path: str, output_kind: str) -> dict:
    try:
        if output_kind == "raster":
            return raster_georef(path)
        if output_kind == "vector":
            return geojson_georef(path)
        if output_kind == "model":
            return glb_georef(path)
    except PluginError:
        raise
    except Exception as e:
        raise PluginError(f"output is not a readable {output_kind}: {e}") from e
    return {}


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def _rmtree(path: str):
    shutil.rmtree(path, ignore_errors=True)


def _remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def run_package(
    package_path: str,
    inputs: dict[str, str],
    params: dict,
    output_path: str,
    run_dir: str,
    *,
    output_kind: str = "raster",
    timeout_seconds: int | None = None,
    context: dict | None = None,
) -> dict:
    """Run one plugin package and return ``{"output_path", "metadata", "log"}``.

    ``run_dir`` must exist and be writable; the extracted plugin, scratch space
    and logs are created under it and removed again, so on return it contains
    the output file and nothing else of the runner's. ``context`` is passed to
    the plugin verbatim in ``request.json``.
    """
    timeout = int(timeout_seconds or DEFAULT_TIMEOUT)
    timeout = max(1, min(timeout, MAX_TIMEOUT))

    for name, path in inputs.items():
        if not os.path.isfile(path):
            raise SandboxError(f"input '{name}' not found: {path}")

    plugin_dir = os.path.join(run_dir, "plugin")
    work_dir = os.path.join(run_dir, "work")
    request_path = os.path.join(run_dir, "request.json")
    result_path = os.path.join(run_dir, "result.json")
    stdout_path = os.path.join(run_dir, "stdout.log")
    stderr_path = os.path.join(run_dir, "stderr.log")

    try:
        safe_extract(package_path, plugin_dir)
        manifest = load_manifest(plugin_dir)
        os.makedirs(work_dir, exist_ok=True)
        _remove(output_path)
        _remove(result_path)

        with open(request_path, "w", encoding="utf-8") as f:
            json.dump({
                "inputs": inputs,
                "params": params,
                "output_path": output_path,
                "result_path": result_path,
                "work_dir": work_dir,
                "context": context if isinstance(context, dict) else {},
            }, f)

        cmd = [PLUGIN_PYTHON, "-E", "-s", "-B", manifest["_entrypoint_abs"], request_path]
        with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
            proc = subprocess.Popen(
                cmd,
                cwd=plugin_dir,
                env=_plugin_env(work_dir),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,  # own process group: the kill reaches children
                preexec_fn=lambda: _set_limits(timeout),
            )
            try:
                returncode = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                tail = _tail(stderr_path)
                raise PluginError(
                    f"plugin timed out after {timeout}s" + (f":\n{tail}" if tail else "")
                )

        log = _tail(stderr_path) or _tail(stdout_path)
        if returncode != 0:
            detail = log or "(no output)"
            raise PluginError(f"plugin exited with status {returncode}:\n{detail}")
        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            raise PluginError("plugin exited successfully but wrote no output")

        metadata = {**_read_result(result_path), **output_georef(output_path, output_kind)}
        return {"output_path": output_path, "metadata": metadata, "log": log}
    finally:
        _rmtree(plugin_dir)
        _rmtree(work_dir)
        for p in (request_path, result_path, stdout_path, stderr_path):
            _remove(p)
