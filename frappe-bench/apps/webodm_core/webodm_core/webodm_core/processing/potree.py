"""On-demand Potree 2.0 octree for a task's point cloud.

The first time someone opens a task's point cloud in the 3D viewer, the app
asks the geospatial service to convert the LAZ with PotreeConverter and keeps
the three resulting files — ``metadata.json``, ``hierarchy.bin``,
``octree.bin`` — as private files attached to the task, exactly like the
other outputs: served by Frappe's ``/private/files/<name>`` route (session
cookie, org permission through the task, HTTP ranges via werkzeug), and when
object storage is configured stored under ``orgs/<slug>/tasks/<task>/assets/
potree/`` with the host copy as an evictable cache (``WebODM Task Asset``
rows with a ``storage_key``, so ``cache.evict`` / ``serving`` treat them like
any other artifact).

State lives on the task (``potree_status`` Queued | Running | Ready |
Failed, ``potree_error``, ``potree_source`` = the ``point_cloud`` URL the
octree was built from, ``potree_summary``, ``potree_updated``):

* :func:`ensure` is what "opening" calls. It takes a row lock, so two people
  opening the same cloud at once produce one job; the job itself is enqueued
  with a stable ``job_id`` and ``deduplicate=True``. A Failed conversion is
  only retried when asked (``retry=True``); a Queued/Running state older than
  :data:`STALE_SECONDS` (worker died) is restarted.
* :func:`convert_job` does the work in the ``long`` queue and writes a
  terminal state no matter what.
* ``storage.assets.reset_outputs`` (re-processing) clears the state and the
  files; deleting the task drops the files as attachments and the bucket
  prefix with the other outputs.

Tasks whose cloud is never opened cost nothing.
"""

from __future__ import annotations

import json
import os
import shutil

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from webodm_core.plugins import geospatial
from webodm_core.plugins.files import abs_path_for_file_url, save_private_file_from_path, save_private_file_from_stream
from webodm_core import storage
from webodm_core.storage import assets, cache

JOB = "webodm_core.webodm_core.processing.potree.convert_job"
QUEUE = "long"
# The service kills the converter after POTREE_TIMEOUT_SECONDS (7200 default);
# the RQ job and the HTTP call get a little more so the service's own error
# reaches us instead of a timeout.
CONVERSION_TIMEOUT = 7200
JOB_TIMEOUT = CONVERSION_TIMEOUT + 600
# A Queued/Running state older than this has lost its worker and may be restarted.
STALE_SECONDS = JOB_TIMEOUT + 300

STATE_FIELDS = ("potree_status", "potree_error", "potree_source", "potree_summary", "potree_updated")
FILES = assets.POTREE_FILENAMES  # kind -> file name
KIND_FOR_FILE = {name: kind for kind, name in FILES.items()}


def job_id(task_name: str) -> str:
    return f"webodm:potree:{task_name}"


def enqueue(task_name: str):
    frappe.enqueue(JOB, queue=QUEUE, job_id=job_id(task_name), deduplicate=True,
                   timeout=JOB_TIMEOUT, task_name=task_name)


def private_name(task_name: str, filename: str) -> str:
    return f"{task_name}_potree_{filename}"


def _is_stale(updated) -> bool:
    if not updated:
        return True
    return get_datetime(updated) < add_to_date(now_datetime(), seconds=-STALE_SECONDS)


def _set(task_name: str, **values):
    frappe.db.set_value("WebODM Task", task_name, {**values, "potree_updated": now_datetime()},
                        update_modified=False)


def file_urls(task) -> dict[str, str]:
    """``{"metadata.json": "/private/files/...", ...}`` for the stored octree (may be partial)."""
    out = {}
    for row in assets.potree_rows(task):
        if row.file_url and row.kind in FILES:
            out[FILES[row.kind]] = row.file_url
    return out


def _is_ready(task, row) -> bool:
    return (row.get("potree_status") == "Ready"
            and bool(row.get("potree_source"))
            and row.get("potree_source") == task.point_cloud)


def state(task, *, warm: bool = True) -> dict:
    """The viewer's view of the octree: status, error, summary, file URLs, cache warmth.

    A Ready octree built from a point cloud that has since changed is reported
    as not converted (``status`` empty), so the viewer re-triggers the build.
    With object storage, files evicted from the host are refetched in the
    background (``cache.enqueue_fill``) and ``cache`` is ``"warming"`` until
    all three are local again; the viewer waits for ``"warm"`` so the first
    byte-range request never blocks on a multi-GB download.
    """
    row = frappe.db.get_value("WebODM Task", task.name, list(STATE_FIELDS), as_dict=True) or {}
    status = row.get("potree_status") or ""
    if status == "Ready" and not _is_ready(task, row):
        status = ""
    summary = row.get("potree_summary")
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except ValueError:
            summary = None
    out = {
        "task": task.name,
        "status": status,
        "error": row.get("potree_error") or None,
        "updated": row.get("potree_updated"),
        "summary": summary if status == "Ready" else None,
        "point_cloud": task.point_cloud or None,
        "has_dsm": bool(task.dsm),
        "files": None,
        "cache": None,
    }
    if status == "Ready":
        urls = file_urls(task)
        if set(urls) != set(FILES.values()):
            out["status"] = "Failed"
            out["error"] = "the octree files are missing; retry the conversion"
            return out
        out["files"] = urls
        if warm:
            cold = [url for url in urls.values() if not cache.is_warm(abs_path_for_file_url(url))]
            if cold and storage.configured():
                for url in cold:
                    cache.enqueue_fill(url)
                out["cache"] = "warming"
            elif cold:
                out["status"] = "Failed"
                out["error"] = "the octree files are no longer on this host; retry the conversion"
                out["files"] = None
            else:
                out["cache"] = "warm"
    return out


def ensure(task, *, retry: bool = False) -> dict:
    """Start the conversion if needed and return :func:`state`.

    Idempotent under concurrency: the task row is locked while the decision
    is made, and the job id deduplicates in the queue. ``retry`` restarts a
    Failed conversion (a stale Queued/Running one restarts on its own).
    """
    if not task.point_cloud:
        return state(task)
    row = frappe.db.get_value("WebODM Task", task.name, list(STATE_FIELDS), as_dict=True, for_update=True) or {}
    status = row.get("potree_status") or ""
    if status == "Ready" and _is_ready(task, row):
        current = state(task)
        if current["status"] == "Ready":
            return current
        status = "Failed"  # files vanished: fall through to a rebuild
    elif status in ("Queued", "Running") and not _is_stale(row.get("potree_updated")):
        return state(task)
    elif status == "Failed" and not retry:
        return state(task)

    _set(task.name, potree_status="Queued", potree_error=None)
    frappe.db.commit()
    enqueue(task.name)
    return state(task)


# ------------------------------------------------------------------ job


def convert_job(task_name: str):
    """Background job: build the octree and record Ready / Failed on the task."""
    task = frappe.get_doc("WebODM Task", task_name)
    if not task.point_cloud:
        _set(task.name, potree_status="Failed", potree_error="The task has no point cloud")
        frappe.db.commit()
        return
    _set(task.name, potree_status="Running", potree_error=None)
    frappe.db.commit()
    try:
        summary = _convert(task)
    except Exception as e:  # noqa: BLE001 - terminal state must be written whatever happened
        frappe.db.rollback()
        message = _error_text(e)
        frappe.log_error(f"{task.name}: point cloud conversion failed: {message}", "WebODM Potree")
        _set(task.name, potree_status="Failed", potree_error=message)
        frappe.db.commit()
        return
    _set(task.name, potree_status="Ready", potree_error=None, potree_source=task.point_cloud,
         potree_summary=json.dumps(summary))
    frappe.db.commit()


def _error_text(e: Exception) -> str:
    text = str(e) or e.__class__.__name__
    return text[:1000]


def _projection(task) -> str | None:
    if task.get("wkt"):
        return task.wkt
    if task.get("epsg"):
        return f"EPSG:{int(task.epsg)}"
    return None


def _convert(task) -> dict:
    """Run the conversion for ``task`` and attach the files; returns the summary to store."""
    try:
        source, location = assets.raster_source(task, "point_cloud")
    except cache.CacheMiss as e:
        raise RuntimeError(f"the point cloud is not available: {e}") from None

    row = assets.asset_row(task, "point_cloud")
    in_storage = storage.configured() and bool(row and row.storage_key) and task.organization
    clear_files(task)

    if in_storage:
        store = storage.get()
        src = storage.uri(row.storage_key)
        prefix_key = storage.task_key(task, "assets", "potree")
        result = geospatial.to_potree(src, storage.uri(prefix_key), projection=_projection(task),
                                      name=task.title or task.name, timeout=CONVERSION_TIMEOUT)
        for kind, filename in FILES.items():
            key = f"{prefix_key}/{filename}"
            storage.assert_org_key(key, task.organization)
            meta = store.head(key)
            if not meta:
                raise RuntimeError(f"the converter did not write {filename}")
            file_doc = save_private_file_from_stream(
                store.open_stream(key), private_name(task.name, filename),
                attached_to_doctype="WebODM Task", attached_to_name=task.name, ignore_permissions=True)
            assets.record_asset(task, kind, filename=filename, file_url=file_doc.file_url, storage_key=key,
                                size=meta.get("size"), etag=meta.get("etag"),
                                content_type=meta.get("content_type") or storage.content_type_for(filename))
    else:
        if source != "local":
            raise RuntimeError("the point cloud is not on this host")
        out_dir = os.path.abspath(frappe.get_site_path("private", "processing", f"potree-{task.name}"))
        shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(os.path.dirname(out_dir), exist_ok=True)
        try:
            result = geospatial.to_potree(os.path.abspath(location), out_dir, projection=_projection(task),
                                          name=task.title or task.name, timeout=CONVERSION_TIMEOUT)
            for kind, filename in FILES.items():
                src_path = os.path.join(out_dir, filename)
                if not os.path.isfile(src_path):
                    raise RuntimeError(f"the converter did not write {filename}")
                file_doc = save_private_file_from_path(
                    src_path, private_name(task.name, filename),
                    attached_to_doctype="WebODM Task", attached_to_name=task.name,
                    ignore_permissions=True, move=True)
                assets.record_asset(task, kind, filename=filename, file_url=file_doc.file_url, storage_key=None,
                                    size=file_doc.file_size, content_type=storage.content_type_for(filename))
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    summary = {k: result.get(k) for k in ("points", "spacing", "bounding_box", "projection", "attributes",
                                          "encoding", "duration_s")}
    summary["files"] = {name: (result.get("files") or {}).get(name, {}).get("size") for name in FILES.values()}
    summary["storage"] = "s3" if in_storage else "host"
    return summary


def clear_files(task) -> int:
    """Delete the octree's File documents (and blobs) and asset rows; returns the count."""
    count = 0
    for row in assets.potree_rows(task):
        if row.file_url:
            blob = assets.blob_path(row.file_url)
            for name in frappe.get_all("File", filters={"file_url": row.file_url, "attached_to_doctype": "WebODM Task",
                                                        "attached_to_name": task.name}, pluck="name"):
                try:
                    frappe.delete_doc("File", name, ignore_permissions=True, force=True, delete_permanently=True)
                    count += 1
                except Exception as e:  # noqa: BLE001
                    frappe.log_error(f"{task.name}: could not delete {row.file_url}: {e}", "WebODM Potree")
            assets.remove_blob(blob)
        frappe.db.delete("WebODM Task Asset", {"name": row.name})
    return count
