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


def process_pending_tasks():
    """Sweep tasks the user has actually asked to run.

    Deliberately filters on ``Queued``, not ``Pending``. ``Pending`` means
    "uploaded, parked, awaiting a human" — sweeping it would start every task
    within a minute of upload and make the Start button and the
    ``auto_start_processing`` setting meaningless. Only an explicit start
    (api.task.process_task or _maybe_autostart) moves Pending -> Queued.
    """
    tasks = frappe.get_all(
        "WebODM Task",
        filters={"status": "Queued"},
        pluck="name",
    )
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

    nodes = frappe.get_all(
        "WebODM Processing Node",
        filters={},
        fields=["name", "hostname", "port", "token"],
    )
    if not nodes:
        frappe.log_error("No processing nodes available", "WebODM Processing")
        return

    client = NodeODMClient(nodes[0]["hostname"], nodes[0]["port"], nodes[0].get("token"))

    try:
        info = client.info()
    except NodeODMError as e:
        frappe.log_error(f"Node connection failed: {e}", "WebODM Processing")
        return

    task.reload()
    if task.status != "Queued":
        return

    images = _get_task_images(task)
    if not images:
        frappe.log_error(f"Task {task_name} has no images", "WebODM Processing")
        return

    options = task.processing_options or {}
    if isinstance(options, str):
        options = frappe.parse_json(options)

    node_opts = _build_node_options(options) if isinstance(options, (dict, list)) else []

    try:
        result = client.create_task(images, node_opts, name=task.title)
    except NodeODMError as e:
        frappe.log_error(f"Failed to create task on node: {e}", "WebODM Processing")
        return

    node_task_id = result.get("uuid")
    if not node_task_id:
        frappe.log_error(f"No uuid in node response: {result}", "WebODM Processing")
        return

    task.db_set("node_task_id", node_task_id)
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

    node_task_id = task.node_task_id
    if not node_task_id:
        task.db_set("status", "Failed")
        return

    nodes = frappe.get_all(
        "WebODM Processing Node",
        filters={},
        fields=["hostname", "port", "token"],
    )
    if not nodes:
        return

    client = NodeODMClient(nodes[0]["hostname"], nodes[0]["port"], nodes[0].get("token"))

    try:
        info = client.task_info(node_task_id)
    except NodeODMError:
        task.db_set("status", "Failed")
        return

    raw_status = info.get("status", 0)
    progress = info.get("progress", 0.0)

    action = _status_action(raw_status, progress)

    if action == "download":
        task.db_set("progress", 100)
        _download_assets(client, node_task_id, task)
        return

    if action == "failed":
        task.db_set("status", "Failed")
        task.db_set("progress", 0)
        return

    if action == "cancelled":
        task.db_set("status", "Cancelled")
        task.db_set("progress", 0)
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
            frappe.log_error(f"Failed to download all.zip for {task.name}: {e}", "WebODM Processing")
            task.db_set("status", "Failed")
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

        task.db_set("status", "Completed" if downloaded > 0 else "Failed")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
