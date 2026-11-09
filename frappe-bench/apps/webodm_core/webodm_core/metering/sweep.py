"""Catch-up recording of machine time and analysis runs.

The sweep does not try to notice usage as it happens. It asks, every few
minutes, "which machines are closed and which runs are finished that have no
usage record yet?" and records those. That one question is the whole
mechanism:

* it is safe to run as often as you like — a source with a record is not in
  the answer, and the ledger's unique key refuses a second record anyway;
* it is the same code that fills in history — the first run after deployment
  simply finds everything that ever closed;
* a failure costs nothing — the source is still in the answer next time.

Nothing here writes to a source row or takes part in the task pipeline.

The one thing the sweep cannot see is a source that was deleted before it
looked. ``record_instance`` / ``record_run`` are therefore also called from
``hooks`` when a row is about to be deleted (``deleting=True``), under the
same idempotency key, so either route produces the one record.
"""

from __future__ import annotations

import time

import frappe
from frappe.utils import cint

from webodm_core.metering import guard, ledger, rules, timeutil

INSTANCE = "WebODM Compute Instance"
RUN = "WebODM Plugin Run"
TASK = "WebODM Task"

TERMINAL_RUN_STATUSES = ("Completed", "Failed", "Cancelled")

INSTANCE_FIELDS = (
    "name", "organization", "task", "status", "provider", "instance_class",
    "requested_at", "ready_at", "dispatched_at", "released_at", "terminated_at",
    "destroy_attempts", "release_reason",
)
RUN_FIELDS = (
    "name", "organization", "plugin", "task", "reference_task", "status",
    "creation", "started_at", "completed_at", "modified",
)

# One run records at most this many sources and works for at most this long.
# A large backlog (history, or a sweep that was down for a week) is worked off
# over several runs. The time limit is what matters in practice: scheduled
# jobs run on the default queue, whose jobs are killed after 300 seconds.
MAX_RECORDS_PER_RUN = 2000
MAX_SECONDS_PER_RUN = 120


def site_time_zone() -> str:
    """The zone the source timestamps are written in (Frappe's system time zone)."""
    from frappe.utils import get_system_timezone

    return get_system_timezone()


# -- what is still outstanding ------------------------------------------------


def outstanding_instances(organization: str | None = None) -> list[str]:
    """Closed compute instances that have no usage record, oldest first.

    Closed means the interval has an end: ``released_at`` (we asked for the
    machine back) or, on rows older than that column, ``terminated_at``. An
    instance still in ``Terminating`` — destroy being retried — is closed for
    this purpose: the retries are not the customer's time.
    """
    condition, values = _org_condition("i", organization)
    return [row[0] for row in frappe.db.sql(
        f"""
        SELECT i.name FROM `tab{INSTANCE}` i
        WHERE (i.released_at IS NOT NULL OR i.terminated_at IS NOT NULL) {condition}
          AND NOT EXISTS (
              SELECT 1 FROM `tab{ledger.DOCTYPE}` u WHERE u.idempotency_key = CONCAT(%s, i.name)
          )
        ORDER BY i.creation ASC, i.name ASC
        """,
        (*values, ledger.compute_key("")),
    )]


def outstanding_runs(organization: str | None = None) -> list[str]:
    """Plugin runs in a terminal state that have no usage record, oldest first."""
    condition, values = _org_condition("r", organization)
    marks = ", ".join(["%s"] * len(TERMINAL_RUN_STATUSES))
    return [row[0] for row in frappe.db.sql(
        f"""
        SELECT r.name FROM `tab{RUN}` r
        WHERE r.status IN ({marks}) {condition}
          AND NOT EXISTS (
              SELECT 1 FROM `tab{ledger.DOCTYPE}` u WHERE u.idempotency_key = CONCAT(%s, r.name)
          )
        ORDER BY r.creation ASC, r.name ASC
        """,
        (*TERMINAL_RUN_STATUSES, *values, ledger.analysis_run_key("")),
    )]


def _org_condition(alias: str, organization: str | None) -> tuple[str, tuple]:
    return (f"AND {alias}.organization = %s", (organization,)) if organization else ("", ())


# -- one source -> one record ---------------------------------------------------


def _load(doctype: str, fields: tuple, source):
    """The source's facts as a dict, from a name or from the document being deleted."""
    if isinstance(source, str):
        return frappe.db.get_value(doctype, source, list(fields), as_dict=True)
    return frappe._dict({field: source.get(field) for field in fields})


def _captured(deleting: bool) -> str:
    return rules.CAPTURED_BEFORE_DELETE if deleting else rules.CAPTURED_BY_SWEEP


def instance_inputs(row, *, deleting: bool = False) -> dict:
    """The facts ``rules.compute_usage`` needs, exactly as the instance holds them.

    Deliberately absent: ``estimated_hourly_cost`` and ``estimated_cost``.
    They are floats for a progress screen; a usage record says what ran
    (provider and class), never what it might have cost.
    """
    work_evidence = None
    if row.dispatched_at:
        work_evidence = "dispatched_at"
    elif row.task:
        # Instances closed before `dispatched_at` existed (and any whose stamp
        # failed): the task still says so if it still points here.
        link = frappe.db.get_value(TASK, {"name": row.task, "organization": row.organization},
                                   ["compute_instance", "node_task_id"], as_dict=True)
        if link and link.compute_instance == row.name and link.node_task_id:
            work_evidence = "task_link"
    return {
        "status": row.status,
        "requested_at": timeutil.format_local(row.requested_at),
        "ready_at": timeutil.format_local(row.ready_at),
        "dispatched_at": timeutil.format_local(row.dispatched_at),
        "released_at": timeutil.format_local(row.released_at),
        "terminated_at": timeutil.format_local(row.terminated_at),
        "destroy_attempts": cint(row.destroy_attempts),
        "release_reason": row.release_reason or None,
        "provider": row.provider or None,
        "instance_class": row.instance_class or None,
        "task": row.task or None,
        "site_time_zone": site_time_zone(),
        "work_evidence": work_evidence,
        "captured": _captured(deleting),
        "observed_at_utc": timeutil.iso_z(timeutil.utc_now()),
    }


def record_instance(source, *, deleting: bool = False) -> tuple[str | None, bool]:
    """Record one compute instance. Returns ``(record name, created)``.

    ``source`` is an instance name, or the document itself when it is being
    deleted (``deleting=True``). An instance that is still open is refused by
    the rule unless it is being deleted.
    """
    row = _load(INSTANCE, INSTANCE_FIELDS, source)
    if not row:
        return None, False
    key = ledger.compute_key(row.name)
    existing = ledger.exists(key)
    if existing:
        return existing, False
    inputs = instance_inputs(row, deleting=deleting)
    return ledger.record(
        organization=row.organization,
        rule=rules.RULE_COMPUTE,
        inputs=inputs,
        result=rules.compute_usage(inputs),
        source_doctype=INSTANCE,
        source_name=row.name,
        idempotency_key=key,
        task=row.task,
        project=_project_of(row.task, row.organization),
    )


def run_inputs(row, *, deleting: bool = False) -> dict:
    """The facts ``rules.analysis_run_usage`` needs, plus what describes the plugin.

    The plugin's type, origin and release are copied because a price list
    may care (a marketplace add-on versus an organization's own upload) and
    the plugin row can be removed later.
    """
    plugin = frappe.db.get_value(
        "WebODM Plugin", row.plugin,
        ["plugin_type", "organization", "source", "product", "release", "version"], as_dict=True,
    ) if row.plugin else None
    # `plugin` on a run is a member-writable link. Describe the plugin only if
    # the run's organization could see it: a System plugin (shared) or its
    # own. Anything else stays blank rather than copy another organization's
    # plugin details into this organization's record.
    if not plugin or not (plugin.plugin_type == "System" or plugin.organization == row.organization):
        plugin = frappe._dict()
    return {
        "status": row.status,
        "creation": timeutil.format_local(row.creation),
        "started_at": timeutil.format_local(row.started_at),
        "completed_at": timeutil.format_local(row.completed_at),
        "modified": timeutil.format_local(row.modified),
        "plugin": row.plugin or None,
        "plugin_type": plugin.get("plugin_type") or None,
        "plugin_source": plugin.get("source") or None,
        "plugin_version": plugin.get("version") or None,
        "product": plugin.get("product") or None,
        "release": plugin.get("release") or None,
        "task": row.task or None,
        "reference_task": row.reference_task or None,
        "site_time_zone": site_time_zone(),
        "captured": _captured(deleting),
        "observed_at_utc": timeutil.iso_z(timeutil.utc_now()),
    }


def record_run(source, *, deleting: bool = False) -> tuple[str | None, bool]:
    """Record one plugin run. Returns ``(record name, created)``.

    A run that can still change state is refused by the rule unless it is
    being deleted, in which case it is recorded as a run that produced
    nothing.
    """
    row = _load(RUN, RUN_FIELDS, source)
    if not row:
        return None, False
    key = ledger.analysis_run_key(row.name)
    existing = ledger.exists(key)
    if existing:
        return existing, False
    inputs = run_inputs(row, deleting=deleting)
    return ledger.record(
        organization=row.organization,
        rule=rules.RULE_ANALYSIS_RUN,
        inputs=inputs,
        result=rules.analysis_run_usage(inputs),
        source_doctype=RUN,
        source_name=row.name,
        idempotency_key=key,
        task=row.task,
        project=_project_of(row.task, row.organization),
    )


def _project_of(task: str | None, organization: str | None) -> str | None:
    """The project of ``task`` — only when the task belongs to ``organization``.

    A run's ``task`` is a member-writable link. A project's name is its
    title, so looking it up for a task of another organization would copy
    that organization's project title into this one's usage record.
    """
    if not task or not organization:
        return None
    return frappe.db.get_value(TASK, {"name": task, "organization": organization}, "project")


# -- the sweep ----------------------------------------------------------------


def run(max_records: int = MAX_RECORDS_PER_RUN, max_seconds: float = MAX_SECONDS_PER_RUN) -> dict:
    """Record everything outstanding, oldest first. Idempotent.

    Each source is recorded in its own savepoint: one that fails is logged
    (at most hourly) and left for the next run, and the rest go ahead.
    ``remaining`` counts what was left for the next run because a limit was
    reached; failures do not count against the record limit, so a source
    that can never be recorded cannot starve the ones behind it.
    """
    stats = {"instances": 0, "runs": 0, "already_recorded": 0, "errors": 0, "remaining": 0}
    recorded = 0
    deadline = time.monotonic() + max_seconds
    for kind, names, record_one in (
        ("instances", outstanding_instances(), record_instance),
        ("runs", outstanding_runs(), record_run),
    ):
        for name in names:
            if recorded >= max_records or time.monotonic() >= deadline:
                stats["remaining"] += 1
                continue
            outcome = {}
            with guard.isolated(f"recording {kind[:-1]} {name}", once_key=f"{kind}:{name}") as ok:
                if ok:
                    _, outcome["created"] = record_one(name)
            if "created" not in outcome:
                stats["errors"] += 1
            elif outcome["created"]:
                stats[kind] += 1
                recorded += 1
            else:
                stats["already_recorded"] += 1
            # One transaction per source. A record is durable the moment it is
            # made, and nothing else that wants to record the same fact — a
            # second sweep, the deletion hook inside a user's request — waits
            # on this job for longer than one insert.
            frappe.db.commit()
    return stats


def run_job():
    """Scheduler entry point (every 5 minutes). Never raises."""
    try:
        stats = run()
        if stats["instances"] or stats["runs"] or stats["errors"] or stats["remaining"]:
            frappe.logger("webodm").info(f"usage sweep: {stats}")
    except Exception as e:
        guard.log(f"usage sweep failed: {e.__class__.__name__}: {e}")
