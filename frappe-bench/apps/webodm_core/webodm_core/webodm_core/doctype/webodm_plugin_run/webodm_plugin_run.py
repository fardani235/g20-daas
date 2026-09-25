import frappe
from frappe.model.document import Document


class WebODMPluginRun(Document):
    """A single execution of an analysis plugin against a task.

    Organization is stamped from the acting user (never the payload). The
    lifecycle is Queued -> Running -> Completed | Failed | Cancelled, driven by
    the ``plugins.run`` job.
    """

    def on_trash(self):
        # Best-effort removal of the run's canonical output in object storage.
        from webodm_core.storage import assets

        assets.delete_run_objects(self)
