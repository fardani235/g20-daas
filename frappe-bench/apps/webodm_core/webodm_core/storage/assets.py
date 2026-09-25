"""Task-level bookkeeping between the ``WebODM Task`` record and object storage.

* Inputs: ``sync_inputs`` copies uploaded imagery to S3 and records the key on
  each ``WebODM Task Image`` row; ``input_sources`` hands dispatch a stream per
  image that reads the cache when warm and S3 otherwise, so re-processing
  works after the cache is gone.
* Outputs: ``record_asset`` upserts one ``WebODM Task Asset`` row per output
  (kind, S3 key, cache URL, size, etag, COG flag); ``raster_source`` /
  ``ensure_asset_local`` are what tiles, the viewer and plugins resolve
  through — cache first, S3 second.
* ``sync_pending`` is the periodic backfill: anything registered on the host
  but not yet in S3 (legacy tasks, an upload that failed while storage was
  down) gets copied up, so "inputs and outputs live in S3" becomes true for
  the whole site over time, not just for new tasks.

Nothing here knows which cloud provides the bucket.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import frappe
from frappe.utils import now_datetime

from webodm_core import storage
from webodm_core.plugins.files import abs_path_for_file_url
from webodm_core.storage import cache

ASSET_KINDS = ("orthophoto", "dsm", "dtm", "point_cloud", "model")
RASTER_KINDS = ("orthophoto", "dsm", "dtm")

# Canonical file name of each output inside the task's assets/ prefix.
ASSET_FILENAMES = {
    "orthophoto": "orthophoto.tif",
    "dsm": "dsm.tif",
    "dtm": "dtm.tif",
    "point_cloud": "georeferenced_model.laz",
    "model": "model.glb",
}

SYNC_JOB = "webodm_core.storage.assets.sync_task_inputs_job"


# -- inputs -----------------------------------------------------------------


def sync_inputs(task, *, raise_on_error: bool = False) -> int:
    """Copy every not-yet-synced image of ``task`` to S3. Returns the count synced.

    Idempotent: rows that already carry a ``storage_key`` are skipped, and the
    upload is skipped too when the object is already present (a previous run
    that crashed between the PUT and the row update). Errors are logged and
    swallowed unless ``raise_on_error`` — a storage outage must not block an
    upload response or a dispatch that can still read the host copy.
    """
    if not storage.configured():
        return 0
    store = storage.get()
    synced = 0
    for row in task.images:
        if not row.image or row.storage_key:
            continue
        try:
            path = abs_path_for_file_url(row.image)
        except frappe.DoesNotExistError:
            continue
        if not os.path.isfile(path):
            continue
        key = storage.task_key(task, "inputs", f"{storage.safe_segment(row.name)}_{storage.safe_segment(row.filename or os.path.basename(path))}")
        try:
            if not store.exists(key):
                store.put_path(path, key, storage.content_type_for(path))
            row.db_set("storage_key", key, update_modified=False)
            synced += 1
        except storage.StorageError as e:
            frappe.log_error(f"{task.name}: input sync failed for {row.image}: {e}", "WebODM Storage")
            if raise_on_error:
                raise
            break  # storage is unhappy; let the periodic sync retry the rest
    return synced


def enqueue_input_sync(task_name: str):
    try:
        frappe.enqueue(
            SYNC_JOB, queue="long", job_id=f"webodm:syncinputs:{task_name}", deduplicate=True,
            task_name=task_name,
        )
    except Exception:
        frappe.log_error(f"could not enqueue input sync for {task_name}", "WebODM Storage")


def sync_task_inputs_job(task_name: str):
    if not frappe.db.exists("WebODM Task", task_name):
        return
    task = frappe.get_doc("WebODM Task", task_name)
    sync_inputs(task)
    frappe.db.commit()


@contextmanager
def _open_object(store, key: str):
    body = store.open_stream(key)
    try:
        yield body
    finally:
        try:
            body.close()
        except Exception:
            pass


def input_sources(task) -> list[tuple[str, object]]:
    """``(filename, source)`` per image, cache first, S3 second.

    ``source`` is the absolute path when the image is in the host cache and
    otherwise a context manager yielding a readable stream from object
    storage — both forms are what ``NodeODMClient.create_task`` consumes. An
    image that is neither on disk nor in S3 is skipped (and logged), matching
    the old behaviour for files missing on disk.
    """
    out = []
    store = storage.get() if storage.configured() else None
    for row in task.images:
        if not row.image:
            continue
        filename = row.filename or os.path.basename(row.image)
        try:
            path = abs_path_for_file_url(row.image)
        except frappe.DoesNotExistError:
            frappe.log_error(f"No File record for {row.image}", "WebODM Processing")
            path = None
        if path and os.path.isfile(path):
            cache.touch(path)
            out.append((filename, path))
            continue
        if store is not None and row.storage_key:
            storage.assert_org_key(row.storage_key, task.organization)
            out.append((filename, _open_object(store, row.storage_key)))
            continue
        frappe.log_error(
            f"Cannot read {row.image}: missing on disk and not in object storage", "WebODM Processing"
        )
    return out


# -- outputs ----------------------------------------------------------------


def asset_row(task, kind: str):
    for row in task.get("assets") or []:
        if row.kind == kind:
            return row
    return None


def record_asset(task, kind: str, *, filename: str, file_url: str | None, storage_key: str | None,
                 size: int | None = None, etag: str | None = None, content_type: str | None = None,
                 is_cog: bool = False):
    """Upsert the ``WebODM Task Asset`` row for ``kind`` without ``task.save()``.

    Written with direct child inserts / ``db.set_value`` like the raster
    metadata rows, so the single-writer discipline of ``poll_task`` holds
    (no full-document save that could clobber concurrent field writes).
    """
    values = {
        "kind": kind,
        "filename": filename,
        "file_url": file_url,
        "storage_key": storage_key,
        "file_size": int(size or 0),
        "etag": etag or "",
        "content_type": content_type or storage.content_type_for(filename),
        "is_cog": 1 if is_cog else 0,
        "synced_at": now_datetime() if storage_key else None,
    }
    existing = frappe.db.get_value("WebODM Task Asset", {"parent": task.name, "kind": kind}, "name")
    if existing:
        frappe.db.set_value("WebODM Task Asset", existing, values, update_modified=False)
        return existing
    idx = frappe.db.count("WebODM Task Asset", {"parent": task.name}) + 1
    row = frappe.get_doc({
        "doctype": "WebODM Task Asset",
        "parent": task.name,
        "parenttype": "WebODM Task",
        "parentfield": "assets",
        "idx": idx,
        **values,
    })
    row.insert(ignore_permissions=True)
    return row.name


def raster_source(task, dataset: str):
    """``("local", path)`` or ``("s3", uri)`` for a task raster (tiles, volume, metadata)."""
    file_url = task.get(dataset)
    if not file_url:
        raise frappe.DoesNotExistError(f"Task has no {dataset}")
    row = asset_row(task, dataset)
    return cache.resolve_source(file_url, row.storage_key if row else None, task.organization)


def ensure_asset_local(task, kind: str) -> str:
    """Absolute path of a task output, re-materialised from S3 if evicted."""
    file_url = task.get(kind)
    if not file_url:
        raise frappe.DoesNotExistError(f"Task has no {kind}")
    row = asset_row(task, kind)
    return cache.ensure_local(file_url, row.storage_key if row else None, task.organization)


# -- plugin outputs ---------------------------------------------------------


def store_plugin_output(run, file_doc) -> str | None:
    """Copy a plugin run's output to S3 and record the key. Best-effort."""
    if not storage.configured():
        return None
    try:
        path = abs_path_for_file_url(file_doc.file_url)
        key = storage.plugin_run_key(run, file_doc.file_name)
        storage.get().put_path(path, key, storage.content_type_for(path))
        run.db_set("storage_key", key, update_modified=False)
        return key
    except (storage.StorageError, frappe.DoesNotExistError) as e:
        frappe.log_error(f"plugin run {run.name}: output sync failed: {e}", "WebODM Storage")
        return None


def ensure_run_output_local(run) -> str:
    if not run.output_file:
        raise frappe.DoesNotExistError(f"Run {run.name} has no output")
    return cache.ensure_local(run.output_file, run.get("storage_key"), run.organization)


def run_output_source(run):
    if not run.output_file:
        raise frappe.DoesNotExistError(f"Run {run.name} has no output")
    return cache.resolve_source(run.output_file, run.get("storage_key"), run.organization)


# -- lifecycle ----------------------------------------------------------------


def delete_task_objects(task):
    """Remove every object under the task's prefix. Best-effort (logged)."""
    if not storage.configured() or not task.organization:
        return 0
    try:
        return storage.get().delete_prefix(storage.task_prefix(task))
    except storage.StorageError as e:
        frappe.log_error(f"{task.name}: could not delete objects: {e}", "WebODM Storage")
        return 0


def delete_run_objects(run):
    if not storage.configured() or not run.organization:
        return 0
    try:
        return storage.get().delete_prefix(storage.plugin_run_prefix(run))
    except storage.StorageError as e:
        frappe.log_error(f"plugin run {run.name}: could not delete objects: {e}", "WebODM Storage")
        return 0


# -- periodic backfill ------------------------------------------------------


def sync_pending(limit: int = 20) -> dict:
    """Upload host-only blobs to S3: unsynced images, legacy outputs, plugin outputs.

    Runs on a schedule. Bounded per run so a large legacy site is migrated
    gradually without starving the queue. Stops early on the first storage
    outage (the next run retries).
    """
    stats = {"inputs": 0, "assets": 0, "runs": 0}
    if not storage.configured():
        return stats
    store = storage.get()

    try:
        # Tasks with at least one image missing a key.
        parents = frappe.get_all(
            "WebODM Task Image", filters={"storage_key": ["is", "not set"]},
            pluck="parent", distinct=True, limit=limit,
        )
        for name in parents:
            if not frappe.db.exists("WebODM Task", name):
                continue
            task = frappe.get_doc("WebODM Task", name)
            stats["inputs"] += sync_inputs(task, raise_on_error=True)

        # Legacy outputs: Attach fields set but no (synced) asset row.
        for name in frappe.get_all(
            "WebODM Task", filters={"status": "Completed"}, pluck="name", order_by="modified desc", limit=500,
        ):
            task = frappe.get_doc("WebODM Task", name)
            for kind in ASSET_KINDS:
                file_url = task.get(kind)
                if not file_url:
                    continue
                row = asset_row(task, kind)
                if row and row.storage_key:
                    continue
                try:
                    path = abs_path_for_file_url(file_url)
                except frappe.DoesNotExistError:
                    continue
                if not os.path.isfile(path):
                    continue
                # Same canonical names the relay uses (model.zip for the OBJ fallback).
                filename = ASSET_FILENAMES.get(kind, os.path.basename(path))
                if kind == "model" and path.lower().endswith(".zip"):
                    filename = "model.zip"
                key = storage.task_key(task, "assets", filename)
                meta = store.put_path(path, key, storage.content_type_for(path))
                record_asset(task, kind, filename=filename, file_url=file_url, storage_key=key,
                             size=meta.get("size"), etag=meta.get("etag"),
                             is_cog=bool(row and row.is_cog))
                stats["assets"] += 1
                if stats["assets"] >= limit:
                    break
            if stats["assets"] >= limit:
                break

        for name in frappe.get_all(
            "WebODM Plugin Run",
            filters={"status": "Completed", "output_file": ["!=", ""], "storage_key": ["is", "not set"]},
            pluck="name", limit=limit,
        ):
            run = frappe.get_doc("WebODM Plugin Run", name)
            try:
                file_doc = frappe.get_doc("File", {"file_url": run.output_file})
            except frappe.DoesNotExistError:
                continue
            if store_plugin_output(run, file_doc):
                stats["runs"] += 1
    except storage.StorageError as e:
        frappe.log_error(f"storage backfill interrupted: {e}", "WebODM Storage")
    return stats


def sync_pending_job():
    try:
        sync_pending()
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(f"storage backfill failed: {e}", "WebODM Storage")
