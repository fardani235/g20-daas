"""Usage API: what an organization consumed, from the usage ledger.

Read-only. There is no endpoint that writes, changes or deletes a usage
record; recording is done by ``webodm_core.metering`` in background jobs.

**Whose usage.** The organization always comes from the session
(``tenancy.require_org``), never from the request. The ``organization``
argument exists for platform administrators looking at a customer; from
anyone else, naming any organization other than their own is refused — with
the same answer whether or not that organization exists, so the endpoint
cannot be used to find out which organizations there are. A record of
another organization and a record that does not exist are likewise the same
"not found".

**Period.** ``start`` and ``end`` are instants, ``(start, end]``: a bare date
(``2026-10-01``, midnight UTC) or an ISO-8601 date-time with ``Z`` or an
offset. A date-time without an offset is refused rather than guessed. A
calendar month in UTC is ``start=2026-10-01&end=2026-11-01``.

What the answer contains, and what it keeps apart, is described in
``webodm_core.metering.report``.
"""

import frappe

from webodm_core import tenancy
from webodm_core.metering import ledger, report

_NOT_PERMITTED = "You can only view your own organization's usage"
_NOT_FOUND = "Usage record not found"


def _organization(requested: str | None = None) -> str:
    """The organization the caller may see usage for (see the module docstring)."""
    requested = (requested or "").strip() or None
    if tenancy.is_platform_admin():
        if requested:
            if not frappe.db.exists("WebODM Organization", requested):
                frappe.throw(f"Organization {requested} does not exist", frappe.DoesNotExistError)
            return requested
        own = tenancy.get_current_org()
        if not own:
            frappe.throw("organization is required")
        return own
    own = tenancy.require_org()
    if requested and requested != own:
        frappe.throw(_NOT_PERMITTED, frappe.PermissionError)
    return own


@frappe.whitelist(allow_guest=False)
def summary(start=None, end=None, organization=None, as_of=None):
    """Totals per meter for the period, with what is still in progress kept separate."""
    return report.summary(_organization(organization), start, end, as_of=as_of)


@frappe.whitelist(allow_guest=False)
def records(start=None, end=None, meter=None, organization=None, as_of=None, limit=100, offset=0,
            with_evidence=0):
    """The usage records behind :func:`summary`, oldest first, in pages."""
    return report.records(
        _organization(organization), start, end, meter=meter or None, as_of=as_of,
        limit=frappe.utils.cint(limit) or 100, offset=frappe.utils.cint(offset),
        with_evidence=bool(frappe.utils.cint(with_evidence)),
    )


@frappe.whitelist(allow_guest=False)
def record(name=None):
    """One usage record with its evidence."""
    # Who is asking is settled before anything is looked up, so the answer
    # cannot depend on whether the record exists.
    is_admin = tenancy.is_platform_admin()
    own = None if is_admin else tenancy.require_org()
    row = ledger.get(name) if name else None
    if not row or (not is_admin and row["organization"] != own):
        # A record of another organization and a name that does not exist
        # get the same answer.
        frappe.throw(_NOT_FOUND, frappe.DoesNotExistError)
    return row
