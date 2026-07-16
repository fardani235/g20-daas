import frappe
from frappe.model.document import Document
from webodm_core.webodm_core.processing.node_client import NodeODMClient, NodeODMError

NODE_TASK_ID_KEY = "_node_task_id"

# Which downloaded raster fields carry georeferencing worth extracting, and the
# Task field that should hold each one's EPSG:4326 extent (GeoJSON Polygon).
RASTER_EXTENT_FIELDS = {
    "orthophoto": "orthophoto_extent",
    "dsm": "dsm_extent",
    "dtm": "dtm_extent",
}


def _geospatial_url() -> str:
    """Base URL of the geospatial FastAPI service (configurable via site config)."""
    return (
        frappe.conf.get("geospatial_url")
        or frappe.conf.get("webodm_geospatial_url")
        or "http://127.0.0.1:5000"
    )


def _abs_file_path(file_doc: Document) -> str:
    """Absolute on-disk path for a File doc.

    ``File.get_full_path()`` returns a bench-relative path (e.g.
    ``./site/private/files/x.tif``); the geospatial service runs from a different
    CWD and requires an absolute path, so anchor it at the bench directory.
    """
    import os
    from frappe.utils import get_bench_path

    p = file_doc.get_full_path()
    if os.path.isabs(p):
        return p
    return os.path.normpath(os.path.join(get_bench_path(), "sites", p.lstrip("./")))


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
    tasks = frappe.get_all(
        "WebODM Task",
        filters={"status": "Pending"},
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
    if task.status != "Pending":
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
    if task.status != "Pending":
        return

    images = _get_task_images(task)
    if not images:
        frappe.log_error(f"Task {task_name} has no images", "WebODM Processing")
        return

    options = task.processing_options or {}
    if isinstance(options, str):
        options = frappe.parse_json(options)

    node_opts = {}
    if isinstance(options, dict):
        node_opts = _build_node_options(options)

    try:
        result = client.create_task(images, node_opts)
    except NodeODMError as e:
        frappe.log_error(f"Failed to create task on node: {e}", "WebODM Processing")
        return

    node_task_id = result.get("uuid")
    if not node_task_id:
        frappe.log_error(f"No uuid in node response: {result}", "WebODM Processing")
        return

    node_opts = task.processing_options or {}
    if isinstance(node_opts, str):
        node_opts = frappe.parse_json(node_opts)
    if not isinstance(node_opts, dict):
        node_opts = {}
    node_opts[NODE_TASK_ID_KEY] = node_task_id
    task.db_set("processing_options", frappe.as_json(node_opts))
    task.db_set("status", "Running")


def _build_node_options(opts: dict) -> dict:
    mapping = {
        "orthophoto": "orthophoto",
        "dsm": "dsm",
        "dtm": "dtm",
        "model": "glb",
        "pointCloud": "pc-ept",
        "orthophotoResolution": "orthophoto-resolution",
    }
    result = {}
    for frontend_key, node_flag in mapping.items():
        val = opts.get(frontend_key)
        if val is not None and val is not False:
            result[node_flag] = True if val is True else val
    if not result:
        result["orthophoto"] = True
    return result


def _get_task_images(task: Document) -> list[tuple[str, bytes]]:
    images = []
    for img in task.images:
        if not img.image:
            continue
        file_doc = frappe.get_doc("File", {"file_url": img.image}, ignore_permissions=True)
        file_path = file_doc.get_full_path()
        try:
            with open(file_path, "rb") as f:
                images.append((img.filename or "image.jpg", f.read()))
        except (FileNotFoundError, IOError) as e:
            frappe.log_error(f"Cannot read {file_path}: {e}", "WebODM Processing")
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

    opts = task.processing_options
    if isinstance(opts, str):
        opts = frappe.parse_json(opts)
    if not isinstance(opts, dict):
        return

    node_task_id = opts.get(NODE_TASK_ID_KEY)
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

    if isinstance(raw_status, dict):
        status_code = raw_status.get("code", 0)
    else:
        status_code = raw_status

    if status_code >= 40:
        progress_val = progress if isinstance(progress, (int, float)) else 0
        if progress_val >= 100:
            task.db_set("progress", 100)
            _download_assets(client, node_task_id, task)
            return
        task.db_set("status", "Failed")
        task.db_set("progress", 0)
        return

    if progress > 0:
        if progress > 1:
            pct = max(1, int(progress))
        else:
            pct = max(1, int(progress * 100))
        task.db_set("progress", pct)

    if status_code == 30:
        task.db_set("progress", 100)
        _download_assets(client, node_task_id, task)


def _download_assets(client: NodeODMClient, node_task_id: str, task: Document):
    import zipfile
    import io

    try:
        zip_data = client.download_asset(node_task_id, "all.zip")
    except NodeODMError:
        frappe.log_error(f"Failed to download all.zip for {task.name}", "WebODM Processing")
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
        with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
            for zip_path, (field, filename) in asset_map.items():
                try:
                    data = z.read(zip_path)
                except KeyError:
                    continue

                file_doc = frappe.get_doc({
                    "doctype": "File",
                    "file_name": f"{task.name}_{filename}",
                    "is_private": 1,
                    "content": data,
                    "attached_to_doctype": "WebODM Task",
                    "attached_to_name": task.name,
                })
                file_doc.save(ignore_permissions=True)
                task.db_set(field, file_doc.file_url)
                downloaded += 1

                # For georeferenced rasters, convert to COG in place and persist
                # the extent / EPSG / WKT so the map can locate the layer.
                if field in RASTER_EXTENT_FIELDS:
                    georef = _cogify_raster(_abs_file_path(file_doc))
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

            # Fallback: if model not found as GLB, bundle GLTF files as zip
            if not task.get("model"):
                gltf_prefix = "odm_texturing/"
                model_files = [n for n in z.namelist() if n.startswith(gltf_prefix)]
                if model_files:
                    model_buf = io.BytesIO()
                    with zipfile.ZipFile(model_buf, "w", zipfile.ZIP_DEFLATED) as mz:
                        for name in model_files:
                            mz.writestr(name.replace(gltf_prefix, ""), z.read(name))
                    file_doc = frappe.get_doc({
                        "doctype": "File",
                        "file_name": f"{task.name}_model.zip",
                        "is_private": 1,
                        "content": model_buf.getvalue(),
                        "attached_to_doctype": "WebODM Task",
                        "attached_to_name": task.name,
                    })
                    file_doc.save(ignore_permissions=True)
                    task.db_set("model", file_doc.file_url)
                    downloaded += 1
    except Exception as e:
        frappe.log_error(f"Failed to extract assets from zip for {task.name}: {e}", "WebODM Processing")

    task.db_set("status", "Completed" if downloaded > 0 else "Failed")
