import frappe
from frappe.model.document import Document


class WebODMTask(Document):
    def validate(self):
        self._check_dataset()

    def _check_dataset(self):
        """The dataset is the task's only input and must belong to the same organization.

        ``dataset`` is a user-writable Link: without this check a member could
        point a task at another organization's dataset by name and have the
        pipeline stream those images to a node (``input_sources`` resolves the
        rows with permissions bypassed). ``organization`` is stamped from the
        session before validate runs, so the comparison is against the actor's
        org, not the payload.
        """
        if not self.dataset:
            return  # `reqd` reports the missing value with Frappe's standard message
        org = frappe.db.get_value("WebODM Dataset", self.dataset, "organization")
        if org is None:
            frappe.throw(f"Dataset {self.dataset} does not exist", frappe.DoesNotExistError)
        if self.organization and org != self.organization:
            frappe.throw("The dataset must belong to the task's organization", frappe.PermissionError)

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
        #
        # Only the task's *outputs* (raw/ + assets/) are removed. Its inputs
        # belong to the dataset, which is shared and outlives the task: the
        # dataset stays in the library until the user deletes it themselves.
        from webodm_core.storage import assets
        from webodm_core.webodm_core.processing import compute

        try:
            compute.release_for_task(self)
        except Exception as e:
            frappe.log_error(f"{self.name}: release on delete failed: {e}", "WebODM Compute")
        # The viewer octree files are attachments (Frappe drops them below) but
        # their blobs may survive Frappe's shared-content check; remove them now.
        from webodm_core.webodm_core.processing import potree
        try:
            potree.clear_files(self)
        except Exception as e:
            frappe.log_error(f"{self.name}: octree cleanup on delete failed: {e}", "WebODM Potree")
        assets.delete_task_objects(self)

    def delete_plugin_runs(self) -> list[str]:
        """Delete every plugin run of this task; returns the deleted run names."""
        names = frappe.get_all("WebODM Plugin Run", filters={"task": self.name}, pluck="name")
        for name in names:
            # Permission was checked on the task; the runs belong to it.
            frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
        return names
