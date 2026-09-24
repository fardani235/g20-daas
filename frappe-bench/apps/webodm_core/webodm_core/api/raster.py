"""Raster metadata API for task outputs.

Serves the normalized header metadata stored on the task (see
``processing/raster_metadata.py``) so the viewer, tile layer setup and
analysis/GeoAI plugins can size, place and read a raster without opening it.
Metadata is extracted automatically when a task completes; ``refresh`` (or a
first request for a task processed before metadata existed) re-reads it via
the geospatial service on demand.
"""

import frappe
from frappe.utils import cint

from webodm_core.plugins.files import abs_path_for_file_url
from webodm_core.plugins.geospatial import GeospatialError
from webodm_core.webodm_core.processing import raster_metadata


def _task_checked(task_name: str, ptype: str = "read"):
    # get_doc does not check permissions; gate explicitly so a user cannot read
    # another organization's raster layout by guessing a task id.
    task = frappe.get_doc("WebODM Task", task_name)
    task.check_permission(ptype)
    return task


def _extract(task, dataset: str) -> dict:
    file_url = task.get(dataset)
    if not file_url:
        frappe.throw(f"Task has no {dataset}", frappe.DoesNotExistError)
    try:
        return raster_metadata.extract(task.name, dataset, abs_path_for_file_url(file_url))
    except GeospatialError as e:
        frappe.throw(f"Could not read {dataset} metadata: {e}")


@frappe.whitelist(allow_guest=False)
def get_metadata(task_name: str, dataset: str = "orthophoto", refresh=0):
    """Metadata for one task raster (``orthophoto`` | ``dsm`` | ``dtm``).

    Returns the stored row; extracts on demand when none exists yet (older
    tasks) or when ``refresh`` is truthy. A stored extraction failure is
    returned as-is (``error`` set) unless ``refresh`` is requested.
    """
    if dataset not in raster_metadata.RASTER_DATASETS:
        frappe.throw(f"Unknown dataset: {dataset}")
    refresh = cint(refresh)
    task = _task_checked(task_name, "write" if refresh else "read")

    if not task.get(dataset):
        frappe.throw(f"Task has no {dataset}", frappe.DoesNotExistError)

    meta = None if refresh else raster_metadata.get(task_name, dataset)
    if meta is None:
        meta = _extract(task, dataset)
    return meta


@frappe.whitelist(allow_guest=False)
def list_metadata(task_name: str):
    """``{dataset: metadata}`` for every raster the task has.

    Datasets without stored metadata are extracted on demand; one that cannot
    be read comes back with ``error`` set rather than failing the whole call.
    """
    task = _task_checked(task_name, "read")
    out = raster_metadata.get_all(task_name)
    for dataset in raster_metadata.RASTER_DATASETS:
        if not task.get(dataset) or dataset in out:
            continue
        try:
            out[dataset] = _extract(task, dataset)
        except frappe.ValidationError:
            stored = raster_metadata.get(task_name, dataset)
            if stored is not None:
                out[dataset] = stored
    return out
