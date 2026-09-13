"""Tests for plugin output serving: tiles, GeoJSON, download, access control."""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import plugins as plugins_api
from webodm_core.api import tiles as tiles_api

PLUGIN_ID = "test-out-op"
FEATURECOLLECTION = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {"level": 5},
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
    }],
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


def _org(name):
    existing = frappe.db.get_value("WebODM Organization", {"organization_name": name}, "name")
    if existing:
        return existing
    return frappe.get_doc({
        "doctype": "WebODM Organization",
        "organization_name": name,
    }).insert(ignore_permissions=True).name


def _join(user, org, role="Owner"):
    if frappe.db.exists("WebODM Org Membership", {"user": user, "organization": org}):
        return
    frappe.get_doc({"doctype": "WebODM Org Membership", "user": user,
                    "organization": org, "role": role}).insert(ignore_permissions=True)


class _FakeResponse:
    def __init__(self, payload=None, content=b"PNGDATA"):
        self._payload = payload if payload is not None else {}
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class TestPluginOutputs(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._cleanup()
        if not frappe.db.exists("WebODM Plugin", PLUGIN_ID):
            frappe.get_doc({
                "doctype": "WebODM Plugin",
                "plugin_id": PLUGIN_ID,
                "label": "Output Test Op",
                "version": "1.0.0",
                "output_kind": "raster",
                "render_kind": "dem",
                "platform_enabled": 1,
                "available": 1,
                "params_schema": "{}",
                "inputs": json.dumps([{"name": "raster", "datasets": ["dsm"]}]),
            }).insert(ignore_permissions=True)

        cls.owner = _user("out_owner@example.com")
        cls.outsider = _user("out_outsider@example.com")
        cls.org = _org("Plugin Output Org")
        cls.org_other = _org("Plugin Output Other Org")
        _join(cls.owner, cls.org)
        _join(cls.outsider, cls.org_other)

        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.owner)
        cls.project_name = frappe.db.get_value(
            "WebODM Project", {"title": "Plugin Output Project"}, "name"
        ) or frappe.get_doc({
            "doctype": "WebODM Project",
            "title": "Plugin Output Project",
        }).insert().name
        task = frappe.get_doc({
            "doctype": "WebODM Task",
            "project": cls.project_name,
            "title": "Plugin Output Task",
            "status": "Completed",
        }).insert()
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
        for name in frappe.get_all("WebODM Plugin Run", filters={"plugin": PLUGIN_ID}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)

    def setUp(self):
        self._cleanup()
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def _make_run(self, output_kind, render_kind, file_name, content):
        unique = frappe.generate_hash(length=10)
        if content.lstrip().startswith(b"{"):
            payload = json.loads(content)
            payload["_test_id"] = unique
            content = json.dumps(payload).encode()
        else:
            content = content + unique.encode()
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": f"{unique}_{file_name}",
            "is_private": 1,
            "content": content,
        }).save(ignore_permissions=True)

        frappe.set_user(self.owner)
        frappe.local.webodm_org_cache = {}
        run = frappe.get_doc({
            "doctype": "WebODM Plugin Run",
            "plugin": PLUGIN_ID,
            "task": self.task_name,
            "status": "Completed",
            "output_kind": output_kind,
            "render_kind": render_kind,
        }).insert()
        run.db_set("output_file", file_doc.file_url)
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        return run.name

    def test_raster_run_info_and_tiles(self):
        run_name = self._make_run("raster", "dem", "dem_out.tif", b"FAKETIFF")

        with patch.object(tiles_api.requests, "get",
                          return_value=_FakeResponse({"bounds": [0, 0, 1, 1]})):
            info = tiles_api.run_info(run_name)
        self.assertEqual(info["bounds"], [0, 0, 1, 1])

        with patch.object(tiles_api.requests, "get", return_value=_FakeResponse()):
            resp = tiles_api.serve_run(run_name, 1, 0, 0)
        self.assertEqual(resp.mimetype, "image/png")
        self.assertEqual(resp.get_data(), b"PNGDATA")

    def test_cross_org_raster_denied(self):
        run_name = self._make_run("raster", "dem", "dem_out2.tif", b"FAKETIFF")
        frappe.set_user(self.outsider)
        frappe.local.webodm_org_cache = {}
        with self.assertRaises(frappe.PermissionError):
            tiles_api.serve_run(run_name, 1, 0, 0)

    def test_vector_geojson_returned(self):
        run_name = self._make_run(
            "vector", "contours", "contours.geojson", json.dumps(FEATURECOLLECTION).encode()
        )
        frappe.set_user(self.owner)
        frappe.local.webodm_org_cache = {}
        data = plugins_api.get_run_geojson(run_name)
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual(len(data["features"]), 1)

    def test_vector_gpkg_converted_via_service(self):
        run_name = self._make_run("vector", "contours", "contours.gpkg", b"FAKEGPKG")
        frappe.set_user(self.owner)
        frappe.local.webodm_org_cache = {}

        def _fake_convert(path, output_path, timeout=120):
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(FEATURECOLLECTION, f)
            return {"path": output_path}

        with patch("webodm_core.plugins.geospatial.vector_to_geojson", _fake_convert):
            data = plugins_api.get_run_geojson(run_name)
        self.assertEqual(data["type"], "FeatureCollection")

    def test_cross_org_geojson_denied(self):
        run_name = self._make_run(
            "vector", "contours", "contours2.geojson", json.dumps(FEATURECOLLECTION).encode()
        )
        frappe.set_user(self.outsider)
        frappe.local.webodm_org_cache = {}
        with self.assertRaises(frappe.PermissionError):
            plugins_api.get_run_geojson(run_name)

    def test_download_output(self):
        run_name = self._make_run("raster", "dem", "download.tif", b"BYTES123")
        frappe.set_user(self.owner)
        frappe.local.webodm_org_cache = {}
        frappe.local.response = frappe._dict()
        plugins_api.download_run_output(run_name)
        self.assertEqual(frappe.local.response["type"], "download")
        self.assertIn(b"BYTES123", frappe.local.response["filecontent"])
        self.assertTrue(frappe.local.response["filename"].endswith(".tif"))

    def test_cross_org_download_denied(self):
        run_name = self._make_run("raster", "dem", "download2.tif", b"BYTES123")
        frappe.set_user(self.outsider)
        frappe.local.webodm_org_cache = {}
        with self.assertRaises(frappe.PermissionError):
            plugins_api.download_run_output(run_name)
