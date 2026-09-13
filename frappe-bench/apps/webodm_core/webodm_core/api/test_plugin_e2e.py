"""End-to-end: Frappe orchestrates a real geospatial service and serves outputs.

Requires the local geospatial image (with the /analysis API) running as
``plugin-geospatial`` on the compose backend network, with the shared sites
volume mounted. Exercises the full vertical: catalog sync -> enable -> run ->
persist -> serve tiles/GeoJSON -> download, plus the security rejections.
"""

import json
import math
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import plugins as plugins_api
from webodm_core.api import tiles as tiles_api
from webodm_core.plugins import runner, sync

DSM_FIXTURE = "/tmp/opencode/e2e_dsm.tif"
ORG = "Plugin E2E Org"
ORG_OTHER = "Plugin E2E Other Org"


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


def _join(user, org, role="Owner"):
    if frappe.db.exists("WebODM Org Membership", {"user": user, "organization": org}):
        return
    frappe.get_doc({"doctype": "WebODM Org Membership", "user": user,
                    "organization": org, "role": role}).insert(ignore_permissions=True)


def _deg2tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


class TestPluginE2E(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._cleanup()

        # Catalog synced from the LIVE geospatial service.
        result = sync.sync_catalog()
        assert "contours" in result["synced"], "geospatial catalog not reachable"
        assert "hillshade" in result["synced"]

        cls.owner = _user("e2e_owner@example.com")
        cls.outsider = _user("e2e_outsider@example.com")
        cls.org = _org(ORG)
        cls.org_other = _org(ORG_OTHER)
        _join(cls.owner, cls.org)
        _join(cls.outsider, cls.org_other)

        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.owner)
        cls.project_name = frappe.db.get_value(
            "WebODM Project", {"title": "Plugin E2E Project"}, "name"
        ) or frappe.get_doc({
            "doctype": "WebODM Project", "title": "Plugin E2E Project",
        }).insert().name

        with open(DSM_FIXTURE, "rb") as f:
            dsm_bytes = f.read()
        task = frappe.get_doc({
            "doctype": "WebODM Task",
            "project": cls.project_name,
            "title": "Plugin E2E Task",
            "status": "Completed",
        }).insert()
        fdoc = frappe.get_doc({
            "doctype": "File",
            "file_name": f"{task.name}_e2e_dsm.tif",
            "is_private": 1,
            "content": dsm_bytes,
            "attached_to_doctype": "WebODM Task",
            "attached_to_name": task.name,
        }).save(ignore_permissions=True)
        task.db_set("dsm", fdoc.file_url)
        cls.task_name = task.name

        # A completed task with no DSM, to prove missing-input rejection.
        cls.task_no_dsm = frappe.get_doc({
            "doctype": "WebODM Task",
            "project": cls.project_name,
            "title": "Plugin E2E No DSM",
            "status": "Completed",
        }).insert().name
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
                                   filters={"plugin": ["in", ["contours", "hillshade"]]}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("WebODM Plugin Setting",
                                   filters={"plugin": ["in", ["contours", "hillshade"]]}, pluck="name"):
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

    def _enable(self, plugin, user=None, settings=None):
        user = user or self.owner
        self._as(user)
        payload = {"plugin": plugin, "enabled": True}
        if settings is not None:
            payload["settings"] = settings
        plugins_api.save_plugin_setting(**payload)
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def _run_sync(self, plugin, task=None):
        self._as(self.owner)
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            run_name = plugins_api.run_plugin(
                plugin=plugin, task=task or self.task_name
            )["run"]
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        # No worker is running in this container; execute synchronously and let
        # it call the real geospatial service.
        runner.execute_run(run_name)
        return frappe.get_doc("WebODM Plugin Run", run_name)

    def test_contours_end_to_end_geojson_and_download(self):
        self._enable("contours", settings={"interval_m": 5})

        run = self._run_sync("contours")
        self.assertEqual(run.status, "Completed", run.error)
        self.assertEqual(run.output_kind, "vector")
        self.assertTrue(run.output_file)

        self._as(self.owner)
        geojson = plugins_api.get_run_geojson(run.name)
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertGreater(len(geojson["features"]), 0)

        frappe.local.response = frappe._dict()
        plugins_api.download_run_output(run.name)
        self.assertEqual(frappe.local.response["type"], "download")
        self.assertTrue(len(frappe.local.response["filecontent"]) > 0)

        self._as(self.outsider)
        with self.assertRaises(frappe.PermissionError):
            plugins_api.get_run_geojson(run.name)
        with self.assertRaises(frappe.PermissionError):
            plugins_api.download_run_output(run.name)

    def test_hillshade_end_to_end_tiles(self):
        self._enable("hillshade", settings={"azimuth": 315, "altitude": 45})

        run = self._run_sync("hillshade")
        self.assertEqual(run.status, "Completed", run.error)
        self.assertEqual(run.output_kind, "raster")
        self.assertTrue(run.output_extent)

        self._as(self.owner)
        info = tiles_api.run_info(run.name)
        self.assertIn("bounds", info)
        minx, miny, maxx, maxy = info["bounds"]
        z = 14
        x, y = _deg2tile((miny + maxy) / 2.0, (minx + maxx) / 2.0, z)
        resp = tiles_api.serve_run(run.name, z, x, y)
        self.assertEqual(resp.mimetype, "image/png")
        self.assertTrue(resp.get_data().startswith(b"\x89PNG"))

        self._as(self.outsider)
        with self.assertRaises(frappe.PermissionError):
            tiles_api.serve_run(run.name, z, x, y)

    def test_missing_input_rejected(self):
        self._enable("contours", settings={"interval_m": 5})
        self._as(self.owner)
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            with self.assertRaises(frappe.ValidationError):
                plugins_api.run_plugin(plugin="contours", task=self.task_no_dsm)

    def test_disabled_and_platform_denied(self):
        # Not enabled for the org yet.
        self._as(self.owner)
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            with self.assertRaises(frappe.PermissionError):
                plugins_api.run_plugin(plugin="contours", task=self.task_name)

        self._enable("contours", settings={"interval_m": 5})
        frappe.db.set_value("WebODM Plugin", "contours", "platform_enabled", 0)
        self._as(self.owner)
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            with self.assertRaises(frappe.PermissionError):
                plugins_api.run_plugin(plugin="contours", task=self.task_name)
        frappe.db.set_value("WebODM Plugin", "contours", "platform_enabled", 1)
