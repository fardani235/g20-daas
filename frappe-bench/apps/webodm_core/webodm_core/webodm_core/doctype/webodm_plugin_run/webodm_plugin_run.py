import frappe
from frappe.model.document import Document


class WebODMPluginRun(Document):
    """A single execution of an analysis plugin against a task.

    Organization is stamped from the acting user (never the payload). The
    lifecycle is Queued -> Running -> Completed | Failed | Cancelled, driven by
    the ``plugins.run`` job.
    """
