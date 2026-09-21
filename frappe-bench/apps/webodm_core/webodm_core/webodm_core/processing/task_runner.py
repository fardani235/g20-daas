import os
import shutil
import tempfile
import zipfile

import frappe
from frappe.model.document import Document

from webodm_core.plugins.files import (
    abs_path_for_file_doc,
    abs_path_for_file_url,
    save_private_file_from_stream,
)
from webodm_core.webodm_core.processing.node_client import (
    NodeODMClient,
    NodeODMError,
    NodeODMTransportError,
)

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

# Retry policy. Dispatch failures back off exponentially (1, 2, 4 ... min,
# capped) and give up after MAX_DISPATCH_ATTEMPTS. While Running, transient
# poll errors are tolerated for MAX_POLL_FAILURES consecutive polls (~ that many
# minutes at the cron cadence) before the task is marked Failed.
MAX_DISPATCH_ATTEMPTS = 8
DISPATCH_BACKOFF_BASE_SECONDS = 60
DISPATCH_BACKOFF_CAP_SECONDS = 30 * 60
MAX_POLL_FAILURES = 15

PROCESS_JOB = "webodm_core.webodm_core.processing.task_runner.process_task"
POLL_JOB = "webodm_core.webodm_core.processing.task_runner.poll_task"


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
    return os.path.join(
        get_site_path("private", "processing"),
        task.name.replace(" ", "_"),
    )


def enqueue_process(task_name: str):
    """Enqueue the dispatch job for a task exactly once (dedup by job_id)."""
    frappe.enqueue(
        PROCESS_JOB, queue="long", job_id=f"webodm:process:{task_name}",
        deduplicate=True, task_name=task_name,
    )


def enqueue_poll(task_name: str):
    """Enqueue the poll job for a task exactly once (dedup by job_id)."""
    frappe.enqueue(
        POLL_JOB, queue="short", job_id=f"webodm:poll:{task_name}",
        deduplicate=True, task_name=task_name,
    )


def _backoff_seconds(attempt: int) -> int:
    """Exponential backoff for the Nth failed dispatch attempt (1-based)."""
    return min(DISPATCH_BACKOFF_BASE_SECONDS * 2 ** max(attempt - 1, 0), DISPATCH_BACKOFF_CAP_SECONDS)


def _fail(task: Document, error: str):
    frappe.log_error(f"{task.name}: {error}", "WebODM Processing")
    task.db_set({"status": "Failed", "progress": 0, "last_error": error[:1000]})


def _defer_dispatch(task: Document, error: str):
    """Record a transient dispatch failure and either schedule a retry or give up."""
    from frappe.utils import add_to_date, now_datetime

    attempts = int(task.dispatch_attempts or 0) + 1
    if attempts >= MAX_DISPATCH_ATTEMPTS:
        _fail(task, f"giving up after {attempts} dispatch attempts: {error}")
        return
    delay = _backoff_seconds(attempts)
    frappe.log_error(
        f"{task.name}: dispatch attempt {attempts} failed, retrying in {delay}s: {error}",
        "WebODM Processing",
    )
    task.db_set({
        "dispatch_attempts": attempts,
        "next_attempt_at": add_to_date(now_datetime(), seconds=delay),
        "last_error": error[:1000],
    })


def _record_poll_failure(task: Document, error: str):
    """Tolerate a transient poll error; fail only after MAX_POLL_FAILURES in a row."""
    failures = int(task.poll_failures or 0) + 1
    if failures >= MAX_POLL_FAILURES:
        _fail(task, f"node unreachable for {failures} consecutive polls: {error}")
        return
    task.db_set({"poll_failures": failures, "last_error": error[:1000]})


def _first_node() -> dict | None:
    nodes = frappe.get_all(
        "WebODM Processing Node", filters={}, fields=["name", "hostname", "port", "token"],
        order_by="creation asc",
    )
    return nodes[0] if nodes else None


def _client_for(node: dict) -> NodeODMClient:
    return NodeODMClient(node["hostname"], node["port"], node.get("token"))


def process_pending_tasks():
    """Sweep tasks the user has actually asked to run.

    Deliberately filters on ``Queued``, not ``Pending``. ``Pending`` means
    "uploaded, parked, awaiting a human" — sweeping it would start every task
    within a minute of upload and make the Start button and the
    ``auto_start_processing`` setting meaningless. Only an explicit start
    (api.task.process_task or _maybe_autostart) moves Pending -> Queued.

    Tasks whose last dispatch failed carry ``next_attempt_at``; they are skipped
    until the backoff has elapsed.
    """
    from frappe.utils import now_datetime

    now = now_datetime()
    tasks = frappe.get_all(
        "WebODM Task",
        filters={"status": "Queued"},
        fields=["name", "next_attempt_at"],
    )
    for t in tasks:
        if t.next_attempt_at and t.next_attempt_at > now:
            continue
        enqueue_process(t.name)


def process_task(task_name: str):
    task = frappe.get_doc("WebODM Task", task_name)
    if task.status != "Queued":
        return

    node = _first_node()
    if not node:
        _defer_dispatch(task, "no processing nodes configured")
        return

    client = _client_for(node)
    try:
        client.info()
    except NodeODMError as e:
        _defer_dispatch(task, f"node {node['name']} unreachable: {e}")
        return

    task.reload()
    if task.status != "Queued":
        return

    images = _get_task_images(task)
    if not images:
        # Nothing to send and nothing that a retry could fix.
        _fail(task, "task has no readable images")
        return

    options = task.processing_options or {}
    if isinstance(options, str):
        options = frappe.parse_json(options)

    node_opts = _build_node_options(options) if isinstance(options, (dict, list)) else []

    try:
        result = client.create_task(images, node_opts, name=task.title)
    except NodeODMTransportError as e:
        _defer_dispatch(task, f"upload to node failed: {e}")
        return
    except NodeODMError as e:
        # The node rejected the task (bad options, too many images...). Retrying
        # the identical request would fail the same way.
        _fail(task, f"node rejected task: {e}")
        return

    task.db_set({
        "node_task_id": result["uuid"],
        "status": "Running",
        "dispatch_attempts": 0,
        "poll_failures": 0,
        "next_attempt_at": None,
        "last_error": None,
    })


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
    for task_name in frappe.get_all("WebODM Task", filters={"status": "Running"}, pluck="name"):
        enqueue_poll(task_name)


def poll_task(task_name: str):
    task = frappe.get_doc("WebODM Task", task_name)
    if task.status != "Running":
        return

    node_task_id = task.node_task_id
    if not node_task_id:
        _fail(task, "Running without a node task id")
        return

    node = _first_node()
    if not node:
        _record_poll_failure(task, "no processing nodes configured")
        return
    client = _client_for(node)

    try:
        info = client.task_info(node_task_id)
    except NodeODMTransportError as e:
        _record_poll_failure(task, str(e))
        return
    except NodeODMError as e:
        # The node answered but does not know this task any more (restarted
        # with cleared data, task removed...). It will not come back.
        _fail(task, f"node no longer has task {node_task_id}: {e}")
        return

    if task.poll_failures:
        task.db_set({"poll_failures": 0, "last_error": None})

    raw_status = info.get("status", 0)
    progress = info.get("progress", 0.0)

    action = _status_action(raw_status, progress)

    if action == "download":
        task.db_set("progress", 100)
        try:
            _download_assets(client, node_task_id, task)
        except NodeODMTransportError as e:
            # Leave the task Running so the next poll retries the download.
            _record_poll_failure(task, f"asset download failed: {e}")
        return

    if action == "failed":
        err = raw_status.get("errorMessage") if isinstance(raw_status, dict) else None
        _fail(task, f"processing failed on node: {err or raw_status}")
        return

    if action == "cancelled":
        task.db_set({"status": "Cancelled", "progress": 0})
        return

    # Still running/queued — sync the latest progress percentage.
    if progress > 0:
        if progress > 1:
            pct = max(1, int(progress))
        else:
            pct = max(1, int(progress * 100))
        task.db_set("progress", pct)


def _download_assets(client: NodeODMClient, node_task_id: str, task: Document):
    """Fetch all.zip from the node and register each known asset as a File.

    Raises ``NodeODMTransportError`` if the zip could not be fetched (so the
    caller can retry later); any other failure is terminal and marks the task
    Failed here.

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
        except NodeODMTransportError:
            raise  # transient: caller keeps the task Running and retries
        except NodeODMError as e:
            _fail(task, f"node refused all.zip download: {e}")
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
            _fail(task, f"failed to extract assets from all.zip: {e}")
            return

        if downloaded > 0:
            task.db_set({"status": "Completed", "last_error": None})
        else:
            _fail(task, "all.zip contained none of the expected assets")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
