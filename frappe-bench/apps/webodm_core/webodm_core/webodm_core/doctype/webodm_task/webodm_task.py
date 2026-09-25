import frappe
from frappe.model.document import Document


class WebODMTask(Document):
    def on_trash(self):
        # Deleting a task must not leave a billed instance behind or orphan its
        # objects. Both are best-effort: a failed destroy is retried by the
        # compute sweep, a failed object delete is logged.
        from webodm_core.storage import assets
        from webodm_core.webodm_core.processing import compute

        try:
            compute.release_for_task(self)
        except Exception as e:
            frappe.log_error(f"{self.name}: release on delete failed: {e}", "WebODM Compute")
        assets.delete_task_objects(self)
