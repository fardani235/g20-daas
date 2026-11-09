"""The usage ledger: the only code that writes ``WebODM Usage Record``.

``record`` is how a fact gets in; ``reverse`` is how a mistake is corrected.
There is no update and no delete.

**A fact is recorded once.** Every record has an ``idempotency_key`` built
from the identity of the fact (``compute:<instance>``,
``analysis_run:<run>``, ``storage:<org>:<day>``, ``reversal:<record>``) and
the column has a unique index. ``record`` looks first and inserts inside a
savepoint; if the database refuses the insert as a duplicate, another
recorder got in between the look and the insert, and the fact is recorded —
which is all the caller asked for. So two recorders running at the same
moment produce one row, and neither of them fails. The row that committed
first stands; it is never compared with, merged into or replaced by a later
attempt.

**One-time facts.** Usage is recorded by catching up, because a finished
machine or run is still there to be found. Something like a top-up is not:
nothing later can find it, so it has to be written when it happens. That
is a balance fact, not usage, and it does not belong in this table — but
it should be written the way ``_insert`` writes: an idempotency key taken
from the event's own identity (a payment reference, say) under a unique
index, an insert inside a savepoint, and a refusal treated as "already
recorded", so that a retried request records once.
"""

from __future__ import annotations

import json

import frappe

from webodm_core.metering import guard, rules, timeutil

DOCTYPE = "WebODM Usage Record"

ENTRY_USAGE = "usage"
ENTRY_REVERSAL = "reversal"

# The columns of a record, without the evidence text (which can be large).
SUMMARY_FIELDS = (
    "name", "organization", "meter", "dimension", "entry_type", "quantity", "unit",
    "waived_quantity", "waived_reason", "period_start_utc", "period_end_utc", "recorded_at_utc",
    "recorded_by", "source_doctype", "source_name", "rule", "task", "project", "idempotency_key",
    "reverses", "note", "caveats", "record_hash",
)
ALL_FIELDS = (*SUMMARY_FIELDS, "evidence")


class AlreadyReversed(frappe.ValidationError):
    """The record has a reversal already; a fact can be taken back once."""


# -- idempotency keys -----------------------------------------------------------


def compute_key(instance_name: str) -> str:
    return f"compute:{instance_name}"


def analysis_run_key(run_name: str) -> str:
    return f"analysis_run:{run_name}"


def storage_key(organization: str, day) -> str:
    return f"storage:{organization}:{timeutil.parse_day(day).isoformat()}"


def reversal_key(record_name: str) -> str:
    return f"reversal:{record_name}"


def exists(idempotency_key: str) -> str | None:
    """The name of the record holding ``idempotency_key``, if this transaction can see one."""
    return frappe.db.get_value(DOCTYPE, {"idempotency_key": idempotency_key}, "name")


# -- writing ------------------------------------------------------------------


def record(*, organization: str, rule: str, inputs: dict, result: dict, source_doctype: str, source_name: str,
           idempotency_key: str, task: str | None = None, project: str | None = None) -> tuple[str | None, bool]:
    """Record one fact. Returns ``(record name, created)``.

    ``result`` is what ``rules.RULES[rule](inputs)`` returned; ``inputs`` are
    stored with it as the record's evidence, so the quantity can be rebuilt
    from the row alone. ``created`` is ``False`` when the fact was already
    recorded — the existing row is left untouched. Its name is returned
    when this transaction can see it, and ``None`` when another recorder
    committed it a moment ago (see ``_insert``).
    """
    if not organization:
        raise ValueError(f"{source_doctype} {source_name} has no organization; usage cannot be attributed")
    evidence = rules.canonical_json(rules.evidence_for(rule, inputs, result))
    return _insert({
        "organization": organization,
        "meter": result["meter"],
        "dimension": result["dimension"],
        "entry_type": ENTRY_USAGE,
        "quantity": result["quantity"],
        "unit": result["unit"],
        "waived_quantity": result["waived_quantity"],
        "waived_reason": result["waived_reason"],
        "period_start_utc": result["period_start_utc"],
        "period_end_utc": result["period_end_utc"],
        "source_doctype": source_doctype,
        "source_name": source_name,
        "rule": rule,
        "task": task or None,
        "project": project or None,
        "idempotency_key": idempotency_key,
        "reverses": None,
        "note": None,
        "caveats": "\n".join(result["caveats"]) or None,
        "evidence": evidence,
    })


def reverse(name: str, note: str, *, ignore_permissions: bool = False) -> str:
    """Take a record back by adding its negation. Returns the reversal's name.

    The original is not touched. The reversal carries the same meter,
    dimension, interval and source with the quantities negated, so it nets
    the original out of the same period; it names the original, the person
    who reversed it and why. A record can be reversed once, and a reversal
    cannot be reversed — to restore the fact, record it again under a new
    idempotency key and say so in its evidence.
    """
    from webodm_core import tenancy

    if not ignore_permissions and not tenancy.is_platform_admin():
        frappe.throw("Only platform administrators can reverse a usage record", frappe.PermissionError)
    note = (note or "").strip()
    if not note:
        frappe.throw("A reversal needs a note saying why")
    original = frappe.db.get_value(DOCTYPE, name, list(ALL_FIELDS), as_dict=True)
    if not original:
        frappe.throw(f"Usage record {name} does not exist", frappe.DoesNotExistError)
    if original.entry_type != ENTRY_USAGE:
        frappe.throw("A reversal cannot be reversed")
    key = reversal_key(name)
    if exists(key):
        frappe.throw(f"Usage record {name} has already been reversed", AlreadyReversed)

    evidence = rules.canonical_json({
        "rule": rules.RULE_REVERSAL,
        "inputs": {
            "reverses": name,
            "original_hash": original.record_hash,
            "original_quantity": int(original.quantity or 0),
            "original_waived_quantity": int(original.waived_quantity or 0),
            "note": note,
            "reversed_by": frappe.session.user,
        },
        "derived": {},
        "checked": [],
        "not_checked": ["whether the original was in fact wrong: a reversal records a decision, not a measurement"],
    })
    reversal, created = _insert({
        "organization": original.organization,
        "meter": original.meter,
        "dimension": original.dimension,
        "entry_type": ENTRY_REVERSAL,
        "quantity": -int(original.quantity or 0),
        "unit": original.unit,
        "waived_quantity": -int(original.waived_quantity or 0),
        "waived_reason": original.waived_reason,
        "period_start_utc": timeutil.as_utc(original.period_start_utc),
        "period_end_utc": timeutil.as_utc(original.period_end_utc),
        "source_doctype": original.source_doctype,
        "source_name": original.source_name,
        "rule": rules.RULE_REVERSAL,
        "task": original.task,
        "project": original.project,
        "idempotency_key": key,
        "reverses": name,
        "note": note,
        "caveats": None,
        "evidence": evidence,
    })
    if not created:
        # Someone else reversed it between the check and the insert.
        frappe.throw(f"Usage record {name} has already been reversed", AlreadyReversed)
    return reversal


def _insert(row: dict) -> tuple[str | None, bool]:
    key = row["idempotency_key"]
    existing = exists(key)
    if existing:
        return existing, False

    row = dict(row)
    row["recorded_at_utc"] = timeutil.utc_now()
    row["recorded_by"] = frappe.session.user
    row["record_hash"] = rules.record_hash(row)

    doc = frappe.get_doc({"doctype": DOCTYPE, **row})
    doc.flags.from_ledger = True
    savepoint = guard.savepoint_name("usage")
    frappe.db.savepoint(savepoint)
    try:
        doc.insert(ignore_permissions=True)
    except Exception as e:
        # Whatever went wrong, the transaction must stay usable for the caller.
        frappe.db.rollback(save_point=savepoint)
        if _is_duplicate_key(e):
            # Another recorder got in between the look above and this insert.
            # The database refusing the row *is* the proof that the fact is
            # recorded: an insert that collides with a row another
            # transaction has not committed yet waits for it, and fails only
            # once that row is committed.
            #
            # We may not be able to *see* the winner's row. Frappe runs
            # PostgreSQL at REPEATABLE READ, so this transaction still sees
            # the world as it was before the winner committed; then the
            # name is ``None`` — "recorded, by someone else, just now".
            frappe.clear_last_message()
            return exists(key), False
        raise
    frappe.db.release_savepoint(savepoint)
    return doc.name, True


_UNIQUE_VIOLATION_POSTGRES = "23505"
_DUPLICATE_ENTRY_MARIADB = 1062


def _is_duplicate_key(e: Exception) -> bool:
    """The insert was refused by the unique index on ``idempotency_key``."""
    if isinstance(e, frappe.UniqueValidationError):
        return True
    for error in (e, getattr(e, "__cause__", None), *(arg for arg in e.args if isinstance(arg, Exception))):
        if error is None:
            continue
        if getattr(error, "pgcode", None) == _UNIQUE_VIOLATION_POSTGRES:
            return "idempotency_key" in str(error)
        if getattr(error, "args", None) and error.args[0] == _DUPLICATE_ENTRY_MARIADB:
            return "idempotency_key" in str(error)
    return False


# -- reading ------------------------------------------------------------------


def get(name: str) -> dict | None:
    """A record with its evidence parsed; ``None`` when it does not exist."""
    row = frappe.db.get_value(DOCTYPE, name, list(ALL_FIELDS), as_dict=True)
    return present(row, with_evidence=True) if row else None


def present(row: dict, *, with_evidence: bool = False) -> dict:
    """A record as it leaves the module: instants as ``…Z`` strings, integers as integers."""
    out = {
        "name": row["name"],
        "organization": row["organization"],
        "meter": row["meter"],
        "dimension": row["dimension"],
        "entry_type": row["entry_type"],
        "quantity": int(row["quantity"] or 0),
        "unit": row["unit"],
        "waived_quantity": int(row["waived_quantity"] or 0),
        "waived_reason": row["waived_reason"] or None,
        "period_start_utc": timeutil.iso_z(timeutil.as_utc(row["period_start_utc"])),
        "period_end_utc": timeutil.iso_z(timeutil.as_utc(row["period_end_utc"])),
        "recorded_at_utc": timeutil.iso_z(timeutil.as_utc(row["recorded_at_utc"])),
        "recorded_by": row["recorded_by"],
        "source_doctype": row["source_doctype"],
        "source_name": row["source_name"],
        "rule": row["rule"],
        "task": row["task"] or None,
        "project": row["project"] or None,
        "idempotency_key": row["idempotency_key"],
        "reverses": row["reverses"] or None,
        "note": row["note"] or None,
        "caveats": [line for line in (row["caveats"] or "").split("\n") if line],
        "record_hash": row["record_hash"],
    }
    if with_evidence:
        out["evidence"], out["evidence_unreadable"] = _parse_evidence(row.get("evidence"))
    return out


def _parse_evidence(text) -> tuple[dict | None, bool]:
    """``(evidence, unreadable)``. A row whose evidence was damaged must still be showable."""
    try:
        evidence = json.loads(text or "")
    except ValueError:
        return None, True
    return (evidence, False) if isinstance(evidence, dict) else (None, True)
