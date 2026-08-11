import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import session as session_api


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email,
                        "first_name": email.split("@")[0], "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email); u.roles = []
    u.append("roles", {"role": "WebODM User"}); u.save(ignore_permissions=True)
    return email


class TestWhoami(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.local.webodm_org_cache = {}
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "Whoami Org"}).insert(ignore_permissions=True).name
        cls.member = _user("whoami_member@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.member,
                        "organization": cls.org, "role": "Member"}).insert(ignore_permissions=True)

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_member_gets_org_role_and_no_admin(self):
        frappe.set_user(self.member)
        out = session_api.whoami()
        self.assertEqual(out["user"], self.member)
        self.assertEqual(out["organization"], self.org)
        self.assertEqual(out["org_role"], "Member")
        self.assertFalse(out["is_platform_admin"])

    def test_administrator_is_platform_admin(self):
        frappe.set_user("Administrator")
        out = session_api.whoami()
        self.assertTrue(out["is_platform_admin"])

    def test_suspended_org_reports_no_organization(self):
        # whoami must agree with tenancy.get_current_org(): a suspended org is
        # no org at all, so reporting the membership row would contradict every
        # permission path (see api/test_tenancy.test_suspended_org_denies).
        org = frappe.get_doc("WebODM Organization", self.org)
        org.status = "Suspended"
        org.save(ignore_permissions=True)
        frappe.local.webodm_org_cache = {}
        try:
            frappe.set_user(self.member)
            out = session_api.whoami()
            self.assertIsNone(out["organization"])
            self.assertEqual(out["org_role"], "Member")
        finally:
            frappe.set_user("Administrator")
            org.reload()
            org.status = "Active"
            org.save(ignore_permissions=True)
            frappe.local.webodm_org_cache = {}

    def test_orgless_user_gets_nulls(self):
        orgless = _user("whoami_orgless@example.com")
        frappe.set_user(orgless)
        out = session_api.whoami()
        self.assertIsNone(out["organization"])
        self.assertIsNone(out["org_role"])
