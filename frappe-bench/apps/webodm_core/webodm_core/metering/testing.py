"""Test helpers for the metering package. Not imported by production code.

Usage records cannot be deleted through the document API (that is the
point), so tests clean up with :func:`purge`, which removes rows with a
direct database delete. Fixtures are get-or-create because several tests
commit (the concurrency test must: a second connection has to see them).
"""

from __future__ import annotations

import datetime as dt
import io
from contextlib import contextmanager
from unittest.mock import patch

import frappe

from webodm_core.metering import ledger, timeutil
from webodm_core.plugins.files import save_private_file_from_stream

INSTANCE = "WebODM Compute Instance"
RUN = "WebODM Plugin Run"


def make_org(name: str) -> str:
    existing = frappe.db.get_value("WebODM Organization", {"organization_name": name}, "name")
    if existing:
        return existing
    return frappe.get_doc({"doctype": "WebODM Organization", "organization_name": name}
                          ).insert(ignore_permissions=True).name


def make_user(email: str, org: str | None = None, role: str = "Owner") -> str:
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                        "send_welcome_email": 0}).insert(ignore_permissions=True)
    user = frappe.get_doc("User", email)
    user.roles = []
    user.append("roles", {"role": "WebODM User"})
    user.save(ignore_permissions=True)
    if org and not frappe.db.exists("WebODM Org Membership", {"user": email}):
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": email, "organization": org,
                        "role": role}).insert(ignore_permissions=True)
    return email


def as_user(user: str):
    frappe.set_user(user)
    frappe.local.webodm_org_cache = {}


def purge(*organizations: str):
    """Remove every usage record of the given organizations (tests only)."""
    if organizations:
        frappe.db.delete(ledger.DOCTYPE, {"organization": ["in", list(organizations)]})


def wipe(*organizations: str):
    """Delete everything the given organizations own, then their usage records.

    Tests commit, so a failed run can leave rows behind; calling this from
    ``setUp`` makes every test start from an organization that stores and
    has consumed nothing.
    """
    previous = frappe.session.user
    frappe.set_user("Administrator")
    try:
        for org in organizations:
            for doctype in (RUN, "WebODM Task", "WebODM Dataset", "WebODM Project", INSTANCE):
                for name in frappe.get_all(doctype, filters={"organization": org}, pluck="name"):
                    if frappe.db.exists(doctype, name):
                        frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
    finally:
        frappe.set_user(previous)
    purge(*organizations)
    # Metering jobs commit, and FrappeTestCase rolls back whatever is still
    # open when a class ends -- an uncommitted clean-up would be undone and
    # the committed rows would stay.
    frappe.db.commit()


def usage_rows(organization: str, **filters) -> list[dict]:
    return frappe.get_all(ledger.DOCTYPE, filters={"organization": organization, **filters},
                          fields=list(ledger.ALL_FIELDS), order_by="period_end_utc asc, name asc")


@contextmanager
def at(moment: str | dt.datetime):
    """Freeze metering's clock (``timeutil.utc_now``) at a UTC instant."""
    if isinstance(moment, str):
        moment = timeutil.parse_instant(moment)
    with patch.object(timeutil, "utc_now", return_value=moment):
        yield moment


def local(text: str) -> dt.datetime:
    """A source timestamp as the pipeline would write it (naive, site time zone)."""
    return dt.datetime.fromisoformat(text)


def make_instance(organization: str, *, task: str = "no-such-task", **fields) -> str:
    """A ``WebODM Compute Instance`` row, as the pipeline leaves it.

    Defaults describe a machine that became Ready at 10:00, was given a job,
    and was released an hour later. Times are site-local, like the real ones.
    """
    values = {
        "status": "Terminated",
        "provider": "aws",
        "instance_class": "cpu",
        "handle": f"aws:i-{frappe.generate_hash(length=8)}",
        "requested_at": local("2026-10-19 09:55:00"),
        "ready_at": local("2026-10-19 10:00:00"),
        "dispatched_at": local("2026-10-19 10:00:40"),
        "released_at": local("2026-10-19 11:00:00"),
        "terminated_at": local("2026-10-19 11:00:05"),
        "release_reason": "task:Completed",
        "destroy_attempts": 0,
        "estimated_hourly_cost": 0.34,
        "estimated_cost": 0.3405,
    }
    values.update(fields)
    doc = frappe.get_doc({"doctype": INSTANCE, "task": task, "organization": organization, **values})
    doc.flags.ignore_links = True
    doc.insert(ignore_permissions=True)
    return doc.name


def make_run(user: str, *, plugin: str = "test.metering-plugin", task: str = "no-such-task", **fields):
    """A ``WebODM Plugin Run`` inserted as ``user`` (the organization is stamped from the session)."""
    values = {
        "status": "Completed",
        "started_at": local("2026-10-19 10:00:02"),
        "completed_at": local("2026-10-19 10:05:02"),
    }
    values.update(fields)
    previous = frappe.session.user
    as_user(user)
    try:
        doc = frappe.get_doc({"doctype": RUN, "plugin": plugin, "task": task, **values})
        doc.flags.ignore_links = True
        doc.insert(ignore_permissions=True)
    finally:
        as_user(previous)
    return doc


def attach_file(doctype: str, name: str, size: int, filename: str | None = None):
    """Register a private File of ``size`` bytes against a document."""
    filename = filename or f"meter_{frappe.generate_hash(length=8)}.bin"
    return save_private_file_from_stream(io.BytesIO(b"\0" * size), filename, attached_to_doctype=doctype,
                                         attached_to_name=name, ignore_permissions=True)


def site_time_zone() -> str:
    from frappe.utils import get_system_timezone

    return get_system_timezone()


def local_to_utc(text: str, edge: str = "start") -> dt.datetime:
    """What a site-local timestamp is in UTC on this test site."""
    return timeutil.local_to_utc(local(text), site_time_zone(), edge=edge)[0]


def site_local(utc: str) -> dt.datetime:
    """The site-local wall-clock time (as the pipeline would write it) of a UTC instant."""
    from zoneinfo import ZoneInfo

    instant = timeutil.parse_instant(utc).replace(tzinfo=dt.UTC)
    return instant.astimezone(ZoneInfo(site_time_zone())).replace(tzinfo=None)
