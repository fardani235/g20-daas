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
        # Administrator is admin; simulate a non-admin by role check patch.
        with patch.object(presets, "_is_admin", return_value=False):
            with self.assertRaises(frappe.PermissionError):
                presets.save(preset_name="Test System Preset", options="[]", system=1)

    def test_delete_removes_own_preset(self):
        presets.save(preset_name="Test User Preset", options="[]")
        frappe.db.commit()
        presets.delete("Test User Preset")
        frappe.db.commit()
        self.assertFalse(frappe.db.exists("WebODM Preset", "Test User Preset"))


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
        p = frappe.get_doc({"doctype": "WebODM Preset", "preset_name": "A Secret Preset", "options": "[]"}).insert()
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


if __name__ == "__main__":
    unittest.main()
