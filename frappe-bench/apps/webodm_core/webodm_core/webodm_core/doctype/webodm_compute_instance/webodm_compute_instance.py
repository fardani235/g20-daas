from frappe.model.document import Document


class WebODMComputeInstance(Document):
    """One ephemeral NodeODM instance provisioned for one task.

    Deliberately separate from ``WebODM Processing Node`` (the static registry
    of known endpoints): this is a lifecycle record — requested, provisioning,
    ready, terminating, terminated/failed — owned by the app, while the
    provisioner service itself keeps no state. Created and driven by
    ``webodm_core.webodm_core.processing.compute``.
    """

    TERMINAL_STATUSES = ("Terminated", "Failed")
    LIVE_STATUSES = ("Requested", "Provisioning", "Ready", "Terminating")
