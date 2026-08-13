import frappe
from frappe.model.document import Document


class WebODMPreset(Document):
    def validate(self):
        # System presets are platform-global. A stamped organization would let
        # that org's members reach the preset through org-scoped permission
        # paths, so normalize here -- validate() runs for every writer (API,
        # Desk, REST, patches), unlike the before_insert stamping hook.
        if int(self.system or 0):
            self.organization = None
