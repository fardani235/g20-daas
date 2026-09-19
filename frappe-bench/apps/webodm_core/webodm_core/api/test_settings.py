import frappe
from frappe.tests.utils import FrappeTestCase
from webodm_core.api import settings as settings_api


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email,
                        "first_name": email.split("@")[0], "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email); u.roles = []
    u.append("roles", {"role": "WebODM User"}); u.save(ignore_permissions=True)
    return email


class TestPerOrgSettings(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org_a = frappe.get_doc({"doctype": "WebODM Organization", "organization_name": "Set Org A"}).insert(ignore_permissions=True).name
        cls.org_b = frappe.get_doc({"doctype": "WebODM Organization", "organization_name": "Set Org B"}).insert(ignore_permissions=True).name
        cls.admin_a = _user("set_admin_a@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.admin_a, "organization": cls.org_a, "role": "Owner"}).insert(ignore_permissions=True)
        cls.member_b = _user("set_member_b@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.member_b, "organization": cls.org_b, "role": "Member"}).insert(ignore_permissions=True)

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_get_creates_and_returns_org_row(self):
        frappe.set_user(self.admin_a)
        data = settings_api.get()
        self.assertIn("auto_start_processing", data)

    def test_save_isolated_per_org(self):
        frappe.set_user(self.admin_a)
        settings_api.save(max_file_size_mb=123)
        self.assertEqual(settings_api.get()["max_file_size_mb"], 123)
        frappe.set_user(self.member_b)
        frappe.local.webodm_org_cache = {}
        self.assertNotEqual(settings_api.get().get("max_file_size_mb"), 123)

    def test_member_cannot_save(self):
        frappe.set_user(self.member_b)
        with self.assertRaises(frappe.PermissionError):
            settings_api.save(max_file_size_mb=999)
