import frappe
from frappe.tests.utils import FrappeTestCase


class TestInvitationModel(FrappeTestCase):
    def test_token_and_expiry_autofilled(self):
        org = frappe.get_doc({"doctype": "WebODM Organization", "organization_name": "Inv Org"}).insert(ignore_permissions=True)
        inv = frappe.get_doc({"doctype": "WebODM Org Invitation",
                              "email": "invitee@example.com", "organization": org.name}).insert(ignore_permissions=True)
        self.assertTrue(inv.token)
        self.assertTrue(inv.expires_on)
        self.assertEqual(inv.status, "Pending")
