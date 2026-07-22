import unittest
from unittest.mock import patch

import frappe

from webodm_core.api import settings


class TestSettings(unittest.TestCase):
    def test_non_admin_cannot_save(self):
        with patch.object(settings, "_is_admin", return_value=False):
            with self.assertRaises(frappe.PermissionError):
                settings.save(max_file_size_mb=999)

    def test_get_returns_known_fields(self):
        out = settings.get()
        self.assertIn("default_preset", out)
        self.assertIn("auto_start_processing", out)
        self.assertIn("max_file_size_mb", out)

    def test_save_persists_allowed_field(self):
        settings.save(max_file_size_mb=321, auto_start_processing=1)
        frappe.db.commit()
        out = settings.get()
        self.assertEqual(int(out["max_file_size_mb"]), 321)
        self.assertEqual(int(out["auto_start_processing"]), 1)

    def test_save_ignores_unknown_field(self):
        # Must not raise even if the caller passes a field that isn't on the doc.
        settings.save(not_a_real_field="x", max_file_size_mb=222)
        frappe.db.commit()
        self.assertEqual(int(settings.get()["max_file_size_mb"]), 222)


if __name__ == "__main__":
    unittest.main()
