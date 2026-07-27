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
                      "orgapi_member@example.com", "orgapi_inviter@example.com",
                      "orgapi_invitee@example.com", "orgapi_dupe_a@example.com",
                      "orgapi_dupe_b@example.com", "orgapi_exp_owner@example.com",
                      "orgapi_exp_invitee@example.com", "orgapi_reuse_owner@example.com",
                      "orgapi_reuse_a@example.com", "orgapi_reuse_b@example.com",
                      "orgapi_hp_owner@example.com", "orgapi_hp_invitee@example.com",
                      "orgapi_mm_owner@example.com", "orgapi_mm_invited@example.com",
                      "orgapi_mm_wrong@example.com"):
            for m in frappe.get_all("WebODM Org Membership", filters={"user": email}, pluck="name"):
                frappe.delete_doc("WebODM Org Membership", m, ignore_permissions=True, force=True)
            for i in frappe.get_all("WebODM Org Invitation", filters={"email": email}, pluck="name"):
                frappe.delete_doc("WebODM Org Invitation", i, ignore_permissions=True, force=True)
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

    def test_invite_and_accept_flow(self):
        from webodm_core.api import organization as org_api
        inviter = _user("orgapi_inviter@example.com")
        frappe.set_user(inviter)
        frappe.local.webodm_org_cache = {}
        org_api.create_organization("Invite Flow Org")
        frappe.local.webodm_org_cache = {}
        invitee = _user("orgapi_invitee@example.com")
        res = org_api.invite_member("orgapi_invitee@example.com")
        token = res["token"]
        frappe.set_user(invitee)
        frappe.local.webodm_org_cache = {}
        accepted = org_api.accept_invitation(token)
        self.assertEqual(accepted["role"], "Member")

    def test_accept_when_already_in_org_fails(self):
        from webodm_core.api import organization as org_api
        a = _user("orgapi_dupe_a@example.com"); b = _user("orgapi_dupe_b@example.com")
        frappe.set_user(a); frappe.local.webodm_org_cache = {}
        org_api.create_organization("Dupe Accept Org A")
        token = org_api.invite_member("orgapi_dupe_b@example.com")["token"]
        frappe.set_user(b); frappe.local.webodm_org_cache = {}
        org_api.create_organization("Dupe Accept Org B")
        frappe.local.webodm_org_cache = {}
        with self.assertRaises(frappe.ValidationError):
            org_api.accept_invitation(token)

    def test_accept_expired_invitation_fails(self):
        from webodm_core.api import organization as org_api
        owner = _user("orgapi_exp_owner@example.com")
        frappe.set_user(owner); frappe.local.webodm_org_cache = {}
        org_api.create_organization("Expired Invite Org")
        invitee = _user("orgapi_exp_invitee@example.com")
        token = org_api.invite_member("orgapi_exp_invitee@example.com")["token"]
        # Force the invitation into the past so the expiry guard trips.
        inv_name = frappe.db.get_value("WebODM Org Invitation", {"token": token})
        frappe.db.set_value("WebODM Org Invitation", inv_name, "expires_on", "2020-01-01 00:00:00")
        frappe.set_user(invitee); frappe.local.webodm_org_cache = {}
        with self.assertRaises(frappe.exceptions.ValidationError):
            org_api.accept_invitation(token)

    def test_token_cannot_be_reused_after_accept(self):
        from webodm_core.api import organization as org_api
        owner = _user("orgapi_reuse_owner@example.com")
        frappe.set_user(owner); frappe.local.webodm_org_cache = {}
        org_api.create_organization("Reuse Token Org")
        a = _user("orgapi_reuse_a@example.com")
        token = org_api.invite_member("orgapi_reuse_a@example.com")["token"]
        # First orgless invitee accepts successfully.
        frappe.set_user(a); frappe.local.webodm_org_cache = {}
        accepted = org_api.accept_invitation(token)
        self.assertEqual(accepted["role"], "Member")
        # A second orgless user replaying the same token must be rejected
        # because the invitation status is now Accepted, not Pending.
        b = _user("orgapi_reuse_b@example.com")
        frappe.set_user(b); frappe.local.webodm_org_cache = {}
        with self.assertRaises(frappe.exceptions.ValidationError):
            org_api.accept_invitation(token)

    def test_accept_creates_membership_in_inviter_org(self):
        from webodm_core.api import organization as org_api
        owner = _user("orgapi_hp_owner@example.com")
        frappe.set_user(owner); frappe.local.webodm_org_cache = {}
        org = org_api.create_organization("Happy Path Org")["organization"]
        invitee = _user("orgapi_hp_invitee@example.com")
        token = org_api.invite_member("orgapi_hp_invitee@example.com")["token"]
        frappe.set_user(invitee); frappe.local.webodm_org_cache = {}
        accepted = org_api.accept_invitation(token)
        # Invitee joins the inviter's org, not some other org.
        self.assertEqual(accepted["organization"], org)
        row = frappe.db.get_value("WebODM Org Membership",
                                  {"user": "orgapi_hp_invitee@example.com", "organization": org},
                                  ["role"], as_dict=True)
        self.assertIsNotNone(row)
        self.assertEqual(row.role, "Member")

    def test_accept_rejected_for_wrong_email(self):
        # An invitation is bound to a specific email; a different (orgless) user
        # replaying the token must be rejected, with no side effects.
        from webodm_core.api import organization as org_api
        owner = _user("orgapi_mm_owner@example.com")
        frappe.set_user(owner); frappe.local.webodm_org_cache = {}
        org_api.create_organization("Mismatch Guard Org")
        # Invite X, but a different orgless user Y attempts to accept.
        _user("orgapi_mm_invited@example.com")
        token = org_api.invite_member("orgapi_mm_invited@example.com")["token"]
        inv_name = frappe.db.get_value("WebODM Org Invitation", {"token": token})
        wrong = _user("orgapi_mm_wrong@example.com")
        frappe.set_user(wrong); frappe.local.webodm_org_cache = {}
        with self.assertRaises(frappe.exceptions.ValidationError):
            org_api.accept_invitation(token)
        # (a) No membership created for the mismatched user.
        self.assertFalse(frappe.db.exists("WebODM Org Membership", {"user": wrong}))
        # (b) Invitation must still be Pending (not flipped to Accepted).
        self.assertEqual(frappe.db.get_value("WebODM Org Invitation", inv_name, "status"), "Pending")
