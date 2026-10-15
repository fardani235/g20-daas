import frappe
from frappe.model.document import Document


class WebODMCameraProfile(Document):
    def validate(self):
        # One validator for every writer (the API, Desk, the REST resource):
        # the same function the planner reads profiles through.
        from webodm_core.api import flightplan

        flightplan.camera_from_doc(self)
