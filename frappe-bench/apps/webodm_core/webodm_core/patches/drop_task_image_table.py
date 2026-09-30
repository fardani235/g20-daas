"""Stage 3 of the dataset-library migration: retire ``WebODM Task Image``.

Runs after ``backfill_task_datasets`` (order in patches.txt) and is the only
destructive step, so it re-verifies the backfill first and refuses to touch
anything unless every check passes:

* every task has a ``dataset`` that exists;
* for every task, the dataset holds at least as many image rows as the
  legacy table has for that task;
* no image ``File`` is still attached to a task.

A failed check raises, which aborts ``bench migrate`` *after* the backfill
has been committed (each patch is its own transaction) and before the table
is dropped — the site keeps working on the new code, and the failure message
lists the offending tasks so they can be repaired (or the backfill re-run)
and the migrate retried. ``verify()`` runs the same checks read-only for a
manual pre-flight.

Frappe's own ``remove_orphan_doctypes`` (run at the end of every migrate)
deletes the DocType *row* of the removed ``webodm_task_image`` module but
deliberately keeps its table; this patch drops the table so the retired
schema is actually gone. On a fresh site there is nothing to verify or drop.
"""

from __future__ import annotations

import frappe

from webodm_core.patches import backfill_task_datasets as backfill

LEGACY_DOCTYPE = "WebODM Task Image"


class MigrationIncomplete(frappe.ValidationError):
    pass


def verify() -> list[str]:
    """Problems that must be fixed before the legacy table may be dropped (empty = OK)."""
    problems = []
    tasks = frappe.get_all(backfill.TASK_DOCTYPE, fields=["name", "title", "dataset"])
    datasets = {}
    if tasks:
        for row in frappe.get_all(backfill.DATASET_DOCTYPE,
                                  filters={"name": ["in", [t.dataset for t in tasks if t.dataset]]},
                                  fields=["name", "image_count"]):
            datasets[row.name] = row.image_count
    for t in tasks:
        if not t.dataset:
            problems.append(f"task {t.name} ({t.title}) has no dataset")
            continue
        if t.dataset not in datasets:
            problems.append(f"task {t.name} points at missing dataset {t.dataset}")
            continue
        legacy = len(backfill.legacy_rows(t.name))
        if legacy > (datasets[t.dataset] or 0):
            problems.append(
                f"task {t.name}: {legacy} legacy image row(s) but dataset {t.dataset} has {datasets[t.dataset]}"
            )
    if backfill.legacy_table_exists():
        stray = frappe.db.sql(
            f"""select count(*) from `tabFile` f
                join `{backfill.LEGACY_TABLE}` i on i.`image` = f.`file_url` and i.`parent` = f.`attached_to_name`
                where f.`attached_to_doctype` = %s""",
            (backfill.TASK_DOCTYPE,),
        )[0][0]
        if stray:
            problems.append(f"{stray} image File(s) are still attached to tasks instead of datasets")
    return problems


def execute():
    if not backfill.legacy_table_exists():
        return
    problems = verify()
    if problems:
        detail = "\n".join(f"  - {p}" for p in problems[:50])
        more = f"\n  ... and {len(problems) - 50} more" if len(problems) > 50 else ""
        frappe.throw(
            "Refusing to drop the WebODM Task Image table: the dataset backfill is incomplete.\n"
            f"{detail}{more}\n"
            "Fix the tasks above (or re-run webodm_core.patches.backfill_task_datasets.execute) "
            "and run `bench migrate` again.",
            MigrationIncomplete,
        )
    frappe.db.sql_ddl(f"drop table if exists `{backfill.LEGACY_TABLE}`")
    # The DocType row would go with remove_orphan_doctypes anyway; removing it
    # here keeps the schema consistent even when this is run by hand.
    if frappe.db.exists("DocType", LEGACY_DOCTYPE):
        frappe.delete_doc("DocType", LEGACY_DOCTYPE, force=True, ignore_permissions=True, ignore_missing=True)
    frappe.clear_cache(doctype=backfill.TASK_DOCTYPE)
    print("drop_task_image_table: legacy WebODM Task Image table dropped")
