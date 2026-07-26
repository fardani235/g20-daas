import frappe
from frappe.tests.utils import FrappeTestCase


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email,
                        "first_name": email.split("@")[0], "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email); u.roles = []
    u.append("roles", {"role": "WebODM User"}); u.save(ignore_permissions=True)
    return email


def _org(name):
    return frappe.get_doc({"doctype": "WebODM Organization", "organization_name": name}).insert(ignore_permissions=True)


def _join(user, org, role="Member"):
    frappe.get_doc({"doctype": "WebODM Org Membership", "user": user,
                    "organization": org, "role": role}).insert(ignore_permissions=True)


class TestIsolation(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org_a = _org("Iso Org A").name
        cls.org_b = _org("Iso Org B").name
        cls.member_a = _user("iso_member_a@example.com"); _join(cls.member_a, cls.org_a, "Owner")
        # A SECOND org-A member who does NOT own proj_a. This is what proves
        # org-scoping (not owner-scoping): owner-scoping would hide proj_a from
        # this user; org-scoping must show it because they share org A.
        cls.member_a2 = _user("iso_member_a2@example.com"); _join(cls.member_a2, cls.org_a, "Member")
        cls.owner_b = _user("iso_owner_b@example.com"); _join(cls.owner_b, cls.org_b, "Owner")

        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.member_a)
        cls.proj_a = frappe.get_doc({"doctype": "WebODM Project", "title": "A Project"}).insert().name
        frappe.set_user(cls.owner_b)
        frappe.local.webodm_org_cache = {}
        cls.proj_b = frappe.get_doc({"doctype": "WebODM Project", "title": "B Project"}).insert().name
        frappe.set_user("Administrator")

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_member_a_sees_only_org_a_projects(self):
        frappe.set_user(self.member_a)
        names = {p.name for p in frappe.get_list("WebODM Project", limit_page_length=0)}
        self.assertIn(self.proj_a, names)
        self.assertNotIn(self.proj_b, names)

    def test_org_scoping_not_owner_scoping(self):
        # member_a2 shares org A but owns nothing. Under org-scoping they see
        # proj_a (same org) but never proj_b (org B).
        frappe.set_user(self.member_a2)
        names = {p.name for p in frappe.get_list("WebODM Project", limit_page_length=0)}
        self.assertIn(self.proj_a, names)
        self.assertNotIn(self.proj_b, names)

    def test_platform_admin_sees_all(self):
        frappe.set_user("Administrator")
        names = {p.name for p in frappe.get_list("WebODM Project", limit_page_length=0)}
        self.assertIn(self.proj_a, names)
        self.assertIn(self.proj_b, names)
