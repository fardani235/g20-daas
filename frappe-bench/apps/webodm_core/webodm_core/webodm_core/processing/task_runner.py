"""Task pipeline: explicit start -> dispatch -> poll -> collect -> terminal state.

Two seams were added without changing the pipeline's shape:

* **Where compute lives.** ``process_task`` prefers a ready on-demand node
  (``WebODM Compute Instance``, driven by :mod:`compute` through the
  provisioner service) and falls back to a configured static
  ``WebODM Processing Node``. With no provisioner configured the static path
  is the only path and behaviour is exactly what it was. A task waiting for a
  node sits in the new ``Provisioning`` state; every terminal state releases
  the instance (best-effort, retried by the sweep).
* **Where the bytes live.** With ``storage_bucket`` configured, inputs are
  streamed to the node from the cache or straight from S3, and outputs are
  relayed node -> S3 -> COG (S3 -> S3, in the geospatial service) -> S3, with
  a write-through copy into the host serving cache. Without it, the original
  host-disk flow (``_download_assets``) runs unchanged.
"""

import os
import shutil
import tempfile
import zipfile

import frappe
from frappe.model.document import Document

from webodm_core import storage
from webodm_core.plugins import geospatial
from webodm_core.plugins.files import (
    abs_path_for_file_doc,
    abs_path_for_file_url,
    save_private_file_from_stream,
)
from webodm_core.storage import assets
from webodm_core.webodm_core.processing import compute, raster_metadata
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

# all.zip member -> (Task field, canonical asset file name). Shared by the
# host-disk and the object-storage collection paths so the asset set is one.
ASSET_MAP = {
    "odm_orthophoto/odm_orthophoto.tif": ("orthophoto", "orthophoto.tif"),
    "odm_dem/dsm.tif": ("dsm", "dsm.tif"),
    "odm_dem/dtm.tif": ("dtm", "dtm.tif"),
    "odm_georeferencing/odm_georeferenced_model.laz": ("point_cloud", "georeferenced_model.laz"),
    "odm_texturing/odm_textured_model_geo.glb": ("model", "model.glb"),
}

# How long a task waits (without consuming a dispatch attempt) when the
# compute concurrency cap is reached.
CAPACITY_WAIT_SECONDS = 60


class AssetRelayRetry(NodeODMTransportError):
    """Collecting outputs into object storage hit a transient problem (storage
    or conversion). The task stays Running and the next poll resumes where
    it left off; tolerated like any other poll failure."""


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
    _finish(task, "Failed", progress=0, last_error=error[:1000])


def _finish(task: Document, status: str, **fields):
    """Write a terminal state and hand any on-demand node back.

    Completed, Failed and Cancelled all release the instance. The release is
    best-effort: a failed destroy is retried by the compute sweep and never
    changes the task's outcome.
    """
    task.db_set({"status": status, **fields})
    compute.release_for_task(task)


def _wait_for_capacity(task: Document, reason: str):
    """Park a Queued task briefly without spending a dispatch attempt."""
    from frappe.utils import add_to_date, now_datetime

    task.db_set({
        "next_attempt_at": add_to_date(now_datetime(), seconds=CAPACITY_WAIT_SECONDS),
        "last_error": reason[:1000],
    })


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


def _node_for_task(task: Document) -> dict | None:
    """The node a dispatched task lives on: its compute instance, else the static node.

    Poll, cancel and console all go through this so a task provisioned on an
    ephemeral node is never mistakenly polled on the static one (or vice
    versa) now that tasks can land on different nodes.
    """
    instance_name = getattr(task, "compute_instance", None)
    if instance_name:
        return compute.node_for_instance(instance_name)
    return _first_node()


def _acquire_node(task: Document) -> tuple[dict | None, str]:
    """Pick where a Queued task runs, provisioning if we can.

    Returns ``(node, outcome)``. Order: the task's own Ready compute instance
    (``"ready"``); otherwise, when a provisioner is configured, ask for one —
    the task goes to ``Provisioning`` (``"provisioning"``) or waits on a cap
    (``"capacity"``, no dispatch attempt consumed); otherwise the static node
    (``"static"``). A provisioner that is down or has no provider degrades to
    the static node. ``"none"`` means there is nowhere to run.
    """
    inst = compute.ready_instance_for(task)
    if inst:
        node = compute.node_for_instance(inst.name)
        if node:
            return node, "ready"

    if task.status == "Provisioning":
        return None, "provisioning"  # the provisioning sweep drives readiness and timeouts

    if compute.enabled():
        try:
            instance_name = compute.request_for_task(task)
        except compute.CapacityExceeded as e:
            _wait_for_capacity(task, str(e))
            return None, "capacity"
        except compute.ProvisionerError as e:
            # Down, or no provider behind it: fall back to a static node.
            frappe.log_error(f"{task.name}: {e}; falling back to static node", "WebODM Compute")
        else:
            task.db_set({"status": "Provisioning", "compute_instance": instance_name,
                         "last_error": None, "progress": 1})
            return None, "provisioning"

    node = _first_node()
    return (node, "static") if node else (None, "none")


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
    if task.status not in ("Queued", "Provisioning"):
        return

    node, outcome = _acquire_node(task)
    if not node:
        if outcome == "none":
            _defer_dispatch(task, "no processing nodes configured")
        return

    client = _client_for(node)
    try:
        client.info()
    except NodeODMError as e:
        _defer_dispatch(task, f"node {node['name']} unreachable: {e}")
        return

    task.reload()
    if task.status not in ("Queued", "Provisioning"):
        return

    # Canonical copy first: anything not yet in object storage is synced now
    # (idempotent, usually a no-op after the post-upload sync). A storage
    # outage is logged, not fatal — the host copy still feeds the node and
    # the periodic sync catches up.
    if storage.configured():
        try:
            assets.sync_inputs(task)
        except storage.StorageError as e:
            frappe.log_error(f"{task.name}: input sync failed: {e}", "WebODM Storage")

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


def _get_task_images(task: Document) -> list[tuple[str, object]]:
    """Resolve the task's images to ``(filename, source)`` pairs.

    ``source`` is an absolute path when the image is in the host cache and a
    stream opener (reading straight from object storage) when it is not — so
    re-processing works after the cache has been evicted. Never file
    contents: the client streams one image at a time.
    """
    if storage.configured():
        return assets.input_sources(task)

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

    node = _node_for_task(task)
    if not node:
        _record_poll_failure(task, "processing node is gone (no static node configured)")
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
        _finish(task, "Cancelled", progress=0)
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
    if storage.configured():
        _relay_assets_to_storage(client, node_task_id, task)
        return

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

        asset_map = ASSET_MAP

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
                        abs_path = abs_path_for_file_doc(file_doc)
                        georef = _cogify_raster(abs_path)
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

                        # Normalized header metadata (size, bands, dtype, CRS, pixel
                        # size, tiling, overviews...) for the viewer, tiler and
                        # plugins. The cogify response already carries it; when it
                        # does not (older service, cogify failed) it is fetched
                        # separately. Best-effort: failures land on the metadata
                        # row and in the error log, never on the task.
                        raster_metadata.capture(
                            task, field, file_doc.file_url,
                            metadata=(georef or {}).get("metadata"),
                            abs_path=abs_path,
                        )

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
            _finish(task, "Completed", last_error=None)
        else:
            _fail(task, "all.zip contained none of the expected assets")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# -- object storage collection path --------------------------------------------


def _relay_assets_to_storage(client: NodeODMClient, node_task_id: str, task: Document):
    """Collect a completed task's outputs node -> S3 without touching host disk.

    Steps, each idempotent so a retry resumes rather than restarts:

    1. ``all.zip`` streams from the node straight into ``raw/all.zip``
       (multipart upload from the response body).
    2. The archive is opened *in S3* through range reads and each known member
       streams into its own object: rasters to ``raw/<name>``, everything
       else directly to ``assets/<name>``.
    3. Each raster is converted S3 -> S3 into a COG at ``assets/<name>`` by
       the geospatial service, which also returns georeferencing and header
       metadata read from the S3 object. The raw copy is deleted.
    4. Every asset is written through to the host serving cache as a private
       File (that is what ``task.orthophoto`` etc. point at) and recorded in
       ``task.assets`` with its object key.

    Raises ``NodeODMTransportError`` / ``AssetRelayRetry`` for transient
    problems (node, storage or conversion) so the caller leaves the task
    Running and the next poll retries; anything else is terminal and marks
    the task Failed here. Completed is written only after every output is
    safely in storage, and only then is the compute instance released.
    """
    store = storage.get()
    zip_key = storage.task_key(task, "raw", "all.zip")

    try:
        if not store.exists(zip_key):
            client.stream_asset(
                node_task_id, "all.zip",
                lambda body: store.put_stream(body, zip_key, "application/zip"),
            )
    except NodeODMTransportError:
        raise
    except NodeODMError as e:
        _fail(task, f"node refused all.zip download: {e}")
        return
    except storage.StorageError as e:
        raise AssetRelayRetry(f"could not relay all.zip to object storage: {e}")

    relayed = 0
    try:
        with store.open_seekable(zip_key) as zf, zipfile.ZipFile(zf) as z:
            members = set(z.namelist())
            for member, (field, filename) in ASSET_MAP.items():
                if member not in members:
                    continue
                row = assets.asset_row(task, field)
                if row and row.storage_key and task.get(field):
                    relayed += 1  # already collected by an earlier attempt
                    continue
                if field in RASTER_EXTENT_FIELDS:
                    _relay_raster(store, task, z, member, field, filename)
                else:
                    _relay_blob(store, task, z, member, field, filename)
                relayed += 1

            # Fallback: no GLB but an OBJ texturing dir -> bundle it as model.zip.
            # The bundle is built in the container's scratch space (not the
            # sites volume) because a zip needs a seekable writer.
            if not task.get("model"):
                gltf_prefix = "odm_texturing/"
                model_files = [n for n in members if n.startswith(gltf_prefix) and not n.endswith("/")]
                if model_files:
                    with tempfile.TemporaryFile(prefix=f"{task.name}_model_") as tmp:
                        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as mz:
                            for name in model_files:
                                with z.open(name) as src, mz.open(name[len(gltf_prefix):], "w") as dst:
                                    shutil.copyfileobj(src, dst)
                        tmp.seek(0)
                        key = storage.task_key(task, "assets", "model.zip")
                        store.put_stream(tmp, key, "application/zip")
                    _cache_and_record(store, task, "model", "model.zip", key, is_cog=False)
                    relayed += 1
    except AssetRelayRetry:
        raise
    except storage.StorageError as e:
        raise AssetRelayRetry(f"object storage error while collecting outputs: {e}")
    except Exception as e:
        _fail(task, f"failed to relay assets to object storage: {e}")
        return

    if relayed == 0:
        _fail(task, "all.zip contained none of the expected assets")
        return

    try:
        store.delete(zip_key)  # transient copy; the per-asset objects are canonical
    except storage.StorageError:
        pass
    _finish(task, "Completed", last_error=None)


def _relay_blob(store, task: Document, z: zipfile.ZipFile, member: str, field: str, filename: str):
    """Point cloud / model: stream the member into assets/ as-is, then cache + record."""
    key = storage.task_key(task, "assets", filename)
    if not store.exists(key):
        with z.open(member) as src:
            store.put_stream(src, key, storage.content_type_for(filename))
    _cache_and_record(store, task, field, filename, key, is_cog=False)


def _relay_raster(store, task: Document, z: zipfile.ZipFile, member: str, field: str, filename: str):
    """Raster: stream to raw/, convert S3 -> S3 into a COG at assets/, cache + record."""
    raw_key = storage.task_key(task, "raw", filename)
    asset_key = storage.task_key(task, "assets", filename)

    georef, is_cog = None, False
    if store.exists(asset_key) and not store.exists(raw_key):
        # A previous attempt converted this raster but died before caching it;
        # the metadata is re-read from the cached copy below.
        is_cog = True
    else:
        if not store.exists(raw_key):
            with z.open(member) as src:
                store.put_stream(src, raw_key, "image/tiff")
        georef, is_cog = _cogify_in_storage(store, task, raw_key, asset_key, filename)
        try:
            store.delete(raw_key)
        except storage.StorageError:
            pass  # harmless leftover; the task prefix is deleted with the task

    file_doc = _cache_and_record(store, task, field, filename, asset_key, is_cog=is_cog)

    if georef:
        extent = georef.get("extent")
        if extent:
            task.db_set(RASTER_EXTENT_FIELDS[field], frappe.as_json(extent))
        if georef.get("epsg") and not task.get("epsg"):
            task.db_set("epsg", georef["epsg"])
        if georef.get("wkt") and not task.get("wkt"):
            task.db_set("wkt", georef["wkt"])

    # Header metadata: from the cogify response (read off the S3 object) when
    # available, otherwise extracted from the cached copy. Best-effort.
    raster_metadata.capture(
        task, field, file_doc.file_url,
        metadata=(georef or {}).get("metadata"),
        abs_path=abs_path_for_file_doc(file_doc),
    )


def _cogify_in_storage(store, task: Document, raw_key: str, asset_key: str, filename: str):
    """S3 -> S3 COG conversion via the geospatial service.

    Transient failures retry on the next poll (``AssetRelayRetry``). Once the
    poll-failure budget is about to run out, the raw raster is kept as the
    asset instead (server-side copy) so the task completes with a non-COG
    raster rather than failing — slower tiles beat lost output.
    """
    try:
        georef = geospatial.cogify(storage.uri(raw_key), storage.uri(asset_key))
        return georef, True
    except geospatial.GeospatialError as e:
        if int(task.poll_failures or 0) + 1 >= MAX_POLL_FAILURES:
            frappe.log_error(
                f"{task.name}: COG conversion of {filename} kept failing ({e}); storing the raw raster",
                "WebODM Geospatial",
            )
            store.copy(raw_key, asset_key, "image/tiff")
            return None, False
        raise AssetRelayRetry(f"COG conversion of {filename} failed: {e}")


def _cache_and_record(store, task: Document, field: str, filename: str, key: str, *, is_cog: bool):
    """Write an object through to the serving cache and record the asset row."""
    body = store.open_stream(key)
    try:
        file_doc = save_private_file_from_stream(
            body,
            f"{task.name}_{filename}",
            attached_to_doctype="WebODM Task",
            attached_to_name=task.name,
            ignore_permissions=True,
        )
    finally:
        try:
            body.close()
        except Exception:
            pass
    meta = store.head(key) or {}
    task.db_set(field, file_doc.file_url)
    assets.record_asset(
        task, field, filename=filename, file_url=file_doc.file_url, storage_key=key,
        size=meta.get("size") or file_doc.file_size, etag=meta.get("etag"),
        content_type=meta.get("content_type"), is_cog=is_cog,
    )
    return file_doc
