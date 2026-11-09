"""The daily measurement of what each organization stores.

Storage is a level, not an event, and it leaves no trace: once a file is
deleted nothing says how big it was or how long it was kept. So unlike
machine time, storage cannot be recorded by catching up later. It is
measured on a schedule, and a day that is not measured is gone for good.

Three things follow, and they shape everything here:

1. **Nothing may hold it up.** The measurement reads database rows only. It
   needs no bucket, no reachable object storage, no prices, no provisioner
   and no other part of metering. It has its own scheduler entry.
2. **It never writes any day but today.** There is no backfill, no
   interpolation and no carrying forward. A missing day stays missing and
   ``report.summary`` names it.
3. **The record is its own evidence.** Each measurement lists, per dataset,
   task and analysis run, how many files and how many bytes made up the
   total — because after a delete that line is all the proof there is.

What is measured: the ``file_size`` of every ``File`` row attached to the
organization's datasets, tasks and plugin runs (imagery, ground control
files, outputs, the viewer octree, analysis outputs). ``File`` rows are the
one place every stored blob has a size on both kinds of deployment: task
asset rows exist only when a bucket is configured and plugin runs carry no
size at all. A blob that was evicted from the serving cache keeps its row,
so it is still counted — these are logical bytes, one object one size,
wherever the copies are.

Frappe can create a second ``File`` row for the same attachment (its
``attach_files_to_document`` hook does, when a document with an Attach value
is saved without a matching row), so each distinct file URL of a document is
counted once, with the largest size recorded for it.

What is not measured is listed in ``rules.STORAGE_NOT_CHECKED`` and written
into every measurement.

Grain: one measurement per organization per **UTC day**, taken by the first
scheduler tick of the day that runs. The record says "the level at
``sampled_at_utc`` stands for the whole day" and its quantity is that level
in byte-days.
"""

from __future__ import annotations

import frappe

from webodm_core.metering import guard, ledger, rules, timeutil

ORGANIZATION = "WebODM Organization"

# Documents whose attached files are an organization's stored data, and the
# field shown next to each in a measurement so a line still means something
# after the document is gone.
SOURCES = (
    ("WebODM Dataset", "title"),
    ("WebODM Task", "title"),
    ("WebODM Plugin Run", "plugin"),
)

BASIS = "file-records"
_LABEL_MAX = 80


def measure(organization: str) -> list[dict]:
    """One line per document of ``organization`` that has files: count and bytes.

    Read-only, three grouped queries. Lines are sorted so the same state
    always yields the same evidence.

    The byte total leaves the database as text (``CONCAT(SUM(..), '')``) and
    becomes a Python integer here. ``SUM`` over a 64-bit column is a
    ``numeric``, and Frappe's PostgreSQL driver hands ``numeric`` back as a
    float — exact only up to 2**53. That is nine petabytes, more than any
    organization will hold, but "exact unless it gets big" is not exact.
    """
    lines = []
    for doctype, label_field in SOURCES:
        rows = frappe.db.sql(
            f"""
            SELECT d.name AS name, d.`{label_field}` AS label,
                   COUNT(*) AS files,
                   CONCAT(COALESCE(SUM(x.size), 0), '') AS bytes,
                   COALESCE(SUM(CASE WHEN x.size = 0 THEN 1 ELSE 0 END), 0) AS unsized
            FROM `tab{doctype}` d
            JOIN (
                SELECT f.attached_to_name AS parent,
                       COALESCE(f.file_url, f.name) AS file_url,
                       MAX(CASE WHEN COALESCE(f.file_size, 0) > 0 THEN f.file_size ELSE 0 END) AS size
                FROM `tabFile` f
                WHERE f.attached_to_doctype = %(doctype)s
                  AND COALESCE(f.is_folder, 0) = 0
                  AND f.attached_to_name IN (
                      SELECT name FROM `tab{doctype}` WHERE organization = %(organization)s
                  )
                GROUP BY f.attached_to_name, COALESCE(f.file_url, f.name)
            ) x ON x.parent = d.name
            WHERE d.organization = %(organization)s
            GROUP BY d.name, d.`{label_field}`
            ORDER BY d.name
            """,
            {"doctype": doctype, "organization": organization},
            as_dict=True,
        )
        for row in rows:
            lines.append({
                "doctype": doctype,
                "name": row["name"],
                "label": (row["label"] or "")[:_LABEL_MAX],
                "files": int(row["files"]),
                "bytes": int(row["bytes"]),
                "unsized": int(row["unsized"]),
            })
    return lines


def sample(organization: str) -> tuple[str | None, bool]:
    """Take today's measurement for ``organization`` unless it exists.

    Returns ``(record name, created)``. The day is the UTC day of *now*: the
    function has no way to be asked for another one.
    """
    now = timeutil.utc_now()
    day = now.date()
    key = ledger.storage_key(organization, day)
    existing = ledger.exists(key)
    if existing:
        return existing, False

    inputs = {
        "day": day.isoformat(),
        "sampled_at_utc": timeutil.iso_z(now),
        "basis": BASIS,
        "object_storage_configured": _object_storage_configured(),
        "lines": measure(organization),
    }
    result = rules.storage_usage(inputs)
    return ledger.record(
        organization=organization,
        rule=rules.RULE_STORAGE,
        inputs=inputs,
        result=result,
        source_doctype=ORGANIZATION,
        source_name=organization,
        idempotency_key=key,
    )


def _object_storage_configured() -> bool:
    # Informational only (which kind of deployment the level was taken on);
    # the measurement itself never touches object storage.
    return bool(frappe.conf.get("storage_bucket"))


def sample_all(organizations: list[str] | None = None) -> dict:
    """Measure every organization that has no measurement for today.

    Every organization, whatever its status: a suspended customer's files
    still occupy space. One organization failing does not stop the others,
    and each measurement is committed on its own so a later failure cannot
    take earlier ones with it. ``organizations`` narrows the run (tests,
    and an operator measuring one customer by hand).
    """
    stats = {"organizations": 0, "measured": 0, "already_measured": 0, "errors": 0}
    if organizations is None:
        organizations = frappe.get_all(ORGANIZATION, pluck="name", order_by="creation asc")
    for organization in organizations:
        stats["organizations"] += 1
        outcome = {}
        with guard.isolated(f"storage measurement for {organization}") as ok:
            if ok:
                _, outcome["created"] = sample(organization)
        if "created" not in outcome:
            stats["errors"] += 1
        elif outcome["created"]:
            stats["measured"] += 1
        else:
            stats["already_measured"] += 1
        frappe.db.commit()
    return stats


def sample_job():
    """Scheduler entry point (every 10 minutes). Never raises."""
    try:
        stats = sample_all()
        if stats["measured"] or stats["errors"]:
            frappe.logger("webodm").info(f"storage measurement: {stats}")
    except Exception as e:
        guard.log(f"storage measurement failed: {e.__class__.__name__}: {e}")
