import frappe
from frappe.tests.utils import FrappeTestCase


class TestMembershipModel(FrappeTestCase):
    def setUp(self):
        self.org = frappe.get_doc({"doctype": "WebODM Organization",
                                   "organization_name": "Membership Org"}).insert(ignore_permissions=True)
        if not frappe.db.exists("User", "m1@example.com"):
            frappe.get_doc({"doctype": "User", "email": "m1@example.com",
                            "first_name": "M1", "send_welcome_email": 0}).insert(ignore_permissions=True)

    def test_user_cannot_have_two_memberships(self):
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": "m1@example.com",
                        "organization": self.org.name, "role": "Owner"}).insert(ignore_permissions=True)
        org2 = frappe.get_doc({"doctype": "WebODM Organization",
                               "organization_name": "Second Org"}).insert(ignore_permissions=True)
        with self.assertRaises(frappe.exceptions.ValidationError):
            frappe.get_doc({"doctype": "WebODM Org Membership", "user": "m1@example.com",
                            "organization": org2.name, "role": "Member"}).insert(ignore_permissions=True)
