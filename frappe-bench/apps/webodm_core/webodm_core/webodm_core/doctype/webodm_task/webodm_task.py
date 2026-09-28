import frappe
from frappe.model.document import Document


class WebODMTask(Document):
    def on_trash(self):
        # Plugin runs link to their task and Frappe refuses to delete a linked
        # document, so the runs go first (each one removes its own stored
        # outputs in its on_trash; Frappe drops its attachments). This lives on
        # the backend so every client — the map page, the Projects page cascade,
        # the REST resource API — gets the same behaviour.
        self.delete_plugin_runs()

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

    def delete_plugin_runs(self) -> list[str]:
        """Delete every plugin run of this task; returns the deleted run names."""
        names = frappe.get_all("WebODM Plugin Run", filters={"task": self.name}, pluck="name")
        for name in names:
            # Permission was checked on the task; the runs belong to it.
            frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
        return names
