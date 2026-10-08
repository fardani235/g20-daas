import frappe
from frappe.model.document import Document


class WebODMPluginRun(Document):
    """A single execution of an analysis plugin against a task.

    Organization is stamped from the acting user (never the payload). The
    lifecycle is Queued -> Running -> Completed | Failed | Cancelled, driven by
    the ``plugins.run`` job.

    A comparison run also names a ``reference_task``: a second survey it
    reads inputs from. The rule that it is in the same organization and
    project as ``task`` lives here, on the record, so no client — the run
    API, Desk, ``/api/resource`` — can create a run that pulls another
    tenant's survey into a comparison.
    """

    def validate(self):
        self._check_reference_task()

    def _check_reference_task(self):
        if not self.reference_task:
            return
        if self.reference_task == self.task:
            frappe.throw("A survey cannot be compared with itself")
        task = frappe.db.get_value("WebODM Task", self.task, ["organization", "project"], as_dict=True)
        reference = frappe.db.get_value(
            "WebODM Task", self.reference_task, ["organization", "project"], as_dict=True
        )
        if not task or not reference:
            frappe.throw("Comparison survey not found", frappe.PermissionError)
        # ``organization`` is stamped from the session before validate runs,
        # so this compares against the actor's organization, not the payload.
        if reference.organization != task.organization or (
            self.organization and reference.organization != self.organization
        ):
            frappe.throw("Comparison survey not found", frappe.PermissionError)
        if reference.project != task.project:
            frappe.throw("The comparison survey must belong to the same project")

    def on_trash(self):
        # Best-effort removal of the run's canonical output in object storage.
        from webodm_core.storage import assets

        assets.delete_run_objects(self)
