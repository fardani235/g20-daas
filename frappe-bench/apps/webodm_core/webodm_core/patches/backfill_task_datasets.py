"""Stage 2 of the dataset-library migration: one dataset per pre-library task.

Before the library, a task owned its images as ``WebODM Task Image`` rows.
This backfill gives every task that has no ``dataset`` yet its own
``WebODM Dataset`` (title from the task, description naming the source),
copies the image rows and their photo metadata verbatim, moves the image
``File`` attachments from the task to the dataset (outputs stay on the task)
and points ``task.dataset`` at it. Object storage keys are copied as they
are — ``orgs/<slug>/tasks/<task>/inputs/...`` stays a valid key because the
organization namespace is all the boundary check enforces — so nothing is
copied or moved in the bucket.

Idempotent: a task that already has a dataset is skipped, and the legacy
rows are read with plain SQL so the patch is a no-op on a site where the
table no longer exists (fresh install, or after ``drop_task_image_table``).
Purely additive: it writes new rows and re-attaches Files but deletes
nothing, so reverting the code leaves the data usable; ``rollback()``
undoes the attachment move for that case. Each patch runs in its own
transaction, so a failure halfway rolls the whole backfill back.

Run manually with ``bench --site <site> execute
webodm_core.patches.backfill_task_datasets.execute``; ``report()`` prints
what a run would do.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

LEGACY_TABLE = "tabWebODM Task Image"
DATASET_DOCTYPE = "WebODM Dataset"
TASK_DOCTYPE = "WebODM Task"

ROW_FIELDS = ("image", "filename", "file_size", "storage_key", "latitude", "longitude", "altitude",
              "capture_time")


def legacy_table_exists() -> bool:
    return frappe.db.table_exists("WebODM Task Image")


def legacy_rows(task_name: str) -> list[dict]:
    """The pre-library image rows of a task, in their original order."""
    if not legacy_table_exists():
        return []
    cols = ", ".join(f"`{c}`" for c in ("name", "idx") + ROW_FIELDS)
    return frappe.db.sql(
        f"select {cols} from `{LEGACY_TABLE}` where `parent` = %s and `parenttype` = %s order by `idx`, `creation`",
        (task_name, TASK_DOCTYPE), as_dict=True,
    )


def tasks_without_dataset() -> list[dict]:
    return frappe.get_all(
        TASK_DOCTYPE, filters={"dataset": ["is", "not set"]},
        fields=["name", "title", "owner", "creation", "organization"], order_by="creation asc",
    )


def dataset_title_for(task) -> str:
    return (task.title or task.name).strip() or task.name


def migrate_task(task) -> str | None:
    """Create the dataset for one task and link it. Returns the dataset name (None if skipped)."""
    if not task.organization:
        # Cannot own a dataset without an org; leave the task for manual repair.
        frappe.log_error(f"{task.name}: task has no organization, dataset not created",
                         "WebODM Dataset Migration")
        return None

    rows = legacy_rows(task.name)
    dataset = frappe.get_doc({
        "doctype": DATASET_DOCTYPE,
        "title": dataset_title_for(task),
        "description": f"Migrated from task {task.name}",
        "organization": task.organization,
        "created_by": task.owner,
        "owner": task.owner,
        "creation": task.creation,
    })
    for row in rows:
        dataset.append("images", {f: row.get(f) for f in ROW_FIELDS})
    # Patch context: keep the task's org (see tenancy_hooks._is_migration_copy)
    # and accept the rare legacy task that never had an image.
    dataset.flags.organization_from_source = True
    dataset.flags.allow_empty = True
    dataset.flags.ignore_permissions = True
    dataset.insert(ignore_permissions=True)

    # The image Files move to the dataset; the task keeps its output Files
    # (orthophoto etc.), which are matched by URL and therefore untouched.
    urls = [r["image"] for r in rows if r.get("image")]
    if urls:
        frappe.db.set_value(
            "File",
            {"attached_to_doctype": TASK_DOCTYPE, "attached_to_name": task.name, "file_url": ["in", urls]},
            {"attached_to_doctype": DATASET_DOCTYPE, "attached_to_name": dataset.name},
            update_modified=False,
        )

    frappe.db.set_value(TASK_DOCTYPE, task.name, "dataset", dataset.name, update_modified=False)
    return dataset.name


def execute() -> dict:
    """Backfill every dataset-less task. Returns ``{"tasks", "images", "skipped"}``."""
    stats = {"tasks": 0, "images": 0, "skipped": 0}
    if not frappe.db.has_column(TASK_DOCTYPE, "dataset"):
        # Model sync has not added the column yet (patch run out of order).
        return stats
    for task in tasks_without_dataset():
        name = migrate_task(task)
        if not name:
            stats["skipped"] += 1
            continue
        stats["tasks"] += 1
        stats["images"] += cint(frappe.db.get_value(DATASET_DOCTYPE, name, "image_count"))
    if stats["tasks"]:
        print(f"backfill_task_datasets: created {stats['tasks']} dataset(s) with {stats['images']} image(s)")
    return stats


def report() -> dict:
    """What ``execute`` would do, without writing anything."""
    pending = tasks_without_dataset()
    out = {"legacy_table": legacy_table_exists(), "tasks_without_dataset": len(pending), "tasks": []}
    for task in pending:
        out["tasks"].append({"task": task.name, "title": task.title, "organization": task.organization,
                             "images": len(legacy_rows(task.name))})
    print(frappe.as_json(out))
    return out


def rollback(names: list[str] | None = None) -> dict:
    """Reverse the attachment move so pre-library code can read the images again.

    For every migrated dataset (description ``Migrated from task <name>``; or
    only ``names`` when given) the image Files are re-attached to the source
    task. Dataset rows and the ``task.dataset`` links are left in place (they
    are harmless to old code and make a re-run of ``execute`` a no-op); the
    legacy table is untouched by the backfill in the first place.
    """
    stats = {"datasets": 0, "files": 0}
    filters = {"description": ["like", "Migrated from task %"]}
    if names:
        filters["name"] = ["in", list(names)]
    for ds in frappe.get_all(DATASET_DOCTYPE, filters=filters, fields=["name", "description"]):
        task_name = ds.description.split("Migrated from task ", 1)[1].strip()
        if not frappe.db.exists(TASK_DOCTYPE, task_name):
            continue
        files = frappe.get_all("File", filters={"attached_to_doctype": DATASET_DOCTYPE,
                                                "attached_to_name": ds.name}, pluck="name")
        for f in files:
            frappe.db.set_value("File", f, {"attached_to_doctype": TASK_DOCTYPE, "attached_to_name": task_name},
                                update_modified=False)
        stats["files"] += len(files)
        stats["datasets"] += 1
    print(f"backfill_task_datasets.rollback: re-attached {stats['files']} file(s) of {stats['datasets']} dataset(s)")
    return stats
