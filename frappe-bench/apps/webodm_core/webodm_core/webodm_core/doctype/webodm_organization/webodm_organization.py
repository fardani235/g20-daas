import frappe
from frappe.model.document import Document
from frappe.utils import cstr


class WebODMOrganization(Document):
    def before_insert(self):
        if not self.slug and self.organization_name:
            self.slug = frappe.scrub(cstr(self.organization_name)).replace("_", "-")
