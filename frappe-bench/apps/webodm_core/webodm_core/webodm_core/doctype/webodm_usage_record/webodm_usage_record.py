import frappe
from frappe.model.document import Document


class UsageRecordImmutable(frappe.ValidationError):
    """A usage record was about to be changed or deleted."""


IMMUTABLE_MESSAGE = (
    "Usage records cannot be changed or deleted. To correct one, record a reversal "
    "(webodm_core.metering.ledger.reverse)."
)


class WebODMUsageRecord(Document):
    """One fact about what one organization consumed. Written once, never changed.

    Rows are inserted only by ``webodm_core.metering.ledger.record`` (which
    sets ``flags.from_ledger``); nothing else — Desk, ``/api/resource``, a
    stray ``frappe.get_doc(...).insert()`` — can add one. Every way the
    document API can alter or remove a row is refused here: ``save``,
    ``db_set``, rename and delete. A mistake is corrected by adding a
    reversal row, so the history of what was once believed stays readable.

    What this cannot stop is a write that goes around the document API
    (``frappe.db.set_value``, raw SQL). That is why each row carries
    ``record_hash``: ``webodm_core.metering.verify`` recomputes it and
    reports any row that no longer says what it said when it was written.
    """

    def before_insert(self):
        if not self.flags.from_ledger:
            frappe.throw(
                "Usage records are written only by webodm_core.metering.ledger.record",
                frappe.PermissionError,
            )
        self._check_hash()

    def after_insert(self):
        # Again, on the document exactly as Frappe wrote it. Between
        # before_insert and the INSERT, Frappe validates and may rewrite
        # values (its HTML sanitizer strips anything that looks like markup
        # from text fields; every text field here opts out with
        # ignore_xss_filter, and this is what notices if one ever does not).
        # Raising here undoes the insert: a row whose content does not match
        # its hash is never left behind.
        self._check_hash()

    def _check_hash(self):
        from webodm_core.metering import rules

        if self.record_hash != rules.record_hash(self.as_dict()):
            frappe.throw("Usage record content does not match its hash", UsageRecordImmutable)

    def validate(self):
        if not self.is_new():
            frappe.throw(IMMUTABLE_MESSAGE, UsageRecordImmutable)

    def db_set(self, *args, **kwargs):
        frappe.throw(IMMUTABLE_MESSAGE, UsageRecordImmutable)

    def before_rename(self, *args, **kwargs):
        frappe.throw(IMMUTABLE_MESSAGE, UsageRecordImmutable)

    def on_trash(self):
        frappe.throw(IMMUTABLE_MESSAGE, UsageRecordImmutable)


def on_doctype_update():
    # "What did this organization consume in this period" is the query this
    # table exists for: organization + the instant a record's interval ended.
    frappe.db.add_index("WebODM Usage Record", ["organization", "period_end_utc"])
