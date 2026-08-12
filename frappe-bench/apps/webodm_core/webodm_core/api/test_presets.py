import json
import unittest
from unittest.mock import patch, MagicMock

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import presets


def _cleanup(names):
    for n in names:
        if frappe.db.exists("WebODM Preset", n):
            frappe.delete_doc("WebODM Preset", n, force=True, ignore_permissions=True)
    frappe.db.commit()


def _puser(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email,
                        "first_name": email.split("@")[0], "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email); u.roles = []
    u.append("roles", {"role": "WebODM User"}); u.save(ignore_permissions=True)
    return email


class TestPresets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Org-stamping now runs on every WebODM Preset insert, so the acting user
        # must belong to an active org or require_org() raises OrgContextError.
        # Give this class a real org + member and act as that member so the
        # roundtrip/delete tests still prove "user owns their own preset".
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "Preset Test Org"}).insert(ignore_permissions=True).name
        cls.member = _puser("preset_member@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.member,
                        "organization": cls.org, "role": "Owner"}).insert(ignore_permissions=True)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        for m in frappe.get_all("WebODM Org Membership", filters={"organization": cls.org}, pluck="name"):
            frappe.delete_doc("WebODM Org Membership", m, force=True, ignore_permissions=True)
        if frappe.db.exists("WebODM Organization", cls.org):
            frappe.delete_doc("WebODM Organization", cls.org, force=True, ignore_permissions=True)
        frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.local.webodm_org_cache = {}
        frappe.set_user(self.member)
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        _cleanup(["Test User Preset", "Test System Preset"])

    def test_options_proxies_node_catalog(self):
        fake = [{"name": "dsm", "type": "bool", "domain": None}]
        client = MagicMock()
        client.get_options.return_value = fake
        with patch.object(presets, "_node_client", return_value=client):
            self.assertEqual(presets.options(), fake)

    def test_options_node_offline_throws(self):
        with patch.object(presets, "_node_client", return_value=None):
            with self.assertRaises(frappe.ValidationError):
                presets.options()

    def test_save_and_list_roundtrip_owns_options(self):
        opts = [{"name": "dsm", "value": True}]
        presets.save(preset_name="Test User Preset", options=json.dumps(opts))
        frappe.db.commit()
        listed = {p["preset_name"]: p for p in presets.list_presets()}
        self.assertIn("Test User Preset", listed)
        self.assertEqual(listed["Test User Preset"]["options"], opts)

    def test_non_admin_cannot_create_system_preset(self):
        # Administrator is admin; simulate a non-admin by patching the platform
        # admin check the API now uses (tenancy.is_platform_admin).
        with patch.object(presets.tenancy, "is_platform_admin", return_value=False):
            with self.assertRaises(frappe.PermissionError):
                presets.save(preset_name="Test System Preset", options="[]", system=1)

    def test_delete_removes_own_preset(self):
        presets.save(preset_name="Test User Preset", options="[]")
        frappe.db.commit()
        presets.delete("Test User Preset")
        frappe.db.commit()
        self.assertFalse(frappe.db.exists("WebODM Preset", "Test User Preset"))

    def test_flags_true_for_admin_on_system_preset(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        presets.save(preset_name="Test System Preset", options="[]", system=1)
        frappe.db.commit()
        row = {p["preset_name"]: p for p in presets.list_presets()}["Test System Preset"]
        self.assertTrue(row["can_write"])
        self.assertTrue(row["can_delete"])

    def test_flags_false_for_member_on_system_preset(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        presets.save(preset_name="Test System Preset", options="[]", system=1)
        frappe.db.commit()
        frappe.set_user(self.member)
        frappe.local.webodm_org_cache = {}
        row = {p["preset_name"]: p for p in presets.list_presets()}["Test System Preset"]
        self.assertFalse(row["can_write"])
        self.assertFalse(row["can_delete"])

    def test_flags_true_for_member_on_own_org_preset(self):
        presets.save(preset_name="Test User Preset", options="[]")
        frappe.db.commit()
        row = {p["preset_name"]: p for p in presets.list_presets()}["Test User Preset"]
        self.assertTrue(row["can_write"])
        self.assertTrue(row["can_delete"])

    def test_flags_do_not_replace_enforcement(self):
        """A false can_write flag is advisory: save() must still refuse the edit.

        Sending system=0 keeps save()'s *requested scope* guard
        ("Only administrators can manage system presets") out of the way, so the
        raise below can only come from the refactored _can_modify() check on the
        *existing* row, which is system=1.
        """
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        presets.save(preset_name="Test System Preset", options="[]", system=1)
        frappe.db.commit()
        frappe.set_user(self.member)
        frappe.local.webodm_org_cache = {}
        with patch.object(presets.tenancy, "is_platform_admin", return_value=False):
            with self.assertRaisesRegex(frappe.PermissionError,
                                        "Only administrators can edit system presets"):
                presets.save(preset_name="Test System Preset", options="[]",
                             system=0, name="Test System Preset")
            # The stored system preset has organization=None, so the org clause
            # alone would also deny it — pin the system branch directly with an
            # org that *does* match, where only that branch can produce False.
            self.assertFalse(presets._can_modify(1, presets.tenancy.get_current_org()))


class TestPresetOrgIsolation(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        # Orgs + memberships live at CLASS scope: this Frappe version rolls back
        # only at class teardown, so re-inserting the unique-named orgs in a
        # per-test setUp would collide on the second test method.
        super().setUpClass()
        frappe.local.webodm_org_cache = {}
        cls.org_a = frappe.get_doc({"doctype": "WebODM Organization", "organization_name": "Preset Iso A"}).insert(ignore_permissions=True).name
        cls.org_b = frappe.get_doc({"doctype": "WebODM Organization", "organization_name": "Preset Iso B"}).insert(ignore_permissions=True).name
        cls.a = _puser("preset_iso_a@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.a, "organization": cls.org_a, "role": "Owner"}).insert(ignore_permissions=True)
        cls.b = _puser("preset_iso_b@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.b, "organization": cls.org_b, "role": "Owner"}).insert(ignore_permissions=True)
        # No commit: FrappeTestCase rolls the whole class transaction back at
        # teardown, so these orgs/memberships never leak into the shared DB.

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_preset_not_visible_across_orgs(self):
        # A non-system preset created by org A must NOT surface for an org-B user.
        # Use frappe.get_list (applies permission_query_conditions), NOT get_all
        # which hardcodes ignore_permissions and would bypass the org scope.
        frappe.set_user(self.a)
        frappe.local.webodm_org_cache = {}
        p = frappe.get_doc({"doctype": "WebODM Preset", "preset_name": "A Secret Preset", "options": "[]"}).insert()
        # Positive control: org A's OWN user must see the preset via the same
        # get_list query. This anchors the negative assertion below so it can't
        # pass trivially (e.g. an empty result set for an unrelated reason).
        own_names = {x.name for x in frappe.get_list("WebODM Preset", limit_page_length=0)}
        self.assertIn(p.name, own_names)
        frappe.set_user(self.b)
        frappe.local.webodm_org_cache = {}
        names = {x.name for x in frappe.get_list("WebODM Preset", limit_page_length=0)}
        self.assertNotIn(p.name, names)

    def test_system_presets_visible_to_all_orgs(self):
        # Seeded system presets (system=1, no org) must remain cross-org visible.
        sys_name = frappe.get_doc({"doctype": "WebODM Preset", "preset_name": "Sys Shared Preset",
                                   "system": 1, "options": "[]"}).insert(ignore_permissions=True).name
        frappe.set_user(self.b)
        frappe.local.webodm_org_cache = {}
        names = {x.name for x in frappe.get_list("WebODM Preset", limit_page_length=0)}
        self.assertIn(sys_name, names)

    def test_flags_false_on_another_orgs_preset(self):
        # B cannot even see A's preset (org query conditions), so assert via the
        # helper directly — this is the rule the flags are derived from.
        frappe.set_user(self.b)
        frappe.local.webodm_org_cache = {}
        self.assertFalse(presets._can_modify(0, self.org_a))
        self.assertTrue(presets._can_modify(0, self.org_b))


class TestPresetOrgSharingAPI(FrappeTestCase):
    """API-level (presets.save / list_presets / delete) org-scoping behavior.

    Org A has two members who must SHARE presets through the API; org C's member
    is fully isolated. FrappeTestCase rolls the whole class transaction back at
    teardown, so fixtures are inserted without committing.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.local.webodm_org_cache = {}
        cls.org_a = frappe.get_doc({"doctype": "WebODM Organization", "organization_name": "Preset Share A"}).insert(ignore_permissions=True).name
        cls.org_c = frappe.get_doc({"doctype": "WebODM Organization", "organization_name": "Preset Share C"}).insert(ignore_permissions=True).name
        cls.m1 = _puser("preset_share_m1@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.m1, "organization": cls.org_a, "role": "Owner"}).insert(ignore_permissions=True)
        cls.m2 = _puser("preset_share_m2@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.m2, "organization": cls.org_a, "role": "Member"}).insert(ignore_permissions=True)
        cls.m3 = _puser("preset_share_m3@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.m3, "organization": cls.org_c, "role": "Owner"}).insert(ignore_permissions=True)

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def _act(self, user):
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}

    def test_org_members_share_presets_via_api(self):
        # m1 creates a preset; m2 (same org) must see it through the API.
        self._act(self.m1)
        saved = presets.save(preset_name="Shared By M1", options=json.dumps([{"name": "dsm", "value": True}]))
        self._act(self.m2)
        listed = {p["preset_name"]: p for p in presets.list_presets()}
        self.assertIn("Shared By M1", listed)
        self.assertEqual(listed["Shared By M1"]["name"], saved["name"])

    def test_different_org_cannot_see_edit_or_delete(self):
        # m1 (org A) creates; m3 (org C) must not see it and cannot edit/delete it.
        self._act(self.m1)
        saved = presets.save(preset_name="Org A Only", options="[]")
        target = saved["name"]
        self._act(self.m3)
        names = {p["preset_name"] for p in presets.list_presets()}
        self.assertNotIn("Org A Only", names)
        with self.assertRaises(frappe.PermissionError):
            presets.save(preset_name="Hijack", options="[]", name=target)
        with self.assertRaises(frappe.PermissionError):
            presets.delete(target)
        # The preset must survive the rejected cross-org delete/edit.
        self.assertTrue(frappe.db.exists("WebODM Preset", target))

    def test_system_presets_appear_in_every_org_list(self):
        sys_name = frappe.get_doc({"doctype": "WebODM Preset", "preset_name": "Sys For All",
                                   "system": 1, "options": "[]"}).insert(ignore_permissions=True).name
        for member in (self.m1, self.m3):
            self._act(member)
            names = {p["name"] for p in presets.list_presets()}
            self.assertIn(sys_name, names)

    def test_non_admin_cannot_save_system_preset(self):
        self._act(self.m1)
        with self.assertRaises(frappe.PermissionError):
            presets.save(preset_name="Illicit System", options="[]", system=1)


class TestPresetSystemDemotion(FrappeTestCase):
    """Demoting a system preset to org scope must not orphan it.

    Org stamping runs on before_insert ONLY, so an update that flips system 1->0
    would leave organization=None: list_presets()'s system query (system=1) no
    longer matches and its org query (organization=<org> AND system=0) can't match
    a NULL organization, making the preset invisible to every user via the API.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.local.webodm_org_cache = {}
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "Preset Demote Org"}).insert(ignore_permissions=True).name
        # The actor must be BOTH a platform admin (to touch system presets) and an
        # org member (so require_org() can resolve an org to adopt). No existing
        # fixture is both, so grant System Manager on top of the org membership.
        cls.admin = _puser("preset_demote_admin@example.com")
        u = frappe.get_doc("User", cls.admin)
        u.append("roles", {"role": "System Manager"})
        u.save(ignore_permissions=True)
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.admin,
                        "organization": cls.org, "role": "Owner"}).insert(ignore_permissions=True)

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_demoted_system_preset_stays_visible(self):
        frappe.set_user(self.admin)
        frappe.local.webodm_org_cache = {}
        self.assertTrue(presets.tenancy.is_platform_admin())
        self.assertEqual(presets.tenancy.get_current_org(), self.org)

        saved = presets.save(preset_name="Demote Me", options="[]", system=1)
        target = saved["name"]
        # Positive control: as a system preset it is visible before the demotion,
        # so a post-demotion disappearance can only come from the scope flip.
        self.assertIn(target, {p["name"] for p in presets.list_presets()})
        self.assertIsNone(frappe.db.get_value("WebODM Preset", target, "organization"))

        presets.save(preset_name="Demote Me", options="[]", system=0, name=target)

        listed = {p["name"]: p for p in presets.list_presets()}
        self.assertIn(target, listed, "demoted preset vanished from list_presets()")
        self.assertEqual(int(listed[target]["system"]), 0)
        self.assertIsNotNone(listed[target]["organization"],
                             "demoted preset kept organization=None and is orphaned")
        self.assertEqual(listed[target]["organization"], self.org)


class TestPresetSystemPromotion(FrappeTestCase):
    """Promoting an org preset to system scope must clear its organization.

    The rest of the codebase (tenancy stamping, permissions.py's preset query
    conditions) encodes the invariant "system preset => no organization". A
    promoted row that kept `organization` set would leave that org's ordinary
    members with write/delete through Frappe's generic REST stack, whose only
    gate is permissions.has_preset_permission -- it falls through to org scoping
    for every non-read ptype. That is a privilege escalation, so assert both the
    stored invariant and the permission outcome.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.local.webodm_org_cache = {}
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "Preset Promote Org"}).insert(ignore_permissions=True).name
        # The actor must be BOTH a platform admin (to touch system presets) and an
        # org member (so the pre-promotion org preset can be created at all).
        cls.admin = _puser("preset_promote_admin@example.com")
        u = frappe.get_doc("User", cls.admin)
        u.append("roles", {"role": "System Manager"})
        u.save(ignore_permissions=True)
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.admin,
                        "organization": cls.org, "role": "Owner"}).insert(ignore_permissions=True)
        # An ordinary member of the SAME org: the user who would gain write on a
        # promoted-but-still-stamped preset.
        cls.member = _puser("preset_promote_member@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.member,
                        "organization": cls.org, "role": "Member"}).insert(ignore_permissions=True)

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def _promote(self, preset_name):
        """Create an org preset as the admin, then flip it to system=1.

        Each test method passes a distinct name: this Frappe version rolls the
        transaction back only at class teardown, so rows created by one method
        are still present for the next and a shared name would collide on the PK.
        """
        frappe.set_user(self.admin)
        frappe.local.webodm_org_cache = {}
        self.assertTrue(presets.tenancy.is_platform_admin())
        saved = presets.save(preset_name=preset_name, options="[]")
        target = saved["name"]
        # Positive control: it really is org-stamped before the promotion, so a
        # post-promotion NULL can only come from the scope flip.
        self.assertEqual(frappe.db.get_value("WebODM Preset", target, "organization"), self.org)
        presets.save(preset_name=preset_name, options="[]", system=1, name=target)
        return target

    def test_promoted_preset_clears_organization(self):
        target = self._promote("Promote Me Invariant")
        self.assertEqual(int(frappe.db.get_value("WebODM Preset", target, "system")), 1)
        self.assertIsNone(frappe.db.get_value("WebODM Preset", target, "organization"),
                          "promoted preset kept its organization -- org members can still write it")

    def test_promoted_preset_denies_original_org_member_writes(self):
        from webodm_core import permissions

        target = self._promote("Promote Me Escalation")
        doc = frappe.get_doc("WebODM Preset", target)
        frappe.set_user(self.member)
        frappe.local.webodm_org_cache = {}
        # The generic REST stack's ONLY gate is this hook, and WebODM User holds
        # write:1/delete:1 on the doctype -- so False here is the whole defense.
        self.assertFalse(permissions.has_preset_permission(doc, "write", self.member))
        self.assertFalse(permissions.has_preset_permission(doc, "delete", self.member))
        # ...while the cross-org readability that list_presets() relies on stands.
        self.assertTrue(permissions.has_preset_permission(doc, "read", self.member))


class TestPresetSystemDeleteMessage(FrappeTestCase):
    """delete() must reject a non-admin on a system preset with its own message.

    Pinned with assertRaisesRegex: OrgContextError subclasses PermissionError, so
    a bare assertRaises would also pass if the call died in require_org() instead
    of the system-preset branch under test.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.local.webodm_org_cache = {}
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "Preset Sys Delete Org"}).insert(ignore_permissions=True).name
        cls.member = _puser("preset_sysdel_member@example.com")
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.member,
                        "organization": cls.org, "role": "Member"}).insert(ignore_permissions=True)

    def setUp(self):
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_non_admin_delete_of_system_preset_is_refused(self):
        target = frappe.get_doc({"doctype": "WebODM Preset", "preset_name": "Sys Undeletable",
                                 "system": 1, "options": "[]"}).insert(ignore_permissions=True).name
        frappe.set_user(self.member)
        frappe.local.webodm_org_cache = {}
        with self.assertRaisesRegex(frappe.PermissionError,
                                    "Only administrators can delete system presets"):
            presets.delete(target)
        self.assertTrue(frappe.db.exists("WebODM Preset", target))


if __name__ == "__main__":
    unittest.main()
