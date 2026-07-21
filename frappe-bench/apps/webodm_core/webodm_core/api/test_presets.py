import json
import unittest
from unittest.mock import patch, MagicMock

import frappe

from webodm_core.api import presets


def _cleanup(names):
    for n in names:
        if frappe.db.exists("WebODM Preset", n):
            frappe.delete_doc("WebODM Preset", n, force=True, ignore_permissions=True)
    frappe.db.commit()


class TestPresets(unittest.TestCase):
    def tearDown(self):
        _cleanup(["Test User Preset", "Test System Preset"])
        frappe.set_user("Administrator")

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


if __name__ == "__main__":
    unittest.main()
