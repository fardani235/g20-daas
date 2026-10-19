"""Answering "what did this organization consume in this period?".

Everything here is computed from ``WebODM Usage Record`` rows at the moment
it is asked. There is no stored total, no roll-up table and no cache, so
there is nothing that can disagree with the records: a total *is* the sum of
the rows :func:`records` returns for the same arguments.

**Period.** ``(start, end]`` in UTC. A record belongs to the period in which
its interval *ended* (``start < period_end_utc <= end``) and to no other;
records are never split or pro-rated. A machine that ran across a month end
is in the month it was released in, whole.

**What a summary keeps apart**, so that no figure can be mistaken for
another:

* ``meters`` — totals of recorded facts whose interval has ended.
  ``quantity`` counts; ``waived_quantity`` was measured and deliberately
  not counted.
* ``in_progress`` — what is **in no total** yet: work that has not finished
  (a machine that is up, a run that is queued or running — computed live
  and marked ``provisional``), and today's storage measurement, which is
  fixed but stands for a day that is not over.
* ``unrecorded`` — finished sources the sweep has not recorded yet. A count:
  their quantities are not known until they are recorded.
* storage ``days`` — which UTC days of the period have a measurement and
  which do not. A day without one contributes nothing and is listed; it is
  never filled in.

``provisional`` says whether the totals can still change, and
``provisional_reasons`` says why. A storage day that was never measured does
not make an answer provisional — it will not change — it makes it
incomplete, and ``gaps`` lists it.

Functions take an explicit ``organization`` and do not check who is asking:
that is the caller's job (``api/usage.py`` resolves it from the session).
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

import frappe

from webodm_core.metering import ledger, rules, sweep, timeutil

METERS = (rules.METER_COMPUTE, rules.METER_ANALYSIS_RUN, rules.METER_STORAGE)

LIVE_INSTANCE_STATUSES = ("Requested", "Provisioning", "Ready")
LIVE_RUN_STATUSES = ("Queued", "Running")

MAX_PAGE = 1000
# Longest period one request may cover. A year with room to spare; a period
# is also a list of days, and "the year 1 to the year 9999" is not a question.
MAX_PERIOD_DAYS = 400


class InvalidPeriod(frappe.ValidationError):
    """The period's bounds are missing, ambiguous, out of order or too far apart."""


def period(start, end) -> tuple:
    """``(start, end)`` as naive UTC instants; refuses anything ambiguous."""
    try:
        start, end = timeutil.parse_instant(start), timeutil.parse_instant(end)
    except (ValueError, TypeError, OverflowError) as e:
        frappe.throw(f"Invalid period: {e}", InvalidPeriod)
    if end <= start:
        frappe.throw("Invalid period: end must be after start", InvalidPeriod)
    if (end - start).days > MAX_PERIOD_DAYS:
        frappe.throw(f"Invalid period: longer than {MAX_PERIOD_DAYS} days; ask for it in parts", InvalidPeriod)
    return start, end


def _as_of(as_of, now):
    if as_of in (None, ""):
        return None
    try:
        as_of = timeutil.parse_instant(as_of)
    except (ValueError, TypeError, OverflowError) as e:
        frappe.throw(f"Invalid as_of: {e}", InvalidPeriod)
    if as_of > now:
        # "As it stood at a moment that has not happened" has no answer, and
        # treating it as now would hide what is still in progress.
        frappe.throw("Invalid as_of: it is in the future", InvalidPeriod)
    return as_of


def _filters(organization: str, after, until, meter=None, as_of=None) -> list:
    """Records of ``organization`` whose interval ended in ``(after, until]``."""
    filters = [
        ["organization", "=", organization],
        ["period_end_utc", ">", after],
        ["period_end_utc", "<=", until],
    ]
    if meter:
        filters.append(["meter", "=", meter])
    if as_of is not None:
        filters.append(["recorded_at_utc", "<=", as_of])
    return filters


def _rows(organization: str, after, until, *, meter=None, as_of=None, fields=ledger.SUMMARY_FIELDS,
          limit=None, offset=0) -> list[dict]:
    return frappe.get_all(
        ledger.DOCTYPE,
        filters=_filters(organization, after, until, meter, as_of),
        fields=list(fields),
        order_by="period_end_utc asc, name asc",
        limit_page_length=limit or 0,
        limit_start=offset,
    )


# -- the answer -----------------------------------------------------------------


def summary(organization: str, start, end, *, as_of=None) -> dict:
    """Totals per meter for ``organization`` over ``(start, end]``.

    A total holds only records whose interval has **ended**: when the period
    is still open, that is everything up to now, not up to ``end``. (The one
    record that exists before its interval is over is today's storage
    measurement; it is listed under ``in_progress`` and counts from
    midnight UTC.)

    With ``as_of`` only records written up to that instant are used, and
    "now" is that instant: the answer as it stood then. Records never
    change, so this is exact for every record whose transaction had
    committed by then — a record written inside a longer request (the
    deletion hook) carries the time it was written, which can be seconds
    before it became visible. ``in_progress`` (live work) and
    ``unrecorded`` describe the present and are left out of an ``as_of``
    answer.
    """
    start, end = period(start, end)
    now = timeutil.utc_now()
    as_of = _as_of(as_of, now)
    reference = as_of or now
    counted_until = min(end, reference)

    rows = _rows(organization, start, counted_until, as_of=as_of)
    meters = {meter: _meter_block(meter, [r for r in rows if r["meter"] == meter]) for meter in METERS}
    # Measured, but the day is not over: exists, and is not in a total yet.
    day_running = _rows(organization, counted_until, end, meter=rules.METER_STORAGE, as_of=as_of) \
        if end > counted_until else []
    days = _storage_days(organization, start, end, [r for r in rows if r["meter"] == rules.METER_STORAGE],
                         day_running, reference, as_of)
    meters[rules.METER_STORAGE]["days"] = days

    reasons = []
    if end > reference:
        reasons.append({"code": "period_open", "detail": f"the period ends at {timeutil.iso_z(end)}, after now"})

    out = {
        "organization": organization,
        "period": {"start_utc": timeutil.iso_z(start), "end_utc": timeutil.iso_z(end),
                   "counted_until_utc": timeutil.iso_z(counted_until),
                   "rule": "a record belongs to the period in which its interval ended: start < period_end <= end; "
                           "nothing is counted before its interval has ended"},
        "generated_at_utc": timeutil.iso_z(now),
        "as_of_utc": timeutil.iso_z(as_of),
        "record_count": len(rows),
        "meters": meters,
    }

    if as_of is None:
        running = in_progress(organization, now=now) if end > now else []
        withdrawn = {row["reverses"] for row in day_running if row["entry_type"] == ledger.ENTRY_REVERSAL}
        running += [_storage_in_progress(row, now) for row in day_running
                    if row["entry_type"] == ledger.ENTRY_USAGE and row["name"] not in withdrawn]
        waiting = unrecorded(organization)
        out["in_progress"] = running
        out["unrecorded"] = waiting
        live = [item for item in running if item["provisional"]]
        if live:
            reasons.append({"code": "work_in_progress",
                            "detail": f"{len(live)} item(s) still running; they are not in any total"})
        if waiting["compute"] or waiting["analysis_run"]:
            reasons.append({
                "code": "sources_not_yet_recorded",
                "detail": f"{waiting['compute']} closed machine(s) and {waiting['analysis_run']} finished run(s) "
                          "are waiting for the sweep; totals may still rise",
            })
    if days["in_progress"]:
        reasons.append({"code": "storage_day_in_progress",
                        "detail": f"{', '.join(days['in_progress'])} is measured and counts once the day has ended"})
    if days["pending"] or days["not_started"]:
        reasons.append({"code": "storage_days_to_come",
                        "detail": f"{len(days['pending']) + len(days['not_started'])} day(s) of the period "
                                  "have not been measured yet and still can be"})

    # Provisional: the totals can still change. A gap is the opposite — a
    # hole that will never be filled — so it does not make an answer
    # provisional; it makes it incomplete, and it is listed.
    out["provisional"] = bool(reasons)
    out["provisional_reasons"] = reasons
    missing = days["not_measured"] + days["before_first_measurement"]
    out["gaps"] = {
        "storage_days_not_measured": missing,
        "storage_days_reversed": days["reversed"],
        "note": ("These days have no storage measurement and never will; nothing is counted for them."
                 if missing else None),
    }
    out["display"] = _display(meters)
    return out


def _meter_block(meter: str, rows: list[dict]) -> dict:
    by_dimension: dict[str, dict] = {}
    waived_by_reason: dict[str, int] = {}
    quantity = waived = usage_rows = reversals = 0
    for row in rows:
        q, w = int(row["quantity"] or 0), int(row["waived_quantity"] or 0)
        quantity += q
        waived += w
        if row["entry_type"] == ledger.ENTRY_REVERSAL:
            reversals += 1
        else:
            usage_rows += 1
        bucket = by_dimension.setdefault(row["dimension"] or "", {"quantity": 0, "waived_quantity": 0, "records": 0})
        bucket["quantity"] += q
        bucket["waived_quantity"] += w
        bucket["records"] += 1
        if row["waived_reason"]:
            waived_by_reason[row["waived_reason"]] = waived_by_reason.get(row["waived_reason"], 0) + w
    return {
        "unit": rules.UNITS[meter],
        "quantity": quantity,
        "waived_quantity": waived,
        "records": usage_rows,
        "reversals": reversals,
        "by_dimension": [{"dimension": key, **by_dimension[key]} for key in sorted(by_dimension)],
        "waived_by_reason": {key: waived_by_reason[key] for key in sorted(waived_by_reason)},
    }


def _storage_days(organization: str, start, end, storage_rows: list[dict], day_running: list[dict],
                  reference, as_of) -> dict:
    """Which UTC days of the period were measured — and which, plainly, were not.

    ``measured``           the day is over and has a measurement (it is in the total)
    ``in_progress``        measured, but the day has not ended (not in the total yet)
    ``pending``            the day has begun and its measurement has not been taken yet
    ``not_started``        the day has not begun
    ``not_measured``       the day is over and nothing measured it: a gap, for good
    ``before_first_measurement``  the same, before this organization was ever measured
    ``reversed``           measured, and the measurement was withdrawn
    """
    expected = timeutil.days_ending_in(start, end)
    measured, reversed_days = set(), set()
    for row in storage_rows:
        day = timeutil.as_utc(row["period_start_utc"]).date()
        (reversed_days if row["entry_type"] == ledger.ENTRY_REVERSAL else measured).add(day)
    running = {timeutil.as_utc(row["period_start_utc"]).date() for row in day_running
               if row["entry_type"] == ledger.ENTRY_USAGE}
    withdrawn_running = {timeutil.as_utc(row["period_start_utc"]).date() for row in day_running
                         if row["entry_type"] == ledger.ENTRY_REVERSAL}
    reversed_days |= withdrawn_running

    first = _first_measured_day(organization, as_of)
    out = {"in_period": len(expected), "measured": 0, "in_progress": [], "not_measured": [],
           "before_first_measurement": [], "pending": [], "not_started": [],
           "reversed": sorted(d.isoformat() for d in reversed_days),
           "first_measured_day": first.isoformat() if first else None}
    for day in expected:
        day_start, day_end = timeutil.day_bounds(day)
        if day in reversed_days:
            continue  # measured and then withdrawn: listed under "reversed", counts as nothing
        if day_end <= reference:
            if day in measured:
                out["measured"] += 1
            elif first is None or day < first:
                out["before_first_measurement"].append(day.isoformat())
            else:
                out["not_measured"].append(day.isoformat())
        elif day in running:
            out["in_progress"].append(day.isoformat())
        elif day_start <= reference:
            out["pending"].append(day.isoformat())  # today's measurement has not been taken yet
        else:
            out["not_started"].append(day.isoformat())
    return out


def _storage_in_progress(row: dict, now) -> dict:
    """Today's storage measurement, shown while its day is still running.

    Unlike the other in-progress items this one is not provisional — the
    measurement is taken and will not change — it is simply not counted
    until the day it stands for is over.
    """
    return {
        "meter": rules.METER_STORAGE,
        "unit": rules.UNITS[rules.METER_STORAGE],
        "dimension": row["dimension"],
        "source_doctype": ledger.DOCTYPE,
        "source_name": row["name"],
        "task": None,
        "state": "measured; the day has not ended",
        "day": timeutil.as_utc(row["period_start_utc"]).date().isoformat(),
        "quantity_when_the_day_ends": int(row["quantity"] or 0),
        "counts_from_utc": timeutil.iso_z(timeutil.as_utc(row["period_end_utc"])),
        "elapsed_so_far": 0,
        "provisional": False,
        "as_of_utc": timeutil.iso_z(now),
        "note": "fixed; it enters the storage total when the UTC day ends",
    }


def _first_measured_day(organization: str, as_of):
    filters = {"organization": organization, "meter": rules.METER_STORAGE, "entry_type": ledger.ENTRY_USAGE}
    if as_of is not None:
        filters["recorded_at_utc"] = ["<=", as_of]
    rows = frappe.get_all(ledger.DOCTYPE, filters=filters, fields=["period_start_utc"],
                          order_by="period_start_utc asc", limit_page_length=1)
    return timeutil.as_utc(rows[0]["period_start_utc"]).date() if rows else None


def records(organization: str, start, end, *, meter: str | None = None, as_of=None, limit: int = 100,
            offset: int = 0, with_evidence: bool = False) -> dict:
    """The records behind :func:`summary`, oldest first, in pages.

    Same arguments, same rows: summing ``quantity`` over every page for a
    meter gives that meter's total.
    """
    start, end = period(start, end)
    now = timeutil.utc_now()
    as_of = _as_of(as_of, now)
    counted_until = min(end, as_of or now)
    if meter and meter not in METERS:
        frappe.throw(f"Unknown meter '{meter}'")
    limit = max(1, min(int(limit or 100), MAX_PAGE))
    offset = max(0, int(offset or 0))
    fields = ledger.ALL_FIELDS if with_evidence else ledger.SUMMARY_FIELDS
    rows = _rows(organization, start, counted_until, meter=meter, as_of=as_of, fields=fields, limit=limit,
                 offset=offset)
    total = frappe.db.count(ledger.DOCTYPE, _filters(organization, start, counted_until, meter, as_of))
    return {
        "organization": organization,
        "period": {"start_utc": timeutil.iso_z(start), "end_utc": timeutil.iso_z(end),
                   "counted_until_utc": timeutil.iso_z(counted_until)},
        "as_of_utc": timeutil.iso_z(as_of),
        "total": total,
        "offset": offset,
        "limit": limit,
        "records": [ledger.present(row, with_evidence=with_evidence) for row in rows],
    }


# -- what is not in the totals --------------------------------------------------


def in_progress(organization: str, *, now=None) -> list[dict]:
    """Work of ``organization`` that has not finished. Provisional by nature.

    Nothing here is, or will be read from, a usage record: these are live
    source rows, described so that a reader knows more is coming. The
    elapsed time of a running machine is what it *would* count if it were
    released this second and had done work — it is not a charge.
    """
    now = now or timeutil.utc_now()
    zone = sweep.site_time_zone()
    items = []
    for inst in frappe.get_all(
        sweep.INSTANCE,
        # `None` -> IS NULL. (["is", "not set"] compares a timestamp with ''
        # and PostgreSQL refuses it.)
        filters={"organization": organization, "status": ["in", list(LIVE_INSTANCE_STATUSES)],
                 "released_at": None, "terminated_at": None},
        fields=["name", "task", "status", "provider", "instance_class", "requested_at", "ready_at", "dispatched_at"],
        order_by="creation asc",
    ):
        ready = None
        if inst.ready_at:
            ready = timeutil.ceil_second(timeutil.local_to_utc(timeutil.parse_local(inst.ready_at), zone, edge="start")[0])
        items.append({
            "meter": rules.METER_COMPUTE,
            "unit": rules.UNITS[rules.METER_COMPUTE],
            "dimension": rules.compute_dimension(inst.provider, inst.instance_class),
            "source_doctype": sweep.INSTANCE,
            "source_name": inst.name,
            "task": inst.task,
            "state": inst.status,
            "ready_at_utc": timeutil.iso_z(ready),
            "work_dispatched": bool(inst.dispatched_at),
            "elapsed_so_far": timeutil.whole_seconds(ready, now) if ready else 0,
            "provisional": True,
            "as_of_utc": timeutil.iso_z(now),
            "note": ("running; counts only once released, and only if a job was dispatched to it"
                     if ready else "not ready yet; nothing counts until it is"),
        })
    for run in frappe.get_all(
        sweep.RUN,
        filters={"organization": organization, "status": ["in", list(LIVE_RUN_STATUSES)]},
        fields=["name", "plugin", "task", "status", "started_at"],
        order_by="creation asc",
    ):
        items.append({
            "meter": rules.METER_ANALYSIS_RUN,
            "unit": rules.UNITS[rules.METER_ANALYSIS_RUN],
            "dimension": run.plugin,
            "source_doctype": sweep.RUN,
            "source_name": run.name,
            "task": run.task,
            "state": run.status,
            "elapsed_so_far": 0,
            "provisional": True,
            "as_of_utc": timeutil.iso_z(now),
            "note": "counts as one run if it completes; nothing if it fails or is cancelled",
        })
    return items


def unrecorded(organization: str | None = None) -> dict:
    """Finished sources that have no usage record yet (the sweep's to-do list)."""
    return {
        "compute": len(sweep.outstanding_instances(organization)),
        "analysis_run": len(sweep.outstanding_runs(organization)),
    }


# -- operations ---------------------------------------------------------------


def health() -> dict:
    """Is measurement keeping up? Platform-wide; for operators.

    ``storage.not_measured_today`` should be empty a few minutes after
    00:00 UTC and stay empty. ``unrecorded`` should be zero or small and not
    grow. Anything else means the scheduler or the workers are not running
    the two metering jobs — and for storage, a day that ends unmeasured is
    lost.
    """
    now = timeutil.utc_now()
    start, _ = timeutil.day_bounds(now.date())
    organizations = frappe.get_all("WebODM Organization", pluck="name", order_by="creation asc")
    measured = set(frappe.get_all(
        ledger.DOCTYPE,
        filters={"meter": rules.METER_STORAGE, "entry_type": ledger.ENTRY_USAGE, "period_start_utc": start},
        pluck="organization",
    ))
    latest = frappe.get_all(ledger.DOCTYPE, fields=["recorded_at_utc"], order_by="recorded_at_utc desc",
                            limit_page_length=1)
    return {
        "generated_at_utc": timeutil.iso_z(now),
        "storage": {
            "day": now.date().isoformat(),
            "organizations": len(organizations),
            "measured_today": len(measured & set(organizations)),
            "not_measured_today": [org for org in organizations if org not in measured],
        },
        "unrecorded": unrecorded(),
        "records": {
            "total": frappe.db.count(ledger.DOCTYPE),
            "latest_recorded_at_utc": timeutil.iso_z(timeutil.as_utc(latest[0]["recorded_at_utc"])) if latest else None,
        },
    }


# -- reading aid ----------------------------------------------------------------

_GB = Decimal(10) ** 9


def _display(meters: dict) -> dict:
    """Rounded strings for a person reading the answer. Never an input to anything."""
    seconds = meters[rules.METER_COMPUTE]["quantity"]
    storage = meters[rules.METER_STORAGE]
    measured = storage["days"]["measured"]
    average = None
    if measured:
        value = (Decimal(storage["quantity"]) / measured / _GB).quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN)
        average = f"{value} GB on average over {measured} measured day(s)"
    return {
        "note": "rounded for reading; use the integer quantities for any calculation",
        "compute": f"{seconds // 3600} h {seconds % 3600 // 60} min {seconds % 60} s of machine time counted",
        "analysis_run": f"{meters[rules.METER_ANALYSIS_RUN]['quantity']} analysis run(s) counted",
        "storage": average or "no storage measurement in this period",
    }
