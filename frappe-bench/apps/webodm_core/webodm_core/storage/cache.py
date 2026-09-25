"""The host-side serving cache in front of object storage.

The cache *is* ``sites/<site>/private/files``: task images, task assets and
plugin outputs keep their ``File`` documents and ``/private/files/<name>``
URLs, so the tile proxy, the 3D viewer, the plugin sandbox and Frappe's own
private-file route keep working unchanged. What changes is that the blob
behind a URL may be absent — evicted, or never written on this host — and is
re-materialised from S3 on demand (``ensure_local``), or served straight from
S3 when a consumer can read object storage itself (``resolve_source``).

Invariants:

* Only blobs whose row carries a ``storage_key`` (i.e. the object is known to
  be in S3) are ever evicted. Losing the cache is never losing data.
* A blob in use by a Queued/Provisioning/Running task or a Running plugin run
  is never evicted.
* Nothing is correct only because the cache is warm: every consumer falls
  back to S3 on a miss.

Every access goes through ``touch`` so the eviction order is LRU-by-use, not
by write time (``atime`` is unreliable on ``noatime`` mounts).
"""

from __future__ import annotations

import os
import time

import frappe
from frappe.utils import cint

from webodm_core import storage
from webodm_core.plugins.files import abs_path_for_file_url

# Defaults for the reaper. Overridable via site config
# (storage_cache_max_bytes / storage_cache_idle_seconds).
DEFAULT_CACHE_MAX_BYTES = 50 * 1024 * 1024 * 1024
DEFAULT_CACHE_IDLE_SECONDS = 7 * 24 * 3600

FILL_JOB = "webodm_core.storage.cache.fill_job"

# Task statuses during which a task's inputs/outputs must stay on disk.
_BUSY_TASK_STATUSES = ("Queued", "Provisioning", "Running")


class CacheMiss(FileNotFoundError):
    """The blob is not on disk and cannot be fetched (no storage / no key)."""


def touch(path: str):
    try:
        os.utime(path, None)
    except OSError:
        pass


def is_warm(path: str) -> bool:
    return bool(path) and os.path.isfile(path)


def storage_key_for_file_url(file_url: str):
    """``(storage_key, organization)`` for a private file URL, or ``None``.

    Looks the URL up in the three places a cache-backed blob can be
    registered. Cheap (indexed equality on ``file_url``/``image``) and only
    called on a miss.
    """
    if not file_url:
        return None
    row = frappe.db.get_value(
        "WebODM Task Asset", {"file_url": file_url, "storage_key": ["!=", ""]},
        ["storage_key", "parent"], as_dict=True,
    )
    if row and row.storage_key:
        org = frappe.db.get_value("WebODM Task", row.parent, "organization")
        return row.storage_key, org
    row = frappe.db.get_value(
        "WebODM Task Image", {"image": file_url, "storage_key": ["!=", ""]},
        ["storage_key", "parent"], as_dict=True,
    )
    if row and row.storage_key:
        org = frappe.db.get_value("WebODM Task", row.parent, "organization")
        return row.storage_key, org
    row = frappe.db.get_value(
        "WebODM Plugin Run", {"output_file": file_url, "storage_key": ["!=", ""]},
        ["storage_key", "organization"], as_dict=True,
    )
    if row and row.storage_key:
        return row.storage_key, row.organization
    return None


def fill(storage_key: str, dest_path: str, org: str) -> str:
    """Download ``storage_key`` to ``dest_path`` (atomic). Enforces the org boundary."""
    storage.assert_org_key(storage_key, org)
    store = storage.get()
    if is_warm(dest_path):
        touch(dest_path)
        return dest_path
    store.get_to_path(storage_key, dest_path)
    touch(dest_path)
    return dest_path


def ensure_local(file_url: str, storage_key: str | None = None, org: str | None = None) -> str:
    """Absolute on-disk path for ``file_url``; fetched from S3 if evicted.

    Raises ``CacheMiss`` when the blob is gone and cannot be re-materialised
    (no storage configured, or no key recorded — a legacy, host-only file).
    """
    path = abs_path_for_file_url(file_url)
    if is_warm(path):
        touch(path)
        return path
    if not storage.configured():
        raise CacheMiss(f"{file_url} is not on disk and object storage is not configured")
    if not storage_key:
        found = storage_key_for_file_url(file_url)
        if not found:
            raise CacheMiss(f"{file_url} is not on disk and has no object storage copy")
        storage_key, org = found
    if not org:
        found = storage_key_for_file_url(file_url)
        org = found[1] if found else None
    return fill(storage_key, path, org)


def resolve_source(file_url: str, storage_key: str | None, org: str | None):
    """Cache-first, S3-second source for a consumer that can read S3 itself.

    Returns ``("local", abs_path)`` when the blob is on disk, otherwise
    ``("s3", "s3://bucket/key")`` and kicks off a background cache fill so the
    next request is warm. Raises ``CacheMiss`` when neither is possible.
    """
    path = abs_path_for_file_url(file_url)
    if is_warm(path):
        touch(path)
        return "local", path
    if storage_key and storage.configured():
        storage.assert_org_key(storage_key, org)
        enqueue_fill(file_url)
        return "s3", storage.uri(storage_key)
    raise CacheMiss(f"{file_url} is not on disk and has no object storage copy")


def enqueue_fill(file_url: str):
    """Warm the cache for ``file_url`` in the background (deduplicated)."""
    try:
        frappe.enqueue(
            FILL_JOB, queue="short", job_id=f"webodm:cachefill:{file_url}", deduplicate=True,
            file_url=file_url,
        )
    except Exception:
        frappe.log_error(f"could not enqueue cache fill for {file_url}", "WebODM Storage")


def fill_job(file_url: str):
    try:
        ensure_local(file_url)
    except CacheMiss:
        pass
    except storage.StorageError as e:
        frappe.log_error(f"cache fill failed for {file_url}: {e}", "WebODM Storage")


# -- eviction -------------------------------------------------------------


def limits() -> tuple[int, int]:
    max_bytes = cint(frappe.conf.get("storage_cache_max_bytes") or 0) or DEFAULT_CACHE_MAX_BYTES
    idle = cint(frappe.conf.get("storage_cache_idle_seconds") or 0) or DEFAULT_CACHE_IDLE_SECONDS
    return max_bytes, idle


def _candidates() -> list[dict]:
    """Every cache-backed blob currently on disk, with size, last use and busy flag."""
    out = []
    busy_tasks = set(frappe.get_all(
        "WebODM Task", filters={"status": ["in", list(_BUSY_TASK_STATUSES)]}, pluck="name",
    ))
    for row in frappe.get_all(
        "WebODM Task Asset", filters={"storage_key": ["!=", ""]},
        fields=["file_url", "storage_key", "parent"],
    ):
        out.append(_entry(row.file_url, row.storage_key, busy=row.parent in busy_tasks))
    for row in frappe.get_all(
        "WebODM Task Image", filters={"storage_key": ["!=", ""]},
        fields=["image as file_url", "storage_key", "parent"],
    ):
        out.append(_entry(row.file_url, row.storage_key, busy=row.parent in busy_tasks))
    for row in frappe.get_all(
        "WebODM Plugin Run", filters={"storage_key": ["!=", ""]},
        fields=["output_file as file_url", "storage_key", "status"],
    ):
        out.append(_entry(row.file_url, row.storage_key, busy=row.status in ("Queued", "Running")))
    return [c for c in out if c]


def _entry(file_url: str, storage_key: str, busy: bool):
    try:
        path = abs_path_for_file_url(file_url)
    except Exception:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return {"file_url": file_url, "path": path, "key": storage_key, "size": st.st_size,
            "last_used": st.st_mtime, "busy": busy}


def evict(max_bytes: int | None = None, idle_seconds: int | None = None, now: float | None = None) -> dict:
    """Drop idle blobs, then the least recently used until under ``max_bytes``.

    Only blobs with an S3 copy are candidates; busy ones are skipped. Returns
    ``{"scanned", "evicted", "bytes_before", "bytes_after"}``.
    """
    if not storage.configured():
        return {"scanned": 0, "evicted": 0, "bytes_before": 0, "bytes_after": 0, "skipped": "storage not configured"}
    default_max, default_idle = limits()
    max_bytes = default_max if max_bytes is None else max_bytes
    idle_seconds = default_idle if idle_seconds is None else idle_seconds
    now = time.time() if now is None else now

    entries = _candidates()
    total = sum(e["size"] for e in entries)
    evicted = 0
    remaining = []
    for e in entries:
        if not e["busy"] and now - e["last_used"] > idle_seconds:
            if _remove(e["path"]):
                evicted += 1
                total -= e["size"]
                continue
        remaining.append(e)

    if total > max_bytes:
        for e in sorted(remaining, key=lambda x: x["last_used"]):
            if total <= max_bytes:
                break
            if e["busy"]:
                continue
            if _remove(e["path"]):
                evicted += 1
                total -= e["size"]

    return {"scanned": len(entries), "evicted": evicted,
            "bytes_before": sum(e["size"] for e in entries), "bytes_after": total}


def _remove(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def evict_job():
    """Scheduler entry point: evict with the configured limits; never raises."""
    try:
        stats = evict()
        if stats.get("evicted"):
            frappe.logger("webodm").info(f"cache eviction: {stats}")
    except Exception as e:
        frappe.log_error(f"cache eviction failed: {e}", "WebODM Storage")
