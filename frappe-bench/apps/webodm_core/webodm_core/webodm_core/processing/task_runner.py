import os
import shutil
import tempfile
import zipfile

import frappe
from frappe.model.document import Document
from frappe.utils import add_to_date, now_datetime

from webodm_core.plugins.files import (
    abs_path_for_file_doc,
    abs_path_for_file_url,
    save_private_file_from_stream,
)
from webodm_core.webodm_core.processing.node_client import NodeODMClient, NodeODMError

# Which downloaded raster fields carry georeferencing worth extracting, and the
# Task field that should hold each one's EPSG:4326 extent (GeoJSON Polygon).
RASTER_EXTENT_FIELDS = {
    "orthophoto": "orthophoto_extent",
    "dsm": "dsm_extent",
    "dtm": "dtm_extent",
}

# NodeODM task status codes (see its libs/statusCodes.js). These are distinct
# terminal states and must not be conflated: FAILED is a failure (no assets),
# CANCELED is a user cancel, only COMPLETED yields downloadable assets.
NODE_STATUS_QUEUED = 10
NODE_STATUS_RUNNING = 20
NODE_STATUS_FAILED = 30
NODE_STATUS_COMPLETED = 40
NODE_STATUS_CANCELED = 50

# Dispatch (Queued -> node) retry policy. Transient failures (no node, node
# unreachable, upload error) defer the task with exponential backoff instead of
# re-trying every sweep forever; after MAX_DISPATCH_ATTEMPTS the task is Failed
# with the last error recorded so the user can see why.
MAX_DISPATCH_ATTEMPTS = 8
DISPATCH_BACKOFF_BASE_SECONDS = 60
DISPATCH_BACKOFF_MAX_SECONDS = 30 * 60

# Polling (Running -> node status) tolerance. One transient task_info error
# used to mark a multi-hour job Failed; now only this many CONSECUTIVE
# failures do. With the 1-minute cron that is ~MAX_POLL_FAILURES minutes of
# node downtime before giving up.
MAX_POLL_FAILURES = 10

# How long one sync holds the per-task lock. Longer than any single
# task_info round-trip, shorter than the cron so a crashed holder self-heals.
SYNC_LOCK_TTL_SECONDS = 50


def _status_action(status, progress) -> str:
    """Map a NodeODM status (code int or ``{"code": ...}`` dict) to a poll action.

    Returns one of ``"running"``, ``"download"``, ``"failed"``, ``"cancelled"``.
    Only COMPLETED(40) downloads assets; FAILED(30) and CANCELED(50) are terminal
    and never trigger a download; anything below COMPLETED is still in flight.
    Any unrecognised code is treated as a failure rather than a silent completion.
    """
    code = status.get("code", 0) if isinstance(status, dict) else status
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = 0

    if code == NODE_STATUS_COMPLETED:
        return "download"
    if code == NODE_STATUS_CANCELED:
        return "cancelled"
    if code < NODE_STATUS_FAILED:
        return "running"
    return "failed"


def _geospatial_url() -> str:
    """Base URL of the geospatial FastAPI service (configurable via site config)."""
    return (
        frappe.conf.get("geospatial_url")
        or frappe.conf.get("webodm_geospatial_url")
        or "http://127.0.0.1:5000"
    )


def _cogify_raster(abs_path: str) -> dict | None:
    """Ask the geospatial service to COG-ify a raster and return its georeferencing.

    Returns the service's JSON dict, or None if the service is unreachable or
    errors — callers must treat georeferencing as best-effort so a task can still
    complete when the geospatial service is down.
    """
    import requests

    try:
        resp = requests.post(
            f"{_geospatial_url().rstrip('/')}/export/cogify",
            json={"path": abs_path},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        frappe.log_error(f"cogify failed for {abs_path}: {e}", "WebODM Geospatial")
        return None


def _get_node_tasks_key(task: Document):
    from frappe.utils import get_site_path
    import os
    return os.path.join(
        get_site_path("private", "processing"),
        task.name.replace(" ", "_"),
    )


def _backoff_seconds(attempts: int) -> int:
    """Delay before dispatch attempt number ``attempts`` (1-based): 1, 2, 4, 8... min, capped."""
    if attempts < 1:
        return 0
    return min(DISPATCH_BACKOFF_BASE_SECONDS * 2 ** (attempts - 1), DISPATCH_BACKOFF_MAX_SECONDS)


def _fail(task: Document, error: str):
    frappe.log_error(f"{task.name}: {error}", "WebODM Processing")
    task.db_set("status", "Failed")
    task.db_set("progress", 0)
    task.db_set("last_error", error[:1000])
    task.db_set("next_attempt_at", None)


def _defer_dispatch(task: Document, error: str):
    """Record a transient dispatch failure and schedule the next attempt, or
    give up after MAX_DISPATCH_ATTEMPTS."""
    attempts = int(task.get("dispatch_attempts") or 0) + 1
    task.db_set("dispatch_attempts", attempts)
    task.db_set("last_error", error[:1000])
    if attempts >= MAX_DISPATCH_ATTEMPTS:
        _fail(task, f"Giving up after {attempts} dispatch attempts: {error}")
        return
    delay = _backoff_seconds(attempts)
    task.db_set("next_attempt_at", add_to_date(now_datetime(), seconds=delay))
    frappe.log_error(
        f"{task.name}: dispatch attempt {attempts} failed, retrying in {delay}s: {error}",
        "WebODM Processing",
    )


def _node_client() -> NodeODMClient | None:
    """Client for the first registered processing node, or None if none exist.

    Single node for now; find_best_node exists for when multi-node lands, and
    every caller (dispatch, poll, console, cancel) should go through here so
    the selection policy changes in one place.
    """
    nodes = frappe.get_all(
        "WebODM Processing Node",
        filters={},
        fields=["name", "hostname", "port", "token"],
        limit_page_length=1,
    )
    if not nodes:
        return None
    n = nodes[0]
    return NodeODMClient(n["hostname"], n["port"], n.get("token"))


def process_pending_tasks():
    """Sweep tasks the user has actually asked to run.

    Deliberately filters on ``Queued``, not ``Pending``. ``Pending`` means
    "uploaded, parked, awaiting a human" — sweeping it would start every task
    within a minute of upload and make the Start button and the
    ``auto_start_processing`` setting meaningless. Only an explicit start
    (api.task.process_task or _maybe_autostart) moves Pending -> Queued.
    """
    now = now_datetime()
    rows = frappe.get_all(
        "WebODM Task",
        filters={"status": "Queued"},
        fields=["name", "next_attempt_at"],
    )
    # Backoff window applied here rather than in SQL: Frappe's "is not set"
    # renders as `= ''` which Postgres rejects for a timestamp column.
    tasks = [r.name for r in rows if not r.next_attempt_at or r.next_attempt_at <= now]
    for task_name in tasks:
        frappe.enqueue(
            "webodm_core.webodm_core.processing.task_runner.process_task",
            queue="long",
            job_name=f"process_{task_name}",
            task_name=task_name,
        )


def process_task(task_name: str):
    task = frappe.get_doc("WebODM Task", task_name)
    if task.status != "Queued":
        return
    if task.get("next_attempt_at") and task.next_attempt_at > now_datetime():
        return  # backoff window not over; the sweep will pick it up later

    client = _node_client()
    if client is None:
        _defer_dispatch(task, "No processing nodes available")
        return

    try:
        client.info()
    except NodeODMError as e:
        _defer_dispatch(task, f"Node connection failed: {e}")
        return

    task.reload()
    if task.status != "Queued":
        return

    images = _get_task_images(task)
    if not images:
        # Permanent: retrying will not conjure the files back.
        _fail(task, "Task has no readable images")
        return

    options = task.processing_options or {}
    if isinstance(options, str):
        options = frappe.parse_json(options)

    node_opts = _build_node_options(options) if isinstance(options, (dict, list)) else []

    try:
        result = client.create_task(images, node_opts, name=task.title)
    except NodeODMError as e:
        _defer_dispatch(task, f"Failed to create task on node: {e}")
        return

    node_task_id = result.get("uuid")
    if not node_task_id:
        _defer_dispatch(task, f"No uuid in node response: {result}")
        return

    task.db_set("node_task_id", node_task_id)
    task.db_set("dispatch_attempts", 0)
    task.db_set("poll_failures", 0)
    task.db_set("next_attempt_at", None)
    task.db_set("last_error", None)
    task.db_set("status", "Running")


def _build_node_options(opts: dict | list) -> list[dict]:
    """Translate the frontend's output selection into NodeODM task options.

    NodeODM's POST /task/new expects ``options`` as a JSON *array* of
    ``{"name": ..., "value": ...}`` objects (see its swagger: "as an array of
    the format: [{name: option1, value: value1}, ...]"). A bare ``{name: value}``
    dict is silently dropped by NodeODM's ``filterOptions`` and ODM then runs
    with its defaults (no DSM/DTM) — which is why selecting DSM produced none.

    ODM has no ``--orthophoto`` flag: the orthophoto is generated by default and
    can only be suppressed with ``--skip-orthophoto``. So an unchecked Orthophoto
    box maps to ``skip-orthophoto: true``, not to omitting an orthophoto flag.
    """
    # Preset / dynamic path: options already in NodeODM array form — pass through,
    # keeping only well-formed {name, value} entries.
    if isinstance(opts, list):
        return [o for o in opts if isinstance(o, dict) and "name" in o and "value" in o]

    result: list[dict] = []

    # Boolean output toggles → their real ODM flag names.
    for frontend_key, node_flag in (
        ("dsm", "dsm"),
        ("dtm", "dtm"),
        ("model", "glb"),
        ("pointCloud", "pc-ept"),
    ):
        if opts.get(frontend_key):
            result.append({"name": node_flag, "value": True})

    resolution = opts.get("orthophotoResolution")
    if resolution:
        result.append({"name": "orthophoto-resolution", "value": resolution})

    # Orthophoto is on by default in ODM; only act when the user opted OUT.
    if not opts.get("orthophoto"):
        result.append({"name": "skip-orthophoto", "value": True})

    return result


def _get_task_images(task: Document) -> list[tuple[str, str]]:
    """Resolve the task's images to ``(filename, absolute_path)`` pairs.

    Paths only — the client streams each file from disk one at a time, so the
    dataset is never held in worker memory.
    """
    images = []
    for img in task.images:
        if not img.image:
            continue
        try:
            path = abs_path_for_file_url(img.image)
        except frappe.DoesNotExistError:
            frappe.log_error(f"No File record for {img.image}", "WebODM Processing")
            continue
        if not os.path.isfile(path):
            frappe.log_error(f"Cannot read {path}: file missing on disk", "WebODM Processing")
            continue
        images.append((img.filename or os.path.basename(path), path))
    return images


def update_running_tasks():
    tasks = frappe.get_all(
        "WebODM Task",
        filters={"status": "Running"},
        pluck="name",
    )
    for task_name in tasks:
        frappe.enqueue(
            "webodm_core.webodm_core.processing.task_runner.poll_task",
            queue="short",
            job_name=f"poll_{task_name}",
            task_name=task_name,
        )


def poll_task(task_name: str):
    task = frappe.get_doc("WebODM Task", task_name)
    if task.status != "Running":
        return
    sync_task_with_node(task)


def _sync_lock_key(task_name: str) -> str:
    return f"{frappe.local.site}|webodm_task_sync|{task_name}"


def _progress_pct(progress) -> int | None:
    try:
        progress = float(progress)
    except (TypeError, ValueError):
        return None
    if progress <= 0:
        return None
    return max(1, int(progress)) if progress > 1 else max(1, int(progress * 100))


def sync_task_with_node(task: Document, client: NodeODMClient | None = None) -> dict:
    """The one place a Running task's state is reconciled with NodeODM.

    Called by the background ``poll_task`` job AND by the foreground
    ``get_task_progress`` API, so a user refreshing the console cannot race
    the scheduler: a per-task Redis lock makes the second caller a no-op that
    just reports current DB state. Returns a dict with ``status``,
    ``progress`` and, when the node answered, ``node_progress`` /
    ``node_status_code``.
    """
    result = {"status": task.status, "progress": task.progress}
    if task.status != "Running":
        return result

    node_task_id = task.node_task_id
    if not node_task_id:
        _fail(task, "Running task has no node_task_id")
        result["status"] = "Failed"
        return result

    lock_key = _sync_lock_key(task.name)
    if not frappe.cache.set(lock_key, "1", nx=True, ex=SYNC_LOCK_TTL_SECONDS):
        result["locked"] = True
        return result

    try:
        client = client or _node_client()
        if client is None:
            # Node registry emptied under a running task: not the task's fault,
            # count it like a poll failure so it eventually fails loudly.
            _record_poll_failure(task, "No processing nodes available")
            result["status"] = task.status
            return result

        try:
            info = client.task_info(node_task_id)
        except NodeODMError as e:
            _record_poll_failure(task, str(e))
            result["status"] = task.status
            return result

        if int(task.get("poll_failures") or 0):
            task.db_set("poll_failures", 0)

        raw_status = info.get("status", 0)
        progress = info.get("progress", 0.0)
        result["node_progress"] = progress
        result["node_status_code"] = raw_status.get("code", 0) if isinstance(raw_status, dict) else raw_status

        action = _status_action(raw_status, progress)

        if action == "download":
            task.db_set("progress", 100)
            _download_assets(client, node_task_id, task)
        elif action == "failed":
            err = raw_status.get("errorMessage") if isinstance(raw_status, dict) else None
            _fail(task, f"Processing failed on node: {err or 'no error message'}")
        elif action == "cancelled":
            task.db_set("status", "Cancelled")
            task.db_set("progress", 0)
        else:
            pct = _progress_pct(progress)
            if pct is not None and pct != task.progress:
                task.db_set("progress", pct)

        result["status"] = task.status
        result["progress"] = task.progress
        return result
    finally:
        frappe.cache.delete(lock_key)


def _record_poll_failure(task: Document, error: str):
    failures = int(task.get("poll_failures") or 0) + 1
    task.db_set("poll_failures", failures)
    task.db_set("last_error", error[:1000])
    if failures >= MAX_POLL_FAILURES:
        _fail(task, f"Lost contact with processing node ({failures} consecutive poll failures): {error}")
    else:
        frappe.log_error(f"{task.name}: poll failure {failures}/{MAX_POLL_FAILURES}: {error}", "WebODM Processing")


def _download_assets(client: NodeODMClient, node_task_id: str, task: Document):
    """Fetch all.zip from the node and register each known asset as a File.

    Everything stays on disk: the zip streams to a scratch dir under the site,
    each member is streamed out of the archive straight into private/files, and
    the scratch dir is removed at the end. Peak memory is one I/O chunk, not the
    zip plus the largest asset as before.
    """
    scratch_root = frappe.get_site_path("private", "processing")
    os.makedirs(scratch_root, exist_ok=True)
    work_dir = tempfile.mkdtemp(prefix=f"{task.name}_", dir=scratch_root)
    zip_path = os.path.join(work_dir, "all.zip")

    try:
        try:
            client.download_asset(node_task_id, "all.zip", zip_path)
        except NodeODMError as e:
            _fail(task, f"Failed to download all.zip: {e}")
            return

        asset_map = {
            "odm_orthophoto/odm_orthophoto.tif": ("orthophoto", "orthophoto.tif"),
            "odm_dem/dsm.tif": ("dsm", "dsm.tif"),
            "odm_dem/dtm.tif": ("dtm", "dtm.tif"),
            "odm_georeferencing/odm_georeferenced_model.laz": ("point_cloud", "georeferenced_model.laz"),
            "odm_texturing/odm_textured_model_geo.glb": ("model", "model.glb"),
        }

        downloaded = 0
        try:
            with zipfile.ZipFile(zip_path) as z:
                members = set(z.namelist())
                for member, (field, filename) in asset_map.items():
                    if member not in members:
                        continue
                    with z.open(member) as src:
                        file_doc = save_private_file_from_stream(
                            src,
                            f"{task.name}_{filename}",
                            attached_to_doctype="WebODM Task",
                            attached_to_name=task.name,
                            ignore_permissions=True,
                        )
                    task.db_set(field, file_doc.file_url)
                    downloaded += 1

                    # For georeferenced rasters, convert to COG in place and persist
                    # the extent / EPSG / WKT so the map can locate the layer.
                    if field in RASTER_EXTENT_FIELDS:
                        georef = _cogify_raster(abs_path_for_file_doc(file_doc))
                        if georef:
                            extent = georef.get("extent")
                            if extent:
                                task.db_set(RASTER_EXTENT_FIELDS[field], frappe.as_json(extent))
                            # EPSG/WKT describe the task CRS; the orthophoto is the
                            # canonical source, but fall back to any raster that has it.
                            if georef.get("epsg") and not task.get("epsg"):
                                task.db_set("epsg", georef["epsg"])
                            if georef.get("wkt") and not task.get("wkt"):
                                task.db_set("wkt", georef["wkt"])

                # Fallback: if model not found as GLB, bundle GLTF files as zip.
                if not task.get("model"):
                    gltf_prefix = "odm_texturing/"
                    model_files = [n for n in members if n.startswith(gltf_prefix) and not n.endswith("/")]
                    if model_files:
                        model_zip = os.path.join(work_dir, "model.zip")
                        with zipfile.ZipFile(model_zip, "w", zipfile.ZIP_DEFLATED) as mz:
                            for name in model_files:
                                with z.open(name) as src, mz.open(name[len(gltf_prefix):], "w") as dst:
                                    shutil.copyfileobj(src, dst)
                        with open(model_zip, "rb") as fh:
                            file_doc = save_private_file_from_stream(
                                fh,
                                f"{task.name}_model.zip",
                                attached_to_doctype="WebODM Task",
                                attached_to_name=task.name,
                                ignore_permissions=True,
                            )
                        task.db_set("model", file_doc.file_url)
                        downloaded += 1
        except Exception as e:
            frappe.log_error(f"Failed to extract assets from zip for {task.name}: {e}", "WebODM Processing")

        if downloaded > 0:
            task.db_set("status", "Completed")
            task.db_set("last_error", None)
        else:
            _fail(task, "Node output contained no recognised assets")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
