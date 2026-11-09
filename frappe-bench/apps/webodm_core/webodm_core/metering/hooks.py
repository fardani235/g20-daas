"""Last-chance capture: record a source that is about to be deleted.

Metering records by catching up — a sweep looks for finished sources without
a usage record. That only works while the source exists, and one kind of
source routinely does not: starting a plugin on a task deletes that plugin's
previous run on it, deleting a task deletes its runs, and members may delete
a run themselves. So these two ``on_trash`` doc events record the row as it
goes. They use the same idempotency key as the sweep: whichever gets there
first writes the one record.

These functions are called by Frappe in the middle of *someone else's*
delete. Nothing they do may stop it, slow it noticeably or leave its
transaction unusable — which is why this module imports only ``frappe`` at
the top (the rest is imported inside the ``try``, so even a broken metering
package cannot turn into a failed delete) and why the work runs inside
``guard.isolated``.
"""

import frappe


def before_run_delete(doc, method=None):
    """``WebODM Plugin Run`` ``on_trash``."""
    _capture("record_run", doc, "plugin run")


def before_instance_delete(doc, method=None):
    """``WebODM Compute Instance`` ``on_trash``."""
    _capture("record_instance", doc, "compute instance")


def _capture(function: str, doc, what: str):
    try:
        from webodm_core.metering import guard, sweep

        with guard.isolated(f"recording {what} {doc.name} before it is deleted") as ok:
            if ok:
                getattr(sweep, function)(doc, deleting=True)
    except Exception as e:
        try:
            frappe.log_error(f"{what} {getattr(doc, 'name', '?')}: capture before delete failed: {e}",
                             "WebODM Metering")
        except Exception:
            pass
