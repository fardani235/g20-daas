# webodm_core/api/test_organization_api.py
import frappe
from frappe.tests.utils import FrappeTestCase
from webodm_core.api import organization as org_api


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email,
                        "first_name": email.split("@")[0], "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email); u.roles = []
    u.append("roles", {"role": "WebODM User"}); u.save(ignore_permissions=True)
    return email


class TestOrganizationAPI(FrappeTestCase):
    def setUp(self):
        frappe.local.webodm_org_cache = {}
        # This Frappe version rolls back only at class teardown, not per-test,
        # so clear any memberships leaked from a prior test in this class.
        for email in ("orgapi_a@example.com", "orgapi_none@example.com",
                      "orgapi_member@example.com"):
            for m in frappe.get_all("WebODM Org Membership", filters={"user": email}, pluck="name"):
                frappe.delete_doc("WebODM Org Membership", m, ignore_permissions=True, force=True)
        self.u = _user("orgapi_a@example.com")

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_create_organization_makes_owner_membership(self):
        frappe.set_user(self.u)
        res = org_api.create_organization("My Org API")
        self.assertEqual(res["role"], "Owner")
        self.assertEqual(org_api.get_my_organization()["organization"], res["organization"])

    def test_second_create_fails(self):
        frappe.set_user(self.u)
        org_api.create_organization("First Org API")
        frappe.local.webodm_org_cache = {}
        with self.assertRaises(frappe.ValidationError):
            org_api.create_organization("Second Org API")

    def test_get_my_organization_none_when_no_org(self):
        other = _user("orgapi_none@example.com")
        frappe.set_user(other)
        self.assertIsNone(org_api.get_my_organization()["organization"])

    def test_non_admin_member_cannot_remove_member(self):
        # Owner creates the org.
        frappe.set_user(self.u)
        org = org_api.create_organization("Admin Guard Org")["organization"]
        # Add a second user directly as a plain Member of that same org.
        member = _user("orgapi_member@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": member,
                        "organization": org, "role": "Member"}).insert(ignore_permissions=True)
        # Acting as the non-admin Member, removing anyone must be denied.
        frappe.set_user(member)
        frappe.local.webodm_org_cache = {}
        with self.assertRaises(frappe.PermissionError):
            org_api.remove_member(self.u)

    def test_cannot_remove_last_owner(self):
        # Owner creates the org, so they are the sole Owner.
        frappe.set_user(self.u)
        org_api.create_organization("Last Owner Org")
        frappe.local.webodm_org_cache = {}
        # bare frappe.throw(msg) raises frappe.exceptions.ValidationError.
        with self.assertRaises(frappe.exceptions.ValidationError):
            org_api.remove_member(self.u)
