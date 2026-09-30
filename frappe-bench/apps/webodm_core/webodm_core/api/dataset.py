"""Dataset library endpoints.

Every endpoint gates access explicitly (``check_permission``) because
``frappe.get_doc`` enforces nothing on its own; the org-scoping hooks in
``permissions.py`` only apply automatically to list queries.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

from webodm_core import datasets
from webodm_core.storage import cache as storage_cache

UPLOAD_LIMIT = 10 * 1024 * 1024 * 1024  # 10 GB, same as the task upload


def _payload():
    raw = frappe.request.data if frappe.request else None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return frappe.parse_json(raw) if raw else frappe.form_dict


def _get_dataset_checked(name: str, ptype: str = "read"):
    if not name:
        frappe.throw("dataset is required")
    dataset = frappe.get_doc(datasets.DOCTYPE, name)
    dataset.check_permission(ptype)
    return dataset


def _list_row(doc, usage: dict) -> dict:
    row = datasets.summary(doc)
    row["task_count"] = usage.get(doc.name, 0)
    return row


@frappe.whitelist(allow_guest=False)
def list_datasets():
    """The organization's datasets, newest first, with image count, size and task usage.

    Goes through ``frappe.get_list`` so the org-scoping query conditions apply;
    ``task_count`` lets the UI show which datasets can be deleted.
    """
    from webodm_core import tenancy
    tenancy.require_org()
    rows = frappe.get_list(
        datasets.DOCTYPE,
        fields=["name", "title", "description", "image_count", "total_size", "created_by", "owner",
                "creation", "modified"],
        order_by="creation desc", limit_page_length=0,
    )
    usage = {}
    if rows:
        for t in frappe.get_all("WebODM Task", filters={"dataset": ["in", [r.name for r in rows]]},
                                fields=["dataset"]):
            usage[t.dataset] = usage.get(t.dataset, 0) + 1
    return [_list_row(frappe._dict(r), usage) for r in rows]


@frappe.whitelist(allow_guest=False)
def get_dataset(name=None):
    """One dataset with its image rows (thumbnail URLs included) and the tasks using it."""
    name = name or _payload().get("name")
    dataset = _get_dataset_checked(name, "read")
    out = datasets.summary(dataset)
    out["images"] = datasets.image_dicts(dataset)
    out["tasks"] = datasets.referencing_tasks(dataset.name)
    return out


@frappe.whitelist(allow_guest=False)
def create_dataset():
    """Create a dataset from multipart ``files`` plus ``title`` / ``description``."""
    from webodm_core import tenancy
    tenancy.require_org()  # deny-by-default: a user with no org cannot upload

    # Frappe only sets max_content_length for /api/method/upload_file.
    frappe.request.max_content_length = UPLOAD_LIMIT
    files = frappe.request.files.getlist("files")
    if not files:
        frappe.throw("No files provided")
    title = frappe.form_dict.get("title") or f"Dataset {frappe.utils.now_datetime():%Y-%m-%d %H:%M}"
    dataset = datasets.create_from_uploads(files, title=title, description=frappe.form_dict.get("description"))
    frappe.db.commit()
    return get_dataset(dataset.name)


@frappe.whitelist(allow_guest=False)
def update_dataset(name=None, title=None, description=None):
    """Edit the title / description. Images are fixed (the controller refuses any change)."""
    data = _payload()
    name = name or data.get("name")
    dataset = _get_dataset_checked(name, "write")
    if title is None:
        title = data.get("title")
    if description is None:
        description = data.get("description")
    if title is not None:
        dataset.title = title
    if description is not None:
        dataset.description = description or None
    dataset.save()
    return datasets.summary(dataset)


@frappe.whitelist(allow_guest=False)
def delete_dataset(name=None):
    """Delete an unreferenced dataset and its stored images.

    A dataset any task still points at is refused with a message naming the
    task(s) (``WebODMDataset.on_trash``), so the UI can tell the user exactly
    what to remove first.
    """
    name = name or _payload().get("name")
    dataset = _get_dataset_checked(name, "delete")
    frappe.delete_doc(datasets.DOCTYPE, dataset.name)
    return {"deleted": dataset.name}


@frappe.whitelist(allow_guest=False)
def thumbnail(dataset=None, image=None, size=None):
    """A small JPEG preview of one dataset image (``GET``, served for ``<img>`` tags).

    Cached on disk per (image, size); built on the first request from the
    original (cache first, object storage second). Responds with the file
    directly (conditional GET / ETag supported) and a private, long-lived
    ``Cache-Control`` so the browser stops asking once it has it — the map view
    shows dozens of these and must never download the multi-megabyte originals.
    """
    from werkzeug.utils import send_file

    dataset_doc = _get_dataset_checked(dataset, "read")
    row = next((r for r in dataset_doc.images if r.name == image), None)
    if row is None:
        frappe.throw(f"Image {image} is not part of dataset {dataset_doc.name}", frappe.DoesNotExistError)
    size = datasets.normalize_thumbnail_size(cint(size) or datasets.DEFAULT_THUMBNAIL_SIZE)
    try:
        path = datasets.ensure_thumbnail(dataset_doc, row, size)
    except storage_cache.CacheMiss:
        frappe.throw("Image is not available", frappe.DoesNotExistError)
    response = send_file(
        path, environ=frappe.request.environ, mimetype="image/jpeg", conditional=True,
        max_age=7 * 24 * 3600, download_name=f"{row.name}_{size}.jpg",
    )
    # Private data: the browser may keep it, shared caches must not.
    response.cache_control.public = False
    response.cache_control.private = True
    return response
