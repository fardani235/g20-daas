"""Cross-package test helpers. Not imported by production code.

Every ``WebODM Task`` needs a ``dataset``, so tests that used to insert a bare
task now build one through :func:`make_dataset` (a dataset with ``n`` tiny
JPEG Files attached, saved as the current session user so the org stamping
applies) and pass its name. :func:`delete_dataset_with_tasks` is the tear-down
counterpart: tasks first, then the dataset, in the order the controllers
require.
"""

from __future__ import annotations

import io

import frappe
from PIL import Image

from webodm_core.plugins.files import save_private_file_from_stream

DATASET_DOCTYPE = "WebODM Dataset"


def tiny_jpeg(color=(120, 120, 120), size=(8, 8), datetime_tag: str | None = None) -> bytes:
    im = Image.new("RGB", size, color)
    kwargs = {}
    if datetime_tag:
        exif = im.getexif()
        exif[306] = datetime_tag
        kwargs["exif"] = exif
    buf = io.BytesIO()
    im.save(buf, format="JPEG", **kwargs)
    return buf.getvalue()


def make_dataset(title: str = "Test Dataset", n: int = 1, *, images: list[tuple[str, bytes]] | None = None,
                 description: str | None = None, ignore_permissions: bool = False, **row_extra):
    """Insert a dataset with ``n`` generated images (or the given ``(filename, bytes)`` pairs).

    Rows are appended *before* the insert, exactly like the backfill does, so
    the non-empty rule and the summary fields are exercised. Returns the doc.
    """
    if images is None:
        images = [(f"IMG_{i:04d}.JPG", tiny_jpeg((10 * i % 255, 50, 90))) for i in range(n)]
    dataset = frappe.get_doc({"doctype": DATASET_DOCTYPE, "title": title, "description": description})
    dataset.flags.allow_empty = True
    dataset.insert(ignore_permissions=ignore_permissions)
    for filename, data in images:
        f = save_private_file_from_stream(io.BytesIO(data), filename, attached_to_doctype=DATASET_DOCTYPE,
                                          attached_to_name=dataset.name, ignore_permissions=True)
        dataset.append("images", {"image": f.file_url, "filename": filename, "file_size": len(data), **row_extra})
    dataset.flags.allow_empty = False
    dataset.flags.images_fixed = False
    dataset.save(ignore_permissions=ignore_permissions)
    return dataset


def make_task(project: str, dataset: str | None = None, **fields):
    """Insert a task for ``project``; creates a one-image dataset when none is given."""
    if dataset is None:
        dataset = make_dataset(f"{fields.get('title') or 'Task'} inputs").name
    doc = frappe.get_doc({"doctype": "WebODM Task", "project": project, "dataset": dataset,
                          "title": fields.pop("title", "Task"), "status": fields.pop("status", "Pending"),
                          **fields})
    return doc.insert()


def delete_dataset_with_tasks(name: str):
    """Tear down: the tasks referencing the dataset, then the dataset itself."""
    if not frappe.db.exists(DATASET_DOCTYPE, name):
        return
    for task in frappe.get_all("WebODM Task", filters={"dataset": name}, pluck="name"):
        frappe.delete_doc("WebODM Task", task, force=True, ignore_permissions=True)
    frappe.delete_doc(DATASET_DOCTYPE, name, force=True, ignore_permissions=True)


def delete_project_tree(project: str):
    """Delete a project, its tasks and every dataset those tasks referenced (if unreferenced afterwards)."""
    if not frappe.db.exists("WebODM Project", project):
        return
    datasets = set(frappe.get_all("WebODM Task", filters={"project": project}, pluck="dataset"))
    frappe.delete_doc("WebODM Project", project, force=True, ignore_permissions=True)
    for ds in datasets:
        if ds and frappe.db.exists(DATASET_DOCTYPE, ds) and not frappe.db.exists("WebODM Task", {"dataset": ds}):
            frappe.delete_doc(DATASET_DOCTYPE, ds, force=True, ignore_permissions=True)
