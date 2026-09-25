"""Execute one user plugin package as a resource-limited subprocess.

A user plugin is a zip with ``plugin.json`` at its root and a Python entrypoint
(default ``main.py``). The contract between the runner and the plugin is a
single JSON file, so a plugin needs no SDK and no imports from this service:

1. The runner extracts the package into a per-run directory and writes
   ``request.json``::

       {"inputs": {"raster": "/sandbox/runs/<id>/inputs/raster.tif"},
        "params": {"threshold": 120.0},
        "context": {"task": {"name": "...", "epsg": 32632, "processing_options": [...]}},
        "output_path":   "/sandbox/runs/<id>/output.tif",
        "result_path":   "/sandbox/runs/<id>/result.json",
        "progress_path": "/sandbox/runs/<id>/progress.json",
        "work_dir":      "/sandbox/runs/<id>/work"}

2. It runs ``python -E -s -B <entrypoint> request.json`` with the plugin
   directory as the working directory.
3. The plugin writes its artifact to ``output_path`` and may write
   ``{"metadata": {...}}`` to ``result_path``. Exit code 0 means success.
   While running it may write ``{"percent": 0-100, "message": "..."}`` to
   ``progress_path``; the caller polls that file (it is left in ``run_dir``
   until the caller removes the directory). ``context`` is read-only
   information about the task the caller chooses to share.

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
import time
import zipfile

MANIFEST_NAME = "plugin.json"

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


GLB_MAGIC = b"glTF"
GLB_CHUNK_JSON = 0x4E4F534A
MODEL_GEOREF_KEY = "webodm_georef"


def read_glb_json(path: str) -> dict:
    """The JSON chunk of a GLB (header + first chunk only; the binary buffer is not read)."""
    import struct

    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != GLB_MAGIC:
            raise PluginError("model output is not a GLB file")
        version = struct.unpack("<I", head[4:8])[0]
        if version != 2:
            raise PluginError(f"model output is GLB version {version}; version 2 is required")
        chunk_len, chunk_type = struct.unpack("<II", f.read(8))
        if chunk_type != GLB_CHUNK_JSON:
            raise PluginError("model output's first GLB chunk is not JSON")
        try:
            return json.loads(f.read(chunk_len).decode("utf-8"))
        except ValueError as e:
            raise PluginError(f"model output has an invalid JSON chunk: {e}") from e


def model_georef(path: str) -> dict:
    """Georeference of a GLB from its ``asset.extras.webodm_georef`` block.

    Unlike rasters, glTF carries no CRS, so the plugin records the origin
    (``CESIUM_RTC``), EPSG and absolute bounds in ``extras``; this validates
    that block and derives the map extent (``bounds_4326``) from it. A model
    without the block (or without an EPSG) is accepted but gets no extent.
    """
    gltf = read_glb_json(path)
    counts = _model_counts(gltf)
    out = {"epsg": None, "extent": None, "bounds_4326": None, "origin": None, **counts}
    extras = ((gltf.get("asset") or {}).get("extras") or {}).get(MODEL_GEOREF_KEY)
    if not isinstance(extras, dict):
        return out
    origin = extras.get("origin")
    if isinstance(origin, list) and len(origin) == 3 and all(isinstance(v, (int, float)) for v in origin):
        out["origin"] = [float(v) for v in origin]
    epsg, bounds = extras.get("epsg"), extras.get("bounds")
    if not (isinstance(epsg, int) and isinstance(bounds, list) and len(bounds) == 4
            and all(isinstance(v, (int, float)) for v in bounds)):
        return out
    from rasterio.crs import CRS
    from rasterio.warp import transform_bounds

    try:
        crs = CRS.from_epsg(epsg)
        minx, miny, maxx, maxy = transform_bounds(crs, "EPSG:4326", *bounds, densify_pts=21)
    except Exception as e:
        raise PluginError(f"model georef has an invalid EPSG/bounds: {e}") from e
    out["epsg"] = int(epsg)
    out["bounds_4326"] = [float(minx), float(miny), float(maxx), float(maxy)]
    out["extent"] = _polygon(minx, miny, maxx, maxy)
    if isinstance(extras.get("z_range"), list):
        out["z_range"] = extras["z_range"]
    return out


def _model_counts(gltf: dict) -> dict:
    triangles = points = 0
    accessors = gltf.get("accessors", [])
    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            mode = prim.get("mode", 4)
            attrs = prim.get("attributes", {})
            try:
                if mode == 4:
                    if "indices" in prim:
                        triangles += accessors[prim["indices"]]["count"] // 3
                    elif "POSITION" in attrs:
                        triangles += accessors[attrs["POSITION"]]["count"] // 3
                elif mode == 0 and "POSITION" in attrs:
                    points += accessors[attrs["POSITION"]]["count"]
            except (IndexError, KeyError, TypeError):
                raise PluginError("model output references a missing accessor")
    return {
        "triangles": int(triangles), "points": int(points),
        "images": len(gltf.get("images", [])),
        "extensions_required": list(gltf.get("extensionsRequired", [])),
    }


OUTPUT_KINDS = ("raster", "vector", "model")


def output_georef(path: str, output_kind: str) -> dict:
    try:
        if output_kind == "raster":
            return raster_georef(path)
        if output_kind == "vector":
            return geojson_georef(path)
        if output_kind == "model":
            return model_georef(path)
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


def _become_child_subreaper():
    """Make orphaned plugin processes reparent to us instead of PID 1.

    Plugins are arbitrary code: one can ``fork``/``setsid`` a process out of its
    process group and exit, leaving a daemon behind. Runs share one container and
    one sandbox volume, so a survivor could read a later run's staged inputs
    (another organization's data). As a child subreaper the runner is the parent
    of anything the plugin orphans, which makes ``_descendant_pids`` find it.
    Returns whether the kernel accepted it.
    """
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        PR_SET_CHILD_SUBREAPER = 36
        return libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except Exception:
        return False


def _descendant_pids(root: int) -> set[int]:
    """Every live process whose parent chain leads back to ``root``."""
    parents: dict[int, int] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                # comm can contain spaces and parentheses; the fields we want
                # (state, ppid) follow the last ')'.
                fields = fh.read().rsplit(b")", 1)[-1].split()
            parents[pid] = int(fields[1])
        except (OSError, IndexError, ValueError):
            continue
    descendants: set[int] = set()
    changed = True
    while changed:
        changed = False
        for pid, ppid in parents.items():
            if pid == root or pid in descendants:
                continue
            if ppid == root or ppid in descendants:
                descendants.add(pid)
                changed = True
    return descendants


def _terminate_tree(proc) -> None:
    """Kill the plugin's process group and any process that escaped it.

    Called on every exit path — success, crash and timeout. Killing only the
    process group (as before) lets a ``setsid`` child survive; the descendant
    sweep catches those because the runner is a child subreaper.
    """
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    mine = os.getpid()
    for _ in range(5):
        strays = _descendant_pids(mine)
        if not strays:
            return
        for pid in strays:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(0.05)


def progress_path_for(run_dir: str) -> str:
    """Where a plugin run publishes its progress; callers poll it while ``/run`` blocks."""
    return os.path.join(run_dir, "progress.json")


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
    the output file (and the last progress file) and nothing else of the
    runner's. ``context`` is passed to the plugin verbatim.
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
    progress_path = progress_path_for(run_dir)
    stdout_path = os.path.join(run_dir, "stdout.log")
    stderr_path = os.path.join(run_dir, "stderr.log")

    try:
        safe_extract(package_path, plugin_dir)
        manifest = load_manifest(plugin_dir)
        os.makedirs(work_dir, exist_ok=True)
        _remove(output_path)
        _remove(result_path)
        _remove(progress_path)

        with open(request_path, "w", encoding="utf-8") as f:
            json.dump({
                "inputs": inputs,
                "params": params,
                "context": context or {},
                "output_path": output_path,
                "result_path": result_path,
                "progress_path": progress_path,
                "work_dir": work_dir,
            }, f)

        _become_child_subreaper()
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
                returncode = None
            finally:
                # On every exit path — success included — drop the process group
                # and anything it forked out of it. A survivor would otherwise
                # read the next run's staged inputs on the shared sandbox volume.
                _terminate_tree(proc)

        if returncode is None:
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
        for p in (request_path, result_path, stdout_path, stderr_path, progress_path + ".tmp"):
            _remove(p)
