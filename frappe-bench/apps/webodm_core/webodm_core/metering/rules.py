"""The rules that turn source facts into a quantity. Pure functions; no Frappe.

Each rule takes ``inputs`` — the facts read from a source row, exactly as
read, as JSON-safe values — and returns an :func:`outcome`. A usage record
stores those same ``inputs`` as its evidence, so the rule can be run again
on the record alone, years later and with the source long deleted, and must
give back the quantity on the row. ``verify`` does exactly that.

That is also why nothing here reads a clock, a database or a setting: the
instant a recorder looked (``observed_at_utc``) and the site time zone are
inputs like any other.

A rule's name carries its version. Changing what a rule counts means adding
``compute/v2`` next to ``compute/v1``, never editing ``v1``: existing
records were made by ``v1`` and must keep verifying against it.

Quantities: ``quantity`` is what counts. ``waived_quantity`` is what was
measured and deliberately not counted, with ``waived_reason`` saying why.
A waived row always has ``quantity == 0``, so summing ``quantity`` can never
pick up something the platform decided to absorb.
"""

from __future__ import annotations

import hashlib
import json

from webodm_core.metering import timeutil

METER_COMPUTE = "compute"
METER_ANALYSIS_RUN = "analysis_run"
METER_STORAGE = "storage"

UNITS = {METER_COMPUTE: "second", METER_ANALYSIS_RUN: "run", METER_STORAGE: "byte-day"}

RULE_COMPUTE = "compute/v1"
RULE_ANALYSIS_RUN = "analysis_run/v1"
RULE_STORAGE = "storage/v1"
RULE_REVERSAL = "reversal/v1"

# compute: why measured machine time does not count.
WAIVED_DELETED_BEFORE_RELEASE = "deleted_before_release"
WAIVED_NEVER_READY = "never_ready"
WAIVED_INVALID_INTERVAL = "invalid_interval"
WAIVED_NO_WORK = "no_work_dispatched"
WAIVED_PLATFORM_FAILURE = "platform_failure"
WAIVED_END_NOT_RECORDED = "end_time_not_recorded"

# analysis_run: why a run does not count.
WAIVED_FAILED = "failed"
WAIVED_CANCELLED = "cancelled"
WAIVED_DELETED_BEFORE_FINISHING = "deleted_before_finishing"

# Reasons the platform itself gave for destroying a machine. Time on a machine
# the platform killed, or that failed on the platform's side, is absorbed.
PLATFORM_RELEASE_REASONS = (
    "lifetime-exceeded",
    "provisioning-failed",
    "provisioning-timeout",
    "provisioner-error",
)

# How a source came to be recorded: found by the sweep, or caught by the
# on_trash hook as it was being deleted (the only way unfinished work is
# ever recorded).
CAPTURED_BY_SWEEP = "sweep"
CAPTURED_BEFORE_DELETE = "before_delete"

STORAGE_DIMENSION = "standard"

# What a storage measurement looked at, and what it did not. Stored on every
# measurement so nobody has to remember.
STORAGE_CHECKED = (
    "size recorded on each File row attached to the organization's datasets, tasks and analysis runs",
    "each distinct file URL of a document counted once",
)
STORAGE_NOT_CHECKED = (
    "the bucket itself (no object listing): orphaned objects, incomplete multipart uploads and object versions are not seen",
    "bytes actually on disk or in the serving cache",
    "plugin packages, thumbnails and transient relay objects (deliberately not measured)",
)
COMPUTE_NOT_CHECKED = (
    "the provider's own billing record for this machine",
    "whether the job's output was usable",
)
ANALYSIS_RUN_NOT_CHECKED = (
    "whether the run's output file still exists",
    "that the run row was not edited before it was recorded (members may write to it)",
)


# -- canonical form and hashing -------------------------------------------------


def canonical_json(value) -> str:
    """One byte sequence per value: sorted keys, no whitespace, UTF-8 as is."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# The fields a record's hash covers, in the order they are listed in the
# DocType. Everything that makes the record say what it says.
HASHED_FIELDS = (
    "organization", "meter", "dimension", "entry_type", "unit",
    "quantity", "waived_quantity", "waived_reason",
    "period_start_utc", "period_end_utc", "recorded_at_utc",
    "source_doctype", "source_name", "task", "project",
    "rule", "idempotency_key", "reverses", "note", "caveats", "recorded_by",
    "evidence",
)
_INSTANT_FIELDS = ("period_start_utc", "period_end_utc", "recorded_at_utc")
_INTEGER_FIELDS = ("quantity", "waived_quantity")


def hashable(row: dict) -> dict:
    """``row`` reduced to the hashed fields in their canonical types."""
    out = {}
    for field in HASHED_FIELDS:
        value = row.get(field)
        if field in _INSTANT_FIELDS:
            value = timeutil.iso_exact(timeutil.as_utc(value))
        elif field in _INTEGER_FIELDS:
            value = int(value or 0)
        elif value in (None, ""):
            value = None
        else:
            value = str(value)
        out[field] = value
    return out


def record_hash(row: dict) -> str:
    """SHA-256 over a record's content. ``evidence`` is hashed as the stored text."""
    return sha256_hex(canonical_json(hashable(row)))


# -- outcome ----------------------------------------------------------------


def outcome(*, meter: str, dimension: str, quantity: int, waived_quantity: int, waived_reason: str | None,
            period_start_utc, period_end_utc, derived: dict | None = None, caveats: list[str] | None = None) -> dict:
    """What a rule decided. Enforces the invariants every record relies on."""
    quantity, waived_quantity = int(quantity), int(waived_quantity)
    if quantity < 0 or waived_quantity < 0:
        raise ValueError("a rule cannot produce a negative quantity")
    if quantity and waived_quantity:
        raise ValueError("usage either counts or is waived, never both")
    if waived_quantity and not waived_reason:
        raise ValueError("waived usage needs a reason")
    if period_end_utc < period_start_utc:
        raise ValueError("a record's interval cannot end before it starts")
    return {
        "meter": meter,
        "unit": UNITS[meter],
        "dimension": dimension,
        "quantity": quantity,
        "waived_quantity": waived_quantity,
        "waived_reason": waived_reason or None,
        "period_start_utc": period_start_utc,
        "period_end_utc": period_end_utc,
        "derived": derived or {},
        "caveats": list(caveats or []),
    }


# -- compute/v1 -----------------------------------------------------------------


def compute_dimension(provider, instance_class) -> str:
    """What ran, as the key a price list will price: ``aws/cpu-large``."""
    return f"{(provider or 'unknown').strip() or 'unknown'}/{(instance_class or 'unknown').strip() or 'unknown'}"


def compute_usage(inputs: dict) -> dict:
    """Machine time for one ``WebODM Compute Instance``.

    Inputs (as read from the instance): ``status``, ``requested_at``,
    ``ready_at``, ``dispatched_at``, ``released_at``, ``terminated_at``
    (local wall-clock strings or ``None``), ``destroy_attempts``,
    ``release_reason``, ``provider``, ``instance_class``,
    ``site_time_zone``, ``work_evidence`` (``"dispatched_at"``,
    ``"task_link"`` or ``None``), ``captured`` and ``observed_at_utc`` (when
    the recorder looked).

    The interval is ``ready_at`` → ``released_at``: from the machine
    becoming useful to the platform no longer needing it. It **counts** only
    when the machine became Ready, a job was dispatched to it, the platform
    did not kill or fail it, and both ends are known and in order. Anything
    else is waived, most specific reason first.

    Rows closed before ``released_at`` existed fall back to
    ``terminated_at`` — but only when the destroy succeeded first time. If
    it was retried, ``terminated_at`` includes the time the platform spent
    failing to switch the machine off, and that time is not the customer's.
    """
    zone = inputs["site_time_zone"]
    observed = timeutil.from_iso_z(inputs["observed_at_utc"])
    ambiguous = False

    def to_utc(field: str, edge: str):
        nonlocal ambiguous
        local = timeutil.parse_local(inputs.get(field))
        if local is None:
            return None
        utc, was_ambiguous = timeutil.local_to_utc(local, zone, edge=edge)
        ambiguous = ambiguous or was_ambiguous
        return timeutil.ceil_second(utc) if edge == "start" else timeutil.floor_second(utc)

    ready = to_utc("ready_at", "start")
    requested = to_utc("requested_at", "start")
    released = to_utc("released_at", "end")
    terminated = to_utc("terminated_at", "end")
    retried = int(inputs.get("destroy_attempts") or 0) > 0

    if released is not None:
        end, end_basis = released, "released_at"
    elif terminated is not None:
        end, end_basis = terminated, "terminated_at"
    else:
        end, end_basis = None, None
    start, start_basis = (ready, "ready_at") if ready is not None else (requested, "requested_at")

    closed = end is not None
    if not closed and inputs.get("captured") != CAPTURED_BEFORE_DELETE:
        # Recording a machine that is still up would use its one record on a
        # number that is still growing.
        raise ValueError("the instance is not closed yet")
    measured_end = end if closed else observed
    measured = timeutil.whole_seconds(start, measured_end) if start is not None else 0

    status = inputs.get("status")
    platform_killed = status == "Failed" or inputs.get("release_reason") in PLATFORM_RELEASE_REASONS

    if not closed:
        reason = WAIVED_DELETED_BEFORE_RELEASE
    elif ready is None:
        reason = WAIVED_NEVER_READY
    elif end < ready:
        reason, measured = WAIVED_INVALID_INTERVAL, 0
    elif not inputs.get("work_evidence"):
        reason = WAIVED_NO_WORK
    elif platform_killed:
        reason = WAIVED_PLATFORM_FAILURE
    elif end_basis == "terminated_at" and retried:
        reason = WAIVED_END_NOT_RECORDED
    else:
        reason = None

    period_end = measured_end
    period_start = start if start is not None and start <= period_end else period_end

    caveats = []
    if ambiguous:
        caveats.append(
            "a source time fell in an hour the site's clock repeated or skipped (daylight saving); "
            "it was read so as to shorten the interval"
        )
    if reason is None and inputs.get("work_evidence") == "task_link":
        caveats.append(
            "the instance predates dispatched_at; that it did work was taken from its task, "
            "which still pointed at it with a node task id"
        )
    if reason is None and end_basis == "terminated_at":
        caveats.append("the instance predates released_at; the interval ends at terminated_at (destroy succeeded first time)")

    return outcome(
        meter=METER_COMPUTE,
        dimension=compute_dimension(inputs.get("provider"), inputs.get("instance_class")),
        quantity=measured if reason is None else 0,
        waived_quantity=0 if reason is None else measured,
        waived_reason=reason,
        period_start_utc=period_start,
        period_end_utc=period_end,
        derived={
            "start_utc": timeutil.iso_z(start),
            "start_basis": start_basis if start is not None else None,
            "end_utc": timeutil.iso_z(end),
            "end_basis": end_basis,
            "measured_seconds": measured,
            "counts": reason is None,
            "why_not": reason,
            "ambiguous_local_time": ambiguous,
        },
        caveats=caveats,
    )


# -- analysis_run/v1 ------------------------------------------------------------


def analysis_run_usage(inputs: dict) -> dict:
    """One ``WebODM Plugin Run`` that will not change state again.

    Inputs: ``status``, ``creation``, ``started_at``, ``completed_at``,
    ``modified``, ``plugin``, ``site_time_zone``, ``captured``,
    ``observed_at_utc`` (and descriptive facts the rule does not use: plugin
    type, source, product, release, version, task, reference task).

    A ``Completed`` run counts as one run. A ``Failed`` or ``Cancelled`` run,
    and a run deleted while still ``Queued`` or ``Running``, produced nothing:
    it is recorded as one run waived.

    The interval is ``started_at`` → ``completed_at``. A run that never
    started has a zero-length interval at the moment it ended.
    """
    zone = inputs["site_time_zone"]
    observed = timeutil.from_iso_z(inputs["observed_at_utc"])
    ambiguous = False

    def to_utc(field: str, edge: str):
        nonlocal ambiguous
        local = timeutil.parse_local(inputs.get(field))
        if local is None:
            return None
        utc, was_ambiguous = timeutil.local_to_utc(local, zone, edge=edge)
        ambiguous = ambiguous or was_ambiguous
        return timeutil.ceil_second(utc) if edge == "start" else timeutil.floor_second(utc)

    status = inputs.get("status")
    if status == "Completed":
        reason = None
    elif status == "Failed":
        reason = WAIVED_FAILED
    elif status == "Cancelled":
        reason = WAIVED_CANCELLED
    elif inputs.get("captured") == CAPTURED_BEFORE_DELETE:
        reason = WAIVED_DELETED_BEFORE_FINISHING
    else:
        # A run that can still complete must not be recorded: the record
        # would say "produced nothing" about a run that may yet produce.
        raise ValueError(f"the run is not finished (status {status!r})")

    if reason == WAIVED_DELETED_BEFORE_FINISHING:
        end, end_basis = observed, "observed_at_utc"
    else:
        end, end_basis = to_utc("completed_at", "end"), "completed_at"
        if end is None:
            end, end_basis = to_utc("modified", "end"), "modified"
        if end is None:
            end, end_basis = observed, "observed_at_utc"
    start = to_utc("started_at", "start")
    start_basis = "started_at"
    if start is None or start > end:
        start, start_basis = end, end_basis

    caveats = []
    if ambiguous:
        caveats.append(
            "a source time fell in an hour the site's clock repeated or skipped (daylight saving); "
            "it was read so as to shorten the interval"
        )
    if reason is None and end_basis != "completed_at":
        caveats.append(f"the run has no completed_at; its interval ends at {end_basis}")

    return outcome(
        meter=METER_ANALYSIS_RUN,
        dimension=str(inputs.get("plugin") or "unknown"),
        quantity=1 if reason is None else 0,
        waived_quantity=0 if reason is None else 1,
        waived_reason=reason,
        period_start_utc=start,
        period_end_utc=end,
        derived={
            "start_utc": timeutil.iso_z(start),
            "start_basis": start_basis,
            "end_utc": timeutil.iso_z(end),
            "end_basis": end_basis,
            "duration_seconds": timeutil.whole_seconds(start, end),
            "counts": reason is None,
            "why_not": reason,
            "ambiguous_local_time": ambiguous,
        },
        caveats=caveats,
    )


# -- storage/v1 ---------------------------------------------------------------


def storage_usage(inputs: dict) -> dict:
    """Bytes one organization held on one UTC day.

    Inputs: ``day`` (``YYYY-MM-DD``, UTC), ``sampled_at_utc``, ``basis`` and
    ``lines`` — one per document that has files: ``doctype``, ``name``,
    ``label``, ``files``, ``bytes``, ``unsized`` (files whose row has no
    size, counted as 0 bytes).

    The quantity is the sum of the lines' bytes, in byte-days: the level
    measured at ``sampled_at_utc`` stands for the whole of ``day``. An
    organization with no files has no lines and a quantity of 0 — which is a
    measurement, and different from no record at all.
    """
    day = timeutil.parse_day(inputs["day"])
    start, end = timeutil.day_bounds(day)
    sampled = timeutil.from_iso_z(inputs["sampled_at_utc"])
    if not (start <= sampled < end):
        raise ValueError("a storage measurement belongs to the UTC day it was taken on")

    total_bytes = total_files = unsized = 0
    by_doctype: dict[str, dict] = {}
    seen = set()
    for line in inputs.get("lines") or []:
        key = (line["doctype"], line["name"])
        if key in seen:
            raise ValueError(f"{line['doctype']} {line['name']} is listed twice")
        seen.add(key)
        files, size, missing = int(line["files"]), int(line["bytes"]), int(line.get("unsized") or 0)
        if files < 0 or size < 0 or missing < 0:
            raise ValueError("a storage line cannot be negative")
        total_bytes += size
        total_files += files
        unsized += missing
        bucket = by_doctype.setdefault(line["doctype"], {"documents": 0, "files": 0, "bytes": 0})
        bucket["documents"] += 1
        bucket["files"] += files
        bucket["bytes"] += size

    caveats = []
    if unsized:
        caveats.append(f"{unsized} file(s) have no recorded size and were counted as 0 bytes")

    return outcome(
        meter=METER_STORAGE,
        dimension=STORAGE_DIMENSION,
        quantity=total_bytes,
        waived_quantity=0,
        waived_reason=None,
        period_start_utc=start,
        period_end_utc=end,
        derived={
            "bytes": total_bytes,
            "files": total_files,
            "documents": len(seen),
            "unsized_files": unsized,
            "by_doctype": by_doctype,
        },
        caveats=caveats,
    )


# -- evidence -----------------------------------------------------------------

RULES = {
    RULE_COMPUTE: compute_usage,
    RULE_ANALYSIS_RUN: analysis_run_usage,
    RULE_STORAGE: storage_usage,
}

_NOT_CHECKED = {
    RULE_COMPUTE: COMPUTE_NOT_CHECKED,
    RULE_ANALYSIS_RUN: ANALYSIS_RUN_NOT_CHECKED,
    RULE_STORAGE: STORAGE_NOT_CHECKED,
}
_CHECKED = {RULE_STORAGE: STORAGE_CHECKED}


def evidence_for(rule: str, inputs: dict, result: dict) -> dict:
    """What a record keeps so its quantity can be rebuilt and explained."""
    return {
        "rule": rule,
        "inputs": inputs,
        "derived": result["derived"],
        "checked": list(_CHECKED.get(rule, ())),
        "not_checked": list(_NOT_CHECKED.get(rule, ())),
    }


def rederive(evidence: dict) -> dict:
    """Run the rule named in ``evidence`` on the inputs stored in it."""
    rule = evidence.get("rule")
    if rule not in RULES:
        raise ValueError(f"unknown rule {rule!r}")
    return RULES[rule](evidence["inputs"])
