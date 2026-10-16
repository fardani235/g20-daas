import frappe
from frappe.model.document import Document


class WebODMFlightPlan(Document):
    def before_insert(self):
        if not self.created_by:
            self.created_by = frappe.session.user

    def validate(self):
        self.title = (self.title or "").strip()
        if not self.title:
            frappe.throw("Flight plan title is required")
        self._check_project()
        # The stored mission is what exports read, so it is never taken from
        # the request: whoever saves (the API, Desk, the REST resource), it is
        # recomputed here from the definition unless the API has just done so.
        if not self.flags.mission_computed:
            from webodm_core.api import flightplan

            flightplan.compute_into(self)

    def _check_project(self):
        """The project must exist and belong to the plan's organization.

        ``project`` is a user-writable Link, and ``organization`` is stamped
        from the session before validate runs, so this compares against the
        actor's organization rather than anything in the payload.
        """
        if not self.project:
            return  # `reqd` reports the missing value with Frappe's standard message
        org = frappe.db.get_value("WebODM Project", self.project, "organization")
        if org is None:
            frappe.throw(f"Project {self.project} does not exist", frappe.DoesNotExistError)
        if self.organization and org != self.organization:
            frappe.throw("The project must belong to the flight plan's organization", frappe.PermissionError)
