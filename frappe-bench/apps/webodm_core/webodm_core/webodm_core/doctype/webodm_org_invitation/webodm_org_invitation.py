import frappe
from frappe.model.document import Document
from frappe.utils import add_days, now_datetime


class WebODMOrgInvitation(Document):
    def before_insert(self):
        if not self.token:
            self.token = frappe.generate_hash(length=32)
        if not self.expires_on:
            self.expires_on = add_days(now_datetime(), 7)
