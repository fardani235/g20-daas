"""Isolation for metering work that runs on someone else's code path.

Recording must never damage the thing it describes. When metering code runs
inside another operation — the delete of a plugin run, say — two things can
go wrong: it can raise, and it can leave the database transaction broken
(on PostgreSQL any failed statement aborts the whole transaction, so the
caller's *next* statement fails even though the exception was caught).

``isolated`` deals with both: the work runs inside a savepoint; whatever
goes wrong is rolled back to it, logged and swallowed, and the caller's
transaction is exactly where it was.
"""

from __future__ import annotations

import random
import string
from contextlib import contextmanager

import frappe

LOG_TITLE = "WebODM Metering"
LOG_ONCE_SECONDS = 3600


def savepoint_name(prefix: str = "meter") -> str:
    return f"{prefix}_{''.join(random.choices(string.ascii_lowercase, k=10))}"


def log(message: str, once_key: str | None = None):
    """Write to the error log; never raises (the log is a database write too).

    With ``once_key`` the same problem is logged at most once an hour: a
    source that cannot be recorded is retried by every sweep, and five-minute
    sweeps would otherwise bury the log under one line.
    """
    if once_key:
        try:
            cache_key = f"webodm:metering:logged:{once_key}"
            if frappe.cache.get_value(cache_key):
                return
            frappe.cache.set_value(cache_key, 1, expires_in_sec=LOG_ONCE_SECONDS)
        except Exception:
            pass  # no cache: better a repeated line than a missing one
    try:
        frappe.log_error(message, LOG_TITLE)
    except Exception:
        try:
            frappe.logger("webodm").error(f"{LOG_TITLE}: {message}")
        except Exception:
            pass


@contextmanager
def isolated(what: str, once_key: str | None = None):
    """Run the block; on any error roll back to a savepoint, log and carry on.

    ``what`` names the work for the error log (``once_key``: see
    :func:`log`). Yields ``True`` when the savepoint is open and ``False``
    when it could not be opened — in which case the caller must not start
    work it could not undo::

        with guard.isolated("recording run X") as ok:
            if ok:
                ...

    Only ``Exception`` is swallowed — a worker being shut down still stops.
    """
    name = savepoint_name()
    opened = True
    try:
        frappe.db.savepoint(name)
    except Exception as e:
        opened = False
        log(f"{what}: skipped, could not open a savepoint: {e}")
    try:
        yield opened
    except Exception as e:
        if opened:
            try:
                frappe.db.rollback(save_point=name)
            except Exception:
                pass
        log(f"{what}: {e.__class__.__name__}: {e}", once_key)
    else:
        if opened:
            try:
                frappe.db.release_savepoint(name)
            except Exception:
                pass
