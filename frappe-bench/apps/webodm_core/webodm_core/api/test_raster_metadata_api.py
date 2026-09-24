"""api.raster: permission-gated access to stored raster metadata, on-demand
extraction for tasks processed before metadata existed, and refresh."""
import io
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import raster as api
from webodm_core.plugins.files import save_private_file_from_stream
from webodm_core.plugins.geospatial import GeospatialError
from webodm_core.webodm_core.processing import raster_metadata as rm
from webodm_core.webodm_core.processing.test_raster_metadata import DEM_META, SERVICE_META


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": "api",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email)
    u.roles = []
    u.append("roles", {"role": "WebODM User"})
    u.save(ignore_permissions=True)
    return email


def _org(name, owner):
    org = frappe.get_doc({"doctype": "WebODM Organization",
                          "organization_name": name}).insert(ignore_permissions=True).name
    frappe.get_doc({"doctype": "WebODM Org Membership", "user": owner,
                    "organization": org, "role": "Owner"}).insert(ignore_permissions=True)
    return org


class TestRasterMetadataApi(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = _user("rmapi_owner@example.com")
        cls.outsider = _user("rmapi_outsider@example.com")
        cls.org = _org("RM API Org", cls.owner)
        cls.other_org = _org("RM API Other Org", cls.outsider)
        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.owner)
        cls.project = frappe.get_doc({"doctype": "WebODM Project", "title": "RM API Project"}).insert().name
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def setUp(self):
        frappe.local.webodm_org_cache = {}
        frappe.set_user(self.owner)
        self.task = frappe.get_doc({"doctype": "WebODM Task", "project": self.project,
                                    "title": "RM API Task", "status": "Completed"}).insert()
        frappe.set_user("Administrator")
        f = save_private_file_from_stream(io.BytesIO(b"II*\x00ortho"), f"{self.task.name}_orthophoto.tif",
                                          attached_to_doctype="WebODM Task", attached_to_name=self.task.name,
                                          ignore_permissions=True)
        self.task.db_set("orthophoto", f.file_url)
        f = save_private_file_from_stream(io.BytesIO(b"II*\x00dsm"), f"{self.task.name}_dsm.tif",
                                          attached_to_doctype="WebODM Task", attached_to_name=self.task.name,
                                          ignore_permissions=True)
        self.task.db_set("dsm", f.file_url)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        for f in frappe.get_all("File", filters={"attached_to_doctype": "WebODM Task",
                                                 "attached_to_name": self.task.name}, pluck="name"):
            frappe.delete_doc("File", f, ignore_permissions=True, force=True)
        frappe.delete_doc("WebODM Task", self.task.name, ignore_permissions=True, force=True)

    def _as(self, user):
        frappe.local.webodm_org_cache = {}
        frappe.set_user(user)

    def test_returns_stored_metadata_without_touching_the_service(self):
        rm.store(self.task.name, "orthophoto", rm.normalize(SERVICE_META))
        self._as(self.owner)
        with patch.object(rm, "fetch_raster_metadata") as fetch:
            meta = api.get_metadata(self.task.name, "orthophoto")
        fetch.assert_not_called()
        self.assertEqual(meta["dataset"], "orthophoto")
        self.assertEqual(meta["width"], 38000)
        self.assertEqual(meta["geotransform"], SERVICE_META["geotransform"])
        self.assertEqual(meta["overviews"], SERVICE_META["overviews"])

    def test_extracts_on_demand_for_legacy_tasks(self):
        self._as(self.owner)
        with patch.object(rm, "fetch_raster_metadata", return_value=DEM_META) as fetch:
            meta = api.get_metadata(self.task.name, "dsm")
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(fetch.call_args.args[0].endswith(f"{self.task.name}_dsm.tif"))
        self.assertEqual(meta["nodata"], -9999.0)
        # Stored: the next call is served from the row.
        with patch.object(rm, "fetch_raster_metadata") as fetch:
            api.get_metadata(self.task.name, "dsm")
        fetch.assert_not_called()

    def test_refresh_reextracts_and_replaces(self):
        rm.store(self.task.name, "orthophoto", rm.normalize({**SERVICE_META, "width": 1}))
        self._as(self.owner)
        with patch.object(rm, "fetch_raster_metadata", return_value=SERVICE_META):
            meta = api.get_metadata(self.task.name, "orthophoto", refresh=1)
        self.assertEqual(meta["width"], 38000)
        self.assertEqual(len(frappe.get_doc("WebODM Task", self.task.name).raster_metadata), 1)

    def test_unreadable_raster_surfaces_error_and_records_it(self):
        self._as(self.owner)
        with patch.object(rm, "fetch_raster_metadata",
                          side_effect=GeospatialError("raster metadata read failed: not a readable raster")):
            with self.assertRaises(frappe.ValidationError) as ctx:
                api.get_metadata(self.task.name, "orthophoto")
        self.assertIn("not a readable raster", str(ctx.exception))
        # Without refresh the recorded failure is returned instead of retried.
        with patch.object(rm, "fetch_raster_metadata") as fetch:
            meta = api.get_metadata(self.task.name, "orthophoto")
        fetch.assert_not_called()
        self.assertIn("not a readable raster", meta["error"])

    def test_missing_dataset_and_unknown_dataset(self):
        self._as(self.owner)
        with self.assertRaises(frappe.DoesNotExistError):
            api.get_metadata(self.task.name, "dtm")
        with self.assertRaises(frappe.ValidationError):
            api.get_metadata(self.task.name, "point_cloud")

    def test_other_organization_is_denied(self):
        rm.store(self.task.name, "orthophoto", rm.normalize(SERVICE_META))
        self._as(self.outsider)
        with self.assertRaises(frappe.PermissionError):
            api.get_metadata(self.task.name, "orthophoto")
        with self.assertRaises(frappe.PermissionError):
            api.list_metadata(self.task.name)

    def test_list_covers_every_raster_and_isolates_failures(self):
        rm.store(self.task.name, "orthophoto", rm.normalize(SERVICE_META))
        self._as(self.owner)
        with patch.object(rm, "fetch_raster_metadata", side_effect=GeospatialError("corrupt")) as fetch, \
             patch("frappe.log_error"):
            out = api.list_metadata(self.task.name)
        self.assertEqual(fetch.call_count, 1)          # only the dsm needed extraction
        self.assertEqual(set(out), {"orthophoto", "dsm"})
        self.assertEqual(out["orthophoto"]["width"], 38000)
        self.assertIn("corrupt", out["dsm"]["error"])

    def test_task_dict_carries_rows_for_the_viewer(self):
        rm.store(self.task.name, "orthophoto", rm.normalize(SERVICE_META))
        self._as(self.owner)
        d = frappe.get_doc("WebODM Task", self.task.name).as_dict()
        self.assertEqual(d["raster_metadata"][0]["dataset"], "orthophoto")
        self.assertEqual(d["raster_metadata"][0]["band_count"], 4)
