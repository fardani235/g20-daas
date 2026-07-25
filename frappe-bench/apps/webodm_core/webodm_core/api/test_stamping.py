import frappe
from frappe.tests.utils import FrappeTestCase
from webodm_core import tenancy


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email,
                        "first_name": email.split("@")[0], "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email); u.roles = []
    u.append("roles", {"role": "WebODM User"}); u.save(ignore_permissions=True)
    return email


class TestStamping(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "Stamp Org"}).insert(ignore_permissions=True)
        cls.org2 = frappe.get_doc({"doctype": "WebODM Organization",
                                   "organization_name": "Stamp Org 2"}).insert(ignore_permissions=True)
        cls.member = _user("stampmember@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.member,
                        "organization": cls.org.name, "role": "Owner"}).insert(ignore_permissions=True)
        cls.no_org = _user("stampnoorg@example.com")

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_project_stamped_with_actor_org(self):
        frappe.set_user(self.member)
        p = frappe.get_doc({"doctype": "WebODM Project", "title": "Stamped P"}).insert()
        self.assertEqual(p.organization, self.org.name)

    def test_payload_org_is_overridden(self):
        frappe.set_user(self.member)
        p = frappe.get_doc({"doctype": "WebODM Project", "title": "Spoof P",
                            "organization": self.org2.name}).insert()
        self.assertEqual(p.organization, self.org.name)  # spoof ignored

    def test_no_org_user_denied_no_orphan(self):
        frappe.set_user(self.no_org)
        with self.assertRaises(tenancy.OrgContextError):
            frappe.get_doc({"doctype": "WebODM Project", "title": "Orphan P"}).insert()
        frappe.set_user("Administrator")
        self.assertFalse(frappe.db.exists("WebODM Project", {"title": "Orphan P"}))
