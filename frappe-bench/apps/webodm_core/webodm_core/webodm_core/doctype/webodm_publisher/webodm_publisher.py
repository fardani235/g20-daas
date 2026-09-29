import frappe
from frappe.model.document import Document

from webodm_core.plugins import package as package_mod


class WebODMPublisher(Document):
    """A marketplace publisher: the platform itself (``First-party``) or an
    invited ``Partner``. Rows are created by platform admins in Desk — there
    is no sign-up or self-serve onboarding in this version. Suspending a
    publisher hides its products from the catalog and blocks installs while
    leaving existing installs untouched."""

    def validate(self):
        if not package_mod.is_valid_id(self.publisher_id):
            frappe.throw("Publisher ID must be 2-64 lowercase letters, digits or hyphens")
