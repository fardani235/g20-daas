# webodm_core/api/test_tenancy.py
import frappe
from frappe.tests.utils import FrappeTestCase
from webodm_core import tenancy


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email,
                        "first_name": email.split("@")[0], "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email)
    u.roles = []
    u.append("roles", {"role": "WebODM User"})
    u.save(ignore_permissions=True)
    return email


class TestTenancy(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "Tenancy Org"}).insert(ignore_permissions=True)
        cls.member = _user("tmember@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.member,
                        "organization": cls.org.name, "role": "Owner"}).insert(ignore_permissions=True)
        cls.no_org = _user("tnoorg@example.com")

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_get_current_org_returns_membership_org(self):
        self.assertEqual(tenancy.get_current_org(self.member), self.org.name)

    def test_get_current_org_none_when_no_membership(self):
        self.assertIsNone(tenancy.get_current_org(self.no_org))

    def test_require_org_raises_for_no_org(self):
        with self.assertRaises(tenancy.OrgContextError):
            tenancy.require_org(self.no_org)

    def test_suspended_org_denies(self):
        self.org.status = "Suspended"
        self.org.save(ignore_permissions=True)
        frappe.local.webodm_org_cache = {}
        self.assertIsNone(tenancy.get_current_org(self.member))
        self.org.status = "Active"
        self.org.save(ignore_permissions=True)

    def test_is_org_admin(self):
        self.assertTrue(tenancy.is_org_admin(self.member))
        self.assertFalse(tenancy.is_org_admin(self.no_org))

    def test_platform_admin_detected(self):
        self.assertTrue(tenancy.is_platform_admin("Administrator"))
        self.assertFalse(tenancy.is_platform_admin(self.member))
