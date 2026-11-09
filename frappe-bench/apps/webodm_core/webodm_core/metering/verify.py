"""Checking the ledger against itself.

"If a total cannot be rebuilt from the records, the total is wrong." This
module is how that is checked rather than asserted:

* :func:`verify` goes through usage records and, for each one, recomputes
  its content hash, runs the rule named on the row over the evidence stored
  on the row and compares the result with what the row says, checks that a
  storage quantity equals the sum of its lines, that an idempotency key
  matches its source and that a reversal exactly negates its original. It
  then adds the totals up twice — once in Python from the rows, once in the
  database — and compares.
* :func:`trace` takes one record and lays out where its number came from:
  the stored evidence, the quantity re-derived from it, and — when the
  source row still exists — whether the source still says what it said.

Neither function changes anything. A problem found here is a finding to
investigate, never something to "repair" by editing a row: corrections are
reversals (``ledger.reverse``).

Run from the bench::

    bench --site <site> execute webodm_core.metering.verify.verify
    bench --site <site> execute webodm_core.metering.verify.verify \\
        --kwargs '{"organization": "Acme", "start": "2026-10-01", "end": "2026-11-01"}'
"""

from __future__ import annotations

import json

import frappe

from webodm_core.metering import ledger, rules, sweep, timeutil

_COMPARED = ("meter", "unit", "dimension", "quantity", "waived_quantity", "waived_reason")
_SOURCE_FIELDS_NOT_COMPARED = ("observed_at_utc", "captured", "work_evidence")


def verify(organization: str | None = None, start=None, end=None) -> dict:
    """Check every usage record in scope. Returns ``{"ok", "checked", "problems", "totals"}``.

    Scope: all organizations unless one is named; all time unless a period
    ``(start, end]`` is given. ``problems`` is a list of ``{"record",
    "problem", "detail"}``; an empty list is the only good answer.
    """
    filters = _scope(organization, start, end)
    rows = frappe.get_all(ledger.DOCTYPE, filters=filters, fields=list(ledger.ALL_FIELDS),
                          order_by="period_end_utc asc, name asc", limit_page_length=0)
    problems: list[dict] = []
    by_name = {row["name"]: row for row in rows}
    totals: dict[str, dict] = {}

    for row in rows:
        total = totals.setdefault(row["meter"], {"quantity": 0, "waived_quantity": 0, "records": 0})
        total["quantity"] += int(row["quantity"] or 0)
        total["waived_quantity"] += int(row["waived_quantity"] or 0)
        total["records"] += 1
        for problem, detail in _check_row(row, by_name):
            problems.append({"record": row["name"], "problem": problem, "detail": detail})

    for key, count in _duplicate_keys():
        problems.append({"record": None, "problem": "duplicate_idempotency_key",
                         "detail": f"{key} is held by {count} records"})
    for problem in _check_totals(filters, totals):
        problems.append(problem)

    return {"ok": not problems, "checked": len(rows), "problems": problems, "totals": totals}


def _scope(organization, start, end) -> list:
    filters = []
    if organization:
        filters.append(["organization", "=", organization])
    if start not in (None, ""):
        filters.append(["period_end_utc", ">", timeutil.parse_instant(start)])
    if end not in (None, ""):
        filters.append(["period_end_utc", "<=", timeutil.parse_instant(end)])
    return filters


def _check_row(row: dict, by_name: dict):
    if rules.record_hash(row) != row["record_hash"]:
        yield "hash_mismatch", "the row no longer has the content it was written with"

    try:
        evidence = json.loads(row["evidence"] or "")
        if not isinstance(evidence, dict):
            raise ValueError("not an object")
    except ValueError as e:
        yield "evidence_unreadable", str(e)
        return

    if evidence.get("rule") != row["rule"]:
        yield "rule_mismatch", f"row says {row['rule']!r}, evidence says {evidence.get('rule')!r}"

    if row["entry_type"] == ledger.ENTRY_REVERSAL:
        yield from _check_reversal(row, evidence, by_name)
        return

    if int(row["quantity"] or 0) < 0 or int(row["waived_quantity"] or 0) < 0:
        yield "negative_usage", "only a reversal may be negative"
    if int(row["quantity"] or 0) and int(row["waived_quantity"] or 0):
        yield "counted_and_waived", "a record either counts or is waived"

    try:
        again = rules.rederive(evidence)
    except Exception as e:
        yield "cannot_rederive", f"{e.__class__.__name__}: {e}"
        return
    for field in _COMPARED:
        stored = row[field] if field not in ("quantity", "waived_quantity") else int(row[field] or 0)
        if not _same(stored, again[field]):
            yield f"{field}_mismatch", f"row has {stored!r}, the rule gives {again[field]!r} from the row's evidence"
    for field in ("period_start_utc", "period_end_utc"):
        if timeutil.as_utc(row[field]) != again[field]:
            yield f"{field}_mismatch", (f"row has {timeutil.iso_z(timeutil.as_utc(row[field]))}, "
                                        f"the rule gives {timeutil.iso_z(again[field])}")

    if row["meter"] == rules.METER_STORAGE:
        lines = (evidence.get("inputs") or {}).get("lines") or []
        if sum(int(line.get("bytes") or 0) for line in lines) != int(row["quantity"] or 0):
            yield "storage_lines_do_not_add_up", "the quantity is not the sum of the evidence lines"

    expected_key = _expected_key(row, evidence)
    if expected_key and expected_key != row["idempotency_key"]:
        yield "key_mismatch", f"expected {expected_key!r} for this source"


def _same(a, b) -> bool:
    """Equal, treating an empty string and ``None`` as the same absence."""
    return (None if a in (None, "") else a) == (None if b in (None, "") else b)


def _expected_key(row: dict, evidence: dict) -> str | None:
    if row["meter"] == rules.METER_COMPUTE:
        return ledger.compute_key(row["source_name"])
    if row["meter"] == rules.METER_ANALYSIS_RUN:
        return ledger.analysis_run_key(row["source_name"])
    if row["meter"] == rules.METER_STORAGE:
        day = (evidence.get("inputs") or {}).get("day")
        return ledger.storage_key(row["organization"], day) if day else None
    return None


def _check_reversal(row: dict, evidence: dict, by_name: dict):
    original = by_name.get(row["reverses"]) or frappe.db.get_value(
        ledger.DOCTYPE, row["reverses"], list(ledger.ALL_FIELDS), as_dict=True)
    if not original:
        yield "reversal_without_original", f"{row['reverses']} does not exist"
        return
    if original["entry_type"] != ledger.ENTRY_USAGE:
        yield "reversal_of_a_reversal", row["reverses"]
    for field in ("organization", "meter", "dimension", "unit"):
        if row[field] != original[field]:
            yield "reversal_differs_from_original", f"{field}: {row[field]!r} vs {original[field]!r}"
    for field in ("period_start_utc", "period_end_utc"):
        if timeutil.as_utc(row[field]) != timeutil.as_utc(original[field]):
            yield "reversal_differs_from_original", field
    if int(row["quantity"] or 0) != -int(original["quantity"] or 0) or \
            int(row["waived_quantity"] or 0) != -int(original["waived_quantity"] or 0):
        yield "reversal_does_not_negate", "quantities are not the exact negation of the original"
    if (evidence.get("inputs") or {}).get("original_hash") != original["record_hash"]:
        yield "original_changed_since_reversal", "the original's hash differs from the one the reversal recorded"
    if row["idempotency_key"] != ledger.reversal_key(row["reverses"]):
        yield "key_mismatch", f"expected {ledger.reversal_key(row['reverses'])!r}"


def _duplicate_keys():
    # The unique index makes this impossible. It is checked anyway: the index
    # is the guarantee, and a guarantee is worth one query to confirm.
    return frappe.db.sql(
        f"SELECT idempotency_key, COUNT(*) FROM `tab{ledger.DOCTYPE}` GROUP BY idempotency_key HAVING COUNT(*) > 1"
    )


def _check_totals(filters: list, totals: dict):
    """Add the same rows up again, in the database, and compare.

    Sums leave the database as text and are parsed as integers: a numeric
    ``SUM`` would come back as a float (see ``storage.measure``).
    """
    conditions, values = ["1 = 1"], []
    for field, operator, value in filters:
        conditions.append(f"`{field}` {operator} %s")
        values.append(value)
    rows = frappe.db.sql(
        f"""
        SELECT meter, CONCAT(COALESCE(SUM(quantity), 0), ''), CONCAT(COALESCE(SUM(waived_quantity), 0), ''), COUNT(*)
        FROM `tab{ledger.DOCTYPE}` WHERE {' AND '.join(conditions)} GROUP BY meter
        """,
        tuple(values),
    )
    in_database = {meter: {"quantity": int(q), "waived_quantity": int(w), "records": int(n)} for meter, q, w, n in rows}
    if in_database != totals:
        yield {"record": None, "problem": "totals_do_not_agree",
               "detail": f"summed from rows: {totals}; summed in the database: {in_database}"}


# -- one record, explained ----------------------------------------------------


def trace(name: str) -> dict | None:
    """Where one record's number came from, and whether its source still agrees.

    ``source.differs`` lists the facts that are different on the source row
    *now*. That is information, not an error: a record states what was true
    when it was written and is not revised when the source changes later —
    but an operator answering a customer wants to know.
    """
    record = ledger.get(name)
    if not record:
        return None
    row = frappe.db.get_value(ledger.DOCTYPE, name, list(ledger.ALL_FIELDS), as_dict=True)
    problems = [{"problem": p, "detail": d} for p, d in _check_row(row, {})]
    out = {"record": record, "problems": problems, "rederived": None, "source": None}
    evidence = record["evidence"] or {}

    if record["entry_type"] == ledger.ENTRY_USAGE:
        try:
            again = rules.rederive(evidence)
            rederived = {
                "quantity": again["quantity"], "waived_quantity": again["waived_quantity"],
                "waived_reason": again["waived_reason"],
                "period_start_utc": timeutil.iso_z(again["period_start_utc"]),
                "period_end_utc": timeutil.iso_z(again["period_end_utc"]),
            }
            # Does the number on the record follow from the record's own
            # evidence? (A changed hash alone does not answer that.)
            rederived["matches_record"] = all(_same(rederived[field], record[field]) for field in rederived)
            out["rederived"] = rederived
        except Exception as e:
            out["rederived"] = {"error": f"{e.__class__.__name__}: {e}"}

    doctype, source_name = record["source_doctype"], record["source_name"]
    if record["meter"] == rules.METER_STORAGE:
        out["source"] = {"exists": None,
                         "note": "a storage measurement cannot be taken again; its evidence lines are the record of it"}
    elif doctype in (sweep.INSTANCE, sweep.RUN):
        fields = sweep.INSTANCE_FIELDS if doctype == sweep.INSTANCE else sweep.RUN_FIELDS
        current = frappe.db.get_value(doctype, source_name, list(fields), as_dict=True)
        if not current:
            out["source"] = {"exists": False, "note": "the source row has been deleted; the evidence on the record stands"}
        else:
            now = (sweep.instance_inputs if doctype == sweep.INSTANCE else sweep.run_inputs)(current)
            then = evidence.get("inputs") or {}
            differs = sorted(
                key for key in set(now) | set(then)
                if key not in _SOURCE_FIELDS_NOT_COMPARED and now.get(key) != then.get(key)
            )
            out["source"] = {"exists": True, "differs": differs,
                             "now": {key: now.get(key) for key in differs},
                             "when_recorded": {key: then.get(key) for key in differs}}
    return out
