"""Catalog sync tests: create, idempotency, removal, unreachable, scheduler hook."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.plugins import sync
from webodm_core.plugins.geospatial import GeospatialUnavailable

NEW_OP = {
    "op_id": "test-sync-new",
    "label": "Sync New",
    "version": "1.0.0",
    "description": "a synced op",
    "params_schema": {"type": "object", "properties": {}},
    "output_kind": "raster",
    "render_kind": "dem",
}
GONE_OP = {
    "op_id": "test-sync-gone",
    "label": "Sync Gone",
    "version": "1.0.0",
    "output_kind": "vector",
    "render_kind": "contours",
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


def _cleanup(prefix):
    for name in frappe.get_all("WebODM Plugin Run", filters={"plugin": ["like", f"{prefix}%"]}, pluck="name"):
        frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
    for name in frappe.get_all("WebODM Plugin", filters={"plugin_id": ["like", f"{prefix}%"]}, pluck="name"):
        frappe.delete_doc("WebODM Plugin", name, force=True, ignore_permissions=True)


class TestPluginCatalogSync(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _cleanup("test-sync-")
        cls.member = _user("catalog_member@example.com")
        cls.org = frappe.db.get_value(
            "WebODM Organization", {"organization_name": "Catalog Sync Org"}, "name"
        ) or frappe.get_doc({
            "doctype": "WebODM Organization",
            "organization_name": "Catalog Sync Org",
        }).insert(ignore_permissions=True).name
        if not frappe.db.exists("WebODM Org Membership",
                                {"user": cls.member, "organization": cls.org}):
            frappe.get_doc({
                "doctype": "WebODM Org Membership",
                "user": cls.member,
                "organization": cls.org,
                "role": "Owner",
            }).insert(ignore_permissions=True)

        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.member)
        cls.project_name = frappe.db.get_value(
            "WebODM Project", {"title": "Catalog Sync Project"}, "name"
        ) or frappe.get_doc({
            "doctype": "WebODM Project",
            "title": "Catalog Sync Project",
        }).insert().name
        task = frappe.get_doc({
            "doctype": "WebODM Task",
            "project": cls.project_name,
            "title": "Catalog Sync Task",
            "status": "Completed",
        }).insert()
        cls.task_name = task.name
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        _cleanup("test-sync-")
        frappe.delete_doc("WebODM Task", cls.task_name, force=True, ignore_permissions=True)
        frappe.delete_doc("WebODM Project", cls.project_name, force=True, ignore_permissions=True)
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        _cleanup("test-sync-")

    def test_sync_creates_new_plugin(self):
        sync.upsert_catalog([NEW_OP])
        self.assertTrue(frappe.db.exists("WebODM Plugin", "test-sync-new"))
        doc = frappe.get_doc("WebODM Plugin", "test-sync-new")
        self.assertEqual(doc.label, "Sync New")
        self.assertEqual(doc.platform_enabled, 1)
        self.assertEqual(doc.available, 1)
        self.assertEqual(doc.render_kind, "dem")

    def test_sync_is_idempotent(self):
        sync.upsert_catalog([NEW_OP])
        sync.upsert_catalog([{**NEW_OP, "label": "Sync New v2"}])
        rows = frappe.get_all("WebODM Plugin", filters={"plugin_id": "test-sync-new"}, pluck="name")
        self.assertEqual(len(rows), 1)
        self.assertEqual(frappe.db.get_value("WebODM Plugin", "test-sync-new", "label"), "Sync New v2")

    def test_sync_preserves_platform_kill_switch(self):
        sync.upsert_catalog([NEW_OP])
        frappe.db.set_value("WebODM Plugin", "test-sync-new", "platform_enabled", 0)
        sync.upsert_catalog([NEW_OP])
        self.assertEqual(frappe.db.get_value("WebODM Plugin", "test-sync-new", "platform_enabled"), 0)

    def test_sync_marks_removed_unavailable_and_preserves_runs(self):
        sync.upsert_catalog([NEW_OP, GONE_OP])

        frappe.set_user(self.member)
        frappe.local.webodm_org_cache = {}
        run = frappe.get_doc({
            "doctype": "WebODM Plugin Run",
            "plugin": "test-sync-gone",
            "task": self.task_name,
        }).insert()
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

        # Next sync no longer returns GONE_OP.
        sync.upsert_catalog([NEW_OP])

        self.assertTrue(frappe.db.exists("WebODM Plugin", "test-sync-gone"))
        self.assertEqual(frappe.db.get_value("WebODM Plugin", "test-sync-gone", "available"), 0)
        self.assertTrue(frappe.db.exists("WebODM Plugin Run", run.name))
        self.assertEqual(
            frappe.db.get_value("WebODM Plugin Run", run.name, "plugin"), "test-sync-gone"
        )

    def test_sync_safe_leaves_catalog_intact_when_unreachable(self):
        sync.upsert_catalog([NEW_OP])
        before = frappe.db.get_value("WebODM Plugin", "test-sync-new", "available")

        original = sync.fetch_catalog

        def _boom():
            raise GeospatialUnavailable("service down")

        sync.fetch_catalog = _boom
        try:
            result = sync.sync_catalog_safe()
        finally:
            sync.fetch_catalog = original

        self.assertFalse(result["ok"])
        self.assertIn("service down", result["error"])
        self.assertEqual(frappe.db.get_value("WebODM Plugin", "test-sync-new", "available"), before)

    def test_sync_stores_timeout_seconds(self):
        sync.upsert_catalog([{**NEW_OP, "timeout_seconds": 1800}])
        self.assertEqual(
            frappe.db.get_value("WebODM Plugin", "test-sync-new", "timeout_seconds"), 1800
        )

    def test_sync_timeout_absent_is_zero(self):
        sync.upsert_catalog([NEW_OP])
        self.assertEqual(
            frappe.db.get_value("WebODM Plugin", "test-sync-new", "timeout_seconds"), 0
        )

    def test_sync_stores_needs_validation(self):
        sync.upsert_catalog([{**NEW_OP, "needs_validation": True}])
        self.assertEqual(
            frappe.db.get_value("WebODM Plugin", "test-sync-new", "needs_validation"), 1
        )
        sync.upsert_catalog([NEW_OP])
        self.assertEqual(
            frappe.db.get_value("WebODM Plugin", "test-sync-new", "needs_validation"), 0
        )

    def test_sync_stores_curated_models(self):
        models = [{"id": "coco", "model": "yolov8n.onnx", "labels": "coco.txt",
                   "family": "yolo", "label_offset": 0}]
        sync.upsert_catalog([{**NEW_OP, "models": models}])
        stored = frappe.db.get_value("WebODM Plugin", "test-sync-new", "models")
        self.assertEqual(json.loads(stored)[0]["id"], "coco")

    def test_scheduler_registers_catalog_sync(self):
        import webodm_core.hooks as hooks

        cron = hooks.scheduler_events["cron"]
        self.assertIn("webodm_core.plugins.sync.sync_catalog_safe", cron.get("*/5 * * * *", []))

    def test_sync_now_requires_platform_admin(self):
        frappe.set_user(self.member)
        frappe.local.webodm_org_cache = {}
        try:
            with self.assertRaises(frappe.PermissionError):
                sync.sync_now()
        finally:
            frappe.set_user("Administrator")
            frappe.local.webodm_org_cache = {}
