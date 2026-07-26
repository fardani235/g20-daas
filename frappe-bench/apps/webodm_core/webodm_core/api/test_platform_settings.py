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


class TestPlatformCaps(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = frappe.get_doc({"doctype": "WebODM Organization", "organization_name": "Cap Org"}).insert(ignore_permissions=True).name
        cls.admin = _user("cap_admin@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.admin, "organization": cls.org, "role": "Owner"}).insert(ignore_permissions=True)
        ps = frappe.get_single("WebODM Platform Settings")
        ps.max_file_size_mb = 200
        ps.save(ignore_permissions=True)

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_org_value_clamped_to_platform_cap(self):
        frappe.set_user(self.admin)
        settings_api.save(max_file_size_mb=99999)
        self.assertEqual(settings_api.get()["max_file_size_mb"], 200)
