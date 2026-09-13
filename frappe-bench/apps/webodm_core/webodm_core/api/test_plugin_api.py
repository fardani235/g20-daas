"""Tests for plugin listing and per-org enable/configure (api.plugins)."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import plugins as plugins_api

PLUGIN_ID = "test-api-op"
SCHEMA = {
    "type": "object",
    "properties": {
        "interval_m": {"type": "number", "exclusiveMinimum": 0},
        "output_format": {"type": "string", "enum": ["GeoJSON", "GPKG"]},
        "model": {"type": "string", "default": "yolov8n.onnx"},
        "labels": {"type": "string", "default": "coco.txt"},
    },
    "required": ["interval_m"],
}


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": email.split("@")[0],
            "send_welcome_email": 0,
        }).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email)
    u.roles = []
    u.append("roles", {"role": "WebODM User"})
    u.save(ignore_permissions=True)
    return email


class TestPluginApi(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._cleanup()
        frappe.get_doc({
            "doctype": "WebODM Plugin",
            "plugin_id": PLUGIN_ID,
            "label": "API Test Op",
            "version": "1.0.0",
            "output_kind": "vector",
            "render_kind": "contours",
            "platform_enabled": 1,
            "available": 1,
            "params_schema": json.dumps(SCHEMA),
        }).insert(ignore_permissions=True)

        cls.owner = _user("plugin_api_owner@example.com")
        cls.member = _user("plugin_api_member@example.com")
        cls.org = frappe.db.get_value(
            "WebODM Organization", {"organization_name": "Plugin API Org"}, "name"
        ) or frappe.get_doc({
            "doctype": "WebODM Organization",
            "organization_name": "Plugin API Org",
        }).insert(ignore_permissions=True).name
        for user, role in ((cls.owner, "Owner"), (cls.member, "Member")):
            if not frappe.db.exists("WebODM Org Membership",
                                    {"user": user, "organization": cls.org}):
                frappe.get_doc({"doctype": "WebODM Org Membership", "user": user,
                                "organization": cls.org, "role": role}).insert(ignore_permissions=True)

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        cls._cleanup()
        frappe.delete_doc("WebODM Plugin", PLUGIN_ID, force=True, ignore_permissions=True)
        super().tearDownClass()

    @classmethod
    def _cleanup(cls):
        for name in frappe.get_all("WebODM Plugin Setting",
                                   filters={"plugin": PLUGIN_ID}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Setting", name, force=True, ignore_permissions=True)

    def setUp(self):
        self._cleanup()
        frappe.db.set_value("WebODM Plugin", PLUGIN_ID, "platform_enabled", 1)
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def _as(self, user):
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}

    def _entry(self):
        return next(p for p in plugins_api.list_plugins() if p["op_id"] == PLUGIN_ID)

    def test_list_shows_disabled_until_enabled(self):
        self._as(self.owner)
        entry = self._entry()
        self.assertFalse(entry["enabled"])
        self.assertFalse(entry["runnable"])
        self.assertTrue(entry["platform_enabled"])

        plugins_api.save_plugin_setting(plugin=PLUGIN_ID, enabled=True)
        entry = self._entry()
        self.assertTrue(entry["enabled"])
        self.assertTrue(entry["runnable"])

    def test_platform_kill_switch_overrides_org_enablement(self):
        self._as(self.owner)
        plugins_api.save_plugin_setting(plugin=PLUGIN_ID, enabled=True)
        frappe.set_user("Administrator")
        frappe.db.set_value("WebODM Plugin", PLUGIN_ID, "platform_enabled", 0)

        self._as(self.owner)
        entry = self._entry()
        self.assertTrue(entry["enabled"])
        self.assertFalse(entry["runnable"])

    def test_non_admin_cannot_change_settings(self):
        self._as(self.member)
        with self.assertRaises(frappe.PermissionError):
            plugins_api.save_plugin_setting(plugin=PLUGIN_ID, enabled=True)

    def test_invalid_settings_rejected(self):
        self._as(self.owner)
        with self.assertRaises(frappe.ValidationError):
            plugins_api.save_plugin_setting(plugin=PLUGIN_ID, enabled=True,
                                            settings={"interval_m": -1})
        with self.assertRaises(frappe.ValidationError):
            plugins_api.save_plugin_setting(plugin=PLUGIN_ID, enabled=True,
                                            settings={"interval_m": 5, "bogus": 1})
        with self.assertRaises(frappe.ValidationError):
            # required parameter missing
            plugins_api.save_plugin_setting(plugin=PLUGIN_ID, enabled=True, settings={})

        # Nothing should have been stored.
        self.assertFalse(frappe.db.exists("WebODM Plugin Setting",
                                          {"plugin": PLUGIN_ID, "organization": self.org}))

    def test_model_labels_defaults_and_override(self):
        self._as(self.owner)
        # Defaults surface in the catalog schema...
        entry = self._entry()
        self.assertEqual(entry["params_schema"]["properties"]["model"]["default"], "yolov8n.onnx")
        self.assertEqual(entry["params_schema"]["properties"]["labels"]["default"], "coco.txt")

        # ...and an organization can override them.
        plugins_api.save_plugin_setting(
            plugin=PLUGIN_ID, enabled=True,
            settings={"interval_m": 5, "model": "custom.onnx", "labels": "custom.txt"},
        )
        entry = self._entry()
        self.assertEqual(entry["settings"]["model"], "custom.onnx")
        self.assertEqual(entry["settings"]["labels"], "custom.txt")

    def test_valid_settings_stored(self):
        self._as(self.owner)
        result = plugins_api.save_plugin_setting(
            plugin=PLUGIN_ID, enabled=True,
            settings={"interval_m": 5, "output_format": "GPKG"},
        )
        self.assertTrue(result["enabled"])
        self.assertEqual(result["settings"]["interval_m"], 5)
        self.assertEqual(result["settings"]["output_format"], "GPKG")

        entry = self._entry()
        self.assertEqual(entry["settings"]["interval_m"], 5)
