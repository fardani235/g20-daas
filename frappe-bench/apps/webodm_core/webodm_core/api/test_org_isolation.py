"""Org-model isolation: a WebODM User must not be able to see other orgs'
rows or forge their way into another org through /api/resource."""
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


def _org(name):
    return frappe.get_doc({"doctype": "WebODM Organization", "organization_name": name}).insert(ignore_permissions=True)


def _join(user, org, role="Member"):
    frappe.get_doc({"doctype": "WebODM Org Membership", "user": user,
                    "organization": org, "role": role}).insert(ignore_permissions=True)


class TestOrgIsolation(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org_a = _org("OrgIso A").name
        cls.org_b = _org("OrgIso B").name
        cls.owner_a = _user("orgiso_owner_a@example.com"); _join(cls.owner_a, cls.org_a, "Owner")
        cls.owner_b = _user("orgiso_owner_b@example.com"); _join(cls.owner_b, cls.org_b, "Owner")
        cls.outsider = _user("orgiso_outsider@example.com")  # no org
        cls.inv_b = frappe.get_doc({"doctype": "WebODM Org Invitation", "email": "someone_b@example.com",
                                    "organization": cls.org_b, "status": "Pending"}).insert(ignore_permissions=True)

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    # --- the escalation that used to work ---------------------------------

    def test_outsider_cannot_mint_invitation_into_another_org(self):
        # Previously: WebODM User had create on Invitation and no permission
        # hook, so this insert succeeded, before_insert minted a token, and
        # accept_invitation let the caller join org B.
        frappe.set_user(self.outsider)
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc({"doctype": "WebODM Org Invitation", "email": self.outsider,
                            "organization": self.org_b, "status": "Pending"}).insert()
        self.assertFalse(frappe.db.exists("WebODM Org Invitation",
                                          {"email": self.outsider, "organization": self.org_b}))

    def test_member_of_a_cannot_mint_invitation_into_org_b(self):
        frappe.set_user(self.owner_a)
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc({"doctype": "WebODM Org Invitation", "email": "x@example.com",
                            "organization": self.org_b, "status": "Pending"}).insert()

    def test_invite_member_api_still_works_for_org_owner(self):
        # Tightening role perms must not break the legitimate path.
        frappe.set_user(self.owner_a)
        out = org_api.invite_member("orgiso_new_a@example.com")
        self.assertTrue(out["token"])
        self.assertEqual(frappe.db.get_value("WebODM Org Invitation", {"token": out["token"]}, "organization"),
                         self.org_a)

    # --- read scoping -----------------------------------------------------

    def test_member_cannot_list_other_orgs_invitations_or_tokens(self):
        frappe.set_user(self.owner_a)
        rows = frappe.get_list("WebODM Org Invitation", fields=["name", "organization", "token"],
                               limit_page_length=0)
        self.assertNotIn(self.inv_b.name, {r.name for r in rows})
        self.assertTrue(all(r.organization == self.org_a for r in rows))

    def test_member_cannot_read_other_orgs_invitation_doc(self):
        frappe.set_user(self.owner_a)
        self.assertFalse(frappe.has_permission("WebODM Org Invitation", "read", self.inv_b))

    def test_member_sees_only_own_org_memberships(self):
        frappe.set_user(self.owner_a)
        orgs = {r.organization for r in frappe.get_list("WebODM Org Membership", fields=["organization"],
                                                        limit_page_length=0)}
        self.assertEqual(orgs, {self.org_a})

    def test_member_sees_only_own_organization(self):
        frappe.set_user(self.owner_a)
        names = {r.name for r in frappe.get_list("WebODM Organization", limit_page_length=0)}
        self.assertEqual(names, {self.org_a})
        org_b_doc = frappe.get_doc("WebODM Organization", self.org_b)
        self.assertFalse(frappe.has_permission("WebODM Organization", "read", org_b_doc))

    def test_orgless_user_sees_nothing(self):
        frappe.set_user(self.outsider)
        for dt in ("WebODM Organization", "WebODM Org Membership", "WebODM Org Invitation"):
            self.assertEqual(frappe.get_list(dt, limit_page_length=0), [], dt)

    def test_platform_admin_sees_all(self):
        frappe.set_user("Administrator")
        names = {r.name for r in frappe.get_list("WebODM Organization", limit_page_length=0)}
        self.assertTrue({self.org_a, self.org_b} <= names)
