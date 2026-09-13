"""End-to-end: object detection over a real orthophoto via the live service.

Requires the geospatial image with the object-detection op (ONNX Runtime +
YOLOv8n) running as ``geospatial`` on the compose backend network, and a small
real image fixture (``/tmp/opencode/bus_ortho.tif``) with known objects.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import plugins as plugins_api
from webodm_core.plugins import runner, sync

ORTHO_FIXTURE = "/tmp/opencode/bus_ortho.tif"
ORG = "Object Detection E2E Org"


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({
            "doctype": "User", "email": email,
            "first_name": email.split("@")[0], "send_welcome_email": 0,
        }).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email)
    u.roles = []
    u.append("roles", {"role": "WebODM User"})
    u.save(ignore_permissions=True)
    return email


def _org(name):
    existing = frappe.db.get_value("WebODM Organization", {"organization_name": name}, "name")
    if existing:
        return existing
    return frappe.get_doc({
        "doctype": "WebODM Organization", "organization_name": name,
    }).insert(ignore_permissions=True).name


class TestObjectDetectionE2E(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._cleanup()
        result = sync.sync_catalog()
        assert "object-detection" in result["synced"], "geospatial catalog not reachable"

        cls.owner = _user("od_owner@example.com")
        cls.org = _org(ORG)
        if not frappe.db.exists("WebODM Org Membership", {"user": cls.owner, "organization": cls.org}):
            frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.owner,
                            "organization": cls.org, "role": "Owner"}).insert(ignore_permissions=True)

        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.owner)
        project = frappe.db.get_value("WebODM Project", {"title": "Object Detection E2E"}, "name") \
            or frappe.get_doc({"doctype": "WebODM Project", "title": "Object Detection E2E"}).insert().name
        task = frappe.get_doc({
            "doctype": "WebODM Task", "project": project,
            "title": "Object Detection E2E Task", "status": "Completed",
        }).insert()
        with open(ORTHO_FIXTURE, "rb") as f:
            content = f.read()
        fdoc = frappe.get_doc({
            "doctype": "File", "file_name": f"{task.name}_ortho.tif", "is_private": 1,
            "content": content, "attached_to_doctype": "WebODM Task",
            "attached_to_name": task.name,
        }).save(ignore_permissions=True)
        task.db_set("orthophoto", fdoc.file_url)
        cls.task_name = task.name
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        cls._cleanup()
        super().tearDownClass()

    @classmethod
    def _cleanup(cls):
        for name in frappe.get_all("WebODM Plugin Run",
                                   filters={"plugin": "object-detection"}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("WebODM Plugin Setting",
                                   filters={"plugin": "object-detection"}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Setting", name, force=True, ignore_permissions=True)

    def setUp(self):
        self._cleanup()
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def _as(self, user):
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}

    def _enable(self, settings=None):
        self._as(self.owner)
        payload = {"plugin": "object-detection", "enabled": True}
        if settings is not None:
            payload["settings"] = settings
        plugins_api.save_plugin_setting(**payload)
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_detection_end_to_end(self):
        self._enable()

        self._as(self.owner)
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            run_name = plugins_api.run_plugin(
                plugin="object-detection", task=self.task_name
            )["run"]
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

        runner.execute_run(run_name)
        run = frappe.get_doc("WebODM Plugin Run", run_name)
        self.assertEqual(run.status, "Completed", run.error)
        self.assertEqual(run.output_kind, "vector")
        self.assertTrue(run.output_file.endswith(".geojson"))

        metadata = json.loads(run.output_metadata)
        self.assertGreaterEqual(metadata["total"], 1)
        self.assertTrue(metadata["counts"], "expected per-class counts")

        self._as(self.owner)
        geojson = plugins_api.get_run_geojson(run_name)
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertTrue(geojson["features"])
        props = geojson["features"][0]["properties"]
        self.assertIn("class", props)
        self.assertIn("confidence", props)

        frappe.local.response = frappe._dict()
        plugins_api.download_run_output(run_name)
        self.assertEqual(frappe.local.response["type"], "download")
        self.assertTrue(frappe.local.response["filecontent"])

    def test_missing_model_rejected_before_run(self):
        self._enable(settings={"model": "does-not-exist.onnx"})
        before = frappe.db.count("WebODM Plugin Run", {"plugin": "object-detection"})

        self._as(self.owner)
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            with self.assertRaises(frappe.ValidationError):
                plugins_api.run_plugin(plugin="object-detection", task=self.task_name)

        self.assertEqual(
            frappe.db.count("WebODM Plugin Run", {"plugin": "object-detection"}), before
        )
