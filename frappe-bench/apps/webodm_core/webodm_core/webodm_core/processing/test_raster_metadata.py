"""Raster metadata: service dict -> child row -> public dict, the pipeline hook
(never raises, records failures) and its wiring into _download_assets."""
import io
import os
import unittest
import zipfile
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.plugins.geospatial import GeospatialError, GeospatialUnavailable
from webodm_core.webodm_core.processing import raster_metadata as rm
from webodm_core.webodm_core.processing import task_runner

# What the geospatial service returns for a typical ODM orthophoto after cogify.
SERVICE_META = {
    "driver": "GTiff",
    "file_size": 3_221_225_472,           # 3 GiB: must survive the Int column
    "width": 38000, "height": 27000, "band_count": 4, "dtype": "uint8",
    "epsg": 32613, "crs_wkt": None, "crs_units": "metre",
    "is_georeferenced": True,
    "geotransform": [500000.0, 0.05, 0.0, 4500000.0, 0.0, -0.05],
    "pixel_size": [0.05, 0.05],
    "bounds": [500000.0, 4498650.0, 501900.0, 4500000.0],
    "bounds_4326": [-105.0001, 40.6401, -104.9776, 40.6524],
    "extent": {"type": "Polygon", "coordinates": []},
    "nodata": None,
    "block_width": 512, "block_height": 512, "is_tiled": True,
    "compression": "deflate", "overviews": [2, 4, 8, 16, 32, 64], "is_cog": True,
    "color_interp": ["red", "green", "blue", "alpha"],
}

DEM_META = {
    **SERVICE_META,
    "band_count": 1, "dtype": "float32", "nodata": -9999.0,
    "color_interp": ["gray"], "file_size": 900_000_000,
}


class TestNormalize(unittest.TestCase):
    def test_full_service_dict_maps_to_columns(self):
        row = rm.normalize(SERVICE_META)
        self.assertEqual(row["driver"], "GTiff")
        self.assertEqual(row["file_size"], 3_221_225_472)
        self.assertEqual((row["width"], row["height"], row["band_count"]), (38000, 27000, 4))
        self.assertEqual(row["dtype"], "uint8")
        self.assertEqual(row["epsg"], 32613)
        self.assertEqual(row["crs_wkt"], "")
        self.assertEqual(row["crs_units"], "metre")
        self.assertEqual((row["is_georeferenced"], row["has_transform"]), (1, 1))
        self.assertEqual((row["origin_x"], row["origin_y"]), (500000.0, 4500000.0))
        self.assertEqual((row["pixel_size_x"], row["pixel_size_y"]), (0.05, 0.05))
        self.assertEqual((row["rotation_x"], row["rotation_y"]), (0.0, 0.0))
        self.assertEqual((row["min_x"], row["min_y"], row["max_x"], row["max_y"]),
                         (500000.0, 4498650.0, 501900.0, 4500000.0))
        self.assertEqual(row["lon_min"], -105.0001)
        self.assertEqual(row["nodata"], "")
        self.assertEqual((row["block_width"], row["block_height"], row["is_tiled"]), (512, 512, 1))
        self.assertEqual(row["compression"], "deflate")
        self.assertEqual((row["overview_count"], row["overview_levels"]), (6, "2,4,8,16,32,64"))
        self.assertEqual(row["is_cog"], 1)
        self.assertEqual(row["color_interp"], "red,green,blue,alpha")
        self.assertEqual(row["error"], "")

    def test_nodata_variants(self):
        self.assertEqual(rm.normalize({"nodata": -9999.0})["nodata"], "-9999")
        self.assertEqual(rm.normalize({"nodata": 255})["nodata"], "255")
        self.assertEqual(rm.normalize({"nodata": 0.5})["nodata"], "0.5")
        self.assertEqual(rm.normalize({"nodata": "nan"})["nodata"], "nan")
        self.assertEqual(rm.normalize({"nodata": float("nan")})["nodata"], "nan")
        self.assertEqual(rm.normalize({"nodata": None})["nodata"], "")
        self.assertEqual(rm.normalize({})["nodata"], "")

    def test_missing_crs_keeps_transform(self):
        meta = {**SERVICE_META, "epsg": None, "crs_units": None, "is_georeferenced": False,
                "bounds_4326": None, "extent": None}
        row = rm.normalize(meta)
        self.assertEqual(row["epsg"], 0)
        self.assertEqual(row["is_georeferenced"], 0)
        self.assertEqual(row["has_transform"], 1)
        self.assertEqual(row["min_x"], 500000.0)
        self.assertEqual((row["lon_min"], row["lat_max"]), (0.0, 0.0))

    def test_wkt_only_crs(self):
        row = rm.normalize({**SERVICE_META, "epsg": None, "crs_wkt": "PROJCS[...]"})
        self.assertEqual(row["epsg"], 0)
        self.assertEqual(row["crs_wkt"], "PROJCS[...]")

    def test_ungeoreferenced_and_striped(self):
        meta = {**SERVICE_META, "epsg": None, "is_georeferenced": False, "geotransform": None,
                "pixel_size": None, "bounds": None, "bounds_4326": None, "is_tiled": False,
                "block_width": 38000, "block_height": 1, "compression": None, "overviews": [],
                "is_cog": False}
        row = rm.normalize(meta)
        self.assertEqual(row["has_transform"], 0)
        self.assertEqual((row["origin_x"], row["pixel_size_x"], row["min_x"]), (0.0, 0.0, 0.0))
        self.assertEqual((row["is_tiled"], row["block_width"]), (0, 38000))
        self.assertEqual((row["compression"], row["overview_count"], row["overview_levels"]), ("", 0, ""))
        self.assertEqual(row["is_cog"], 0)

    def test_empty_or_partial_dict_does_not_raise(self):
        row = rm.normalize({})
        self.assertEqual(row["width"], 0)
        self.assertEqual(row["has_transform"], 0)
        rm.normalize(None)
        rm.normalize({"geotransform": [1, 2, 3]})  # malformed -> no transform


class TestToPublic(unittest.TestCase):
    def test_roundtrip_reassembles_arrays(self):
        row = {**rm.normalize(SERVICE_META), "dataset": "orthophoto", "extracted_at": "2026-09-24 10:00:00"}
        pub = rm.to_public(row)
        self.assertEqual(pub["dataset"], "orthophoto")
        self.assertEqual(pub["geotransform"], SERVICE_META["geotransform"])
        self.assertEqual(pub["pixel_size"], SERVICE_META["pixel_size"])
        self.assertEqual(pub["bounds"], SERVICE_META["bounds"])
        self.assertEqual(pub["bounds_4326"], SERVICE_META["bounds_4326"])
        self.assertEqual(pub["block_size"], [512, 512])
        self.assertEqual(pub["overviews"], SERVICE_META["overviews"])
        self.assertEqual(pub["color_interp"], SERVICE_META["color_interp"])
        self.assertEqual(pub["epsg"], 32613)
        self.assertIsNone(pub["crs_wkt"])
        self.assertIsNone(pub["nodata"])
        self.assertTrue(pub["is_cog"] and pub["is_tiled"] and pub["is_georeferenced"])
        self.assertEqual(pub["file_size"], 3_221_225_472)
        self.assertIsNone(pub["error"])
        self.assertEqual(pub["extracted_at"], "2026-09-24 10:00:00")

    def test_nodata_is_json_safe(self):
        self.assertEqual(rm.to_public(rm.normalize(DEM_META))["nodata"], -9999.0)
        self.assertEqual(rm.to_public(rm.normalize({"nodata": "nan"}))["nodata"], "nan")
        self.assertEqual(rm.to_public(rm.normalize({"nodata": 255}))["nodata"], 255.0)
        self.assertIsNone(rm.to_public(rm.normalize({}))["nodata"])

    def test_unknowns_are_none_not_zero(self):
        pub = rm.to_public(rm.normalize({**SERVICE_META, "epsg": None, "is_georeferenced": False,
                                         "bounds_4326": None, "crs_units": None}))
        self.assertIsNone(pub["epsg"])
        self.assertIsNone(pub["crs_units"])
        self.assertIsNone(pub["bounds_4326"])
        self.assertIsNotNone(pub["bounds"])          # native bounds still known
        self.assertFalse(pub["is_georeferenced"])

        pub = rm.to_public(rm.normalize({**SERVICE_META, "geotransform": None, "pixel_size": None, "bounds": None}))
        self.assertIsNone(pub["geotransform"])
        self.assertIsNone(pub["pixel_size"])
        self.assertIsNone(pub["bounds"])

    def test_error_row(self):
        pub = rm.to_public({"dataset": "dsm", "error": "not a readable raster: TIFF header", "width": 0})
        self.assertEqual(pub["error"], "not a readable raster: TIFF header")
        self.assertIsNone(pub["width"])
        self.assertIsNone(pub["geotransform"])


# --- DB-backed ---------------------------------------------------------------

def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": "rm",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email)
    u.roles = []
    u.append("roles", {"role": "WebODM User"})
    u.save(ignore_permissions=True)
    return email


class _TaskFixture(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = _user("rm_owner@example.com")
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "RM Org"}).insert(ignore_permissions=True).name
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.user,
                        "organization": cls.org, "role": "Owner"}).insert(ignore_permissions=True)
        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.user)
        cls.project = frappe.get_doc({"doctype": "WebODM Project", "title": "RM Project"}).insert().name
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def setUp(self):
        frappe.local.webodm_org_cache = {}
        frappe.set_user(self.user)
        self.task = frappe.get_doc({"doctype": "WebODM Task", "project": self.project,
                                    "title": "RM Task", "status": "Running",
                                    "node_task_id": "U-RM"}).insert()
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")
        for f in frappe.get_all("File", filters={"attached_to_doctype": "WebODM Task",
                                                 "attached_to_name": self.task.name}, pluck="name"):
            frappe.delete_doc("File", f, ignore_permissions=True, force=True)
        frappe.delete_doc("WebODM Task", self.task.name, ignore_permissions=True, force=True,
                          ignore_missing=True)

    def _rows(self):
        return frappe.get_all(rm.DOCTYPE, filters={"parent": self.task.name, "parenttype": "WebODM Task"},
                              fields=["dataset", "width", "error", "idx", "parentfield"], order_by="idx")


class TestStore(_TaskFixture):
    def test_store_get_and_upsert(self):
        rm.store(self.task.name, "orthophoto", rm.normalize(SERVICE_META))
        rm.store(self.task.name, "dsm", rm.normalize(DEM_META))
        rows = self._rows()
        self.assertEqual([r.dataset for r in rows], ["orthophoto", "dsm"])
        self.assertTrue(all(r.parentfield == "raster_metadata" for r in rows))

        # Re-storing the same dataset replaces, never duplicates.
        rm.store(self.task.name, "orthophoto", rm.normalize({**SERVICE_META, "width": 1}))
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r.dataset: r.width for r in rows}, {"orthophoto": 1, "dsm": 38000})

        pub = rm.get(self.task.name, "dsm")
        self.assertEqual(pub["nodata"], -9999.0)
        self.assertEqual(pub["band_count"], 1)
        self.assertEqual(pub["file_size"], 900_000_000)
        self.assertIsNotNone(pub["extracted_at"])
        self.assertIsNone(rm.get(self.task.name, "dtm"))
        self.assertEqual(set(rm.get_all(self.task.name)), {"orthophoto", "dsm"})

    def test_rows_travel_with_the_task_document(self):
        rm.store(self.task.name, "orthophoto", rm.normalize(SERVICE_META))
        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(len(t.raster_metadata), 1)
        self.assertEqual(t.raster_metadata[0].dataset, "orthophoto")
        self.assertEqual(t.raster_metadata[0].epsg, 32613)
        self.assertEqual(t.raster_metadata[0].file_size, 3_221_225_472)
        # A normal parent save (e.g. from Desk) must not choke on the rows.
        t.save(ignore_permissions=True)
        self.assertEqual(len(frappe.get_doc("WebODM Task", self.task.name).raster_metadata), 1)

    def test_rejects_unknown_dataset(self):
        with self.assertRaises(ValueError):
            rm.store(self.task.name, "point_cloud", {})

    def test_rows_are_deleted_with_the_task(self):
        rm.store(self.task.name, "orthophoto", rm.normalize(SERVICE_META))
        frappe.delete_doc("WebODM Task", self.task.name, ignore_permissions=True, force=True)
        self.assertEqual(self._rows(), [])


class TestRecord(_TaskFixture):
    def test_uses_inline_metadata_without_calling_the_service(self):
        with patch.object(rm, "fetch_raster_metadata") as fetch:
            ok = rm.record(self.task, "orthophoto", "/abs/ortho.tif", metadata=SERVICE_META)
        self.assertTrue(ok)
        fetch.assert_not_called()
        self.assertEqual(rm.get(self.task.name, "orthophoto")["width"], 38000)

    def test_fetches_from_service_when_no_inline_metadata(self):
        with patch.object(rm, "fetch_raster_metadata", return_value=DEM_META) as fetch:
            ok = rm.record(self.task, "dsm", "/abs/dsm.tif")
        self.assertTrue(ok)
        fetch.assert_called_once_with("/abs/dsm.tif")
        self.assertEqual(rm.get(self.task.name, "dsm")["nodata"], -9999.0)

    def test_unreadable_raster_records_error_row_and_does_not_raise(self):
        with patch.object(rm, "fetch_raster_metadata",
                          side_effect=GeospatialError("raster metadata read failed: not a readable raster")), \
             patch("frappe.log_error") as log:
            ok = rm.record(self.task, "orthophoto", "/abs/ortho.tif")
        self.assertFalse(ok)
        log.assert_called_once()
        pub = rm.get(self.task.name, "orthophoto")
        self.assertIn("not a readable raster", pub["error"])
        self.assertIsNone(pub["width"])

    def test_service_down_records_error_and_does_not_raise(self):
        with patch.object(rm, "fetch_raster_metadata", side_effect=GeospatialUnavailable("connection refused")), \
             patch("frappe.log_error"):
            self.assertFalse(rm.record(self.task, "dtm", "/abs/dtm.tif"))
        self.assertIn("connection refused", rm.get(self.task.name, "dtm")["error"])

    def test_unexpected_exception_is_swallowed(self):
        with patch.object(rm, "fetch_raster_metadata", side_effect=RuntimeError("boom")), \
             patch("frappe.log_error") as log:
            self.assertFalse(rm.record(self.task, "orthophoto", "/abs/ortho.tif"))
        log.assert_called_once()
        self.assertIn("boom", rm.get(self.task.name, "orthophoto")["error"])

    def test_successful_refresh_clears_previous_error(self):
        rm.store_error(self.task.name, "orthophoto", "old failure")
        rm.record(self.task, "orthophoto", "/abs/ortho.tif", metadata=SERVICE_META)
        pub = rm.get(self.task.name, "orthophoto")
        self.assertIsNone(pub["error"])
        self.assertEqual(pub["width"], 38000)
        self.assertEqual(len(self._rows()), 1)


class _FakeClient:
    def __init__(self, zip_bytes):
        self.zip_bytes = zip_bytes

    def download_asset(self, task_id, asset, dest_path):
        with open(dest_path, "wb") as fh:
            fh.write(self.zip_bytes)
        return dest_path


def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


class TestDownloadAssetsRecordsMetadata(_TaskFixture):
    """_download_assets stores one metadata row per raster, whichever way the
    metadata arrives, and completes the task even when extraction fails."""

    ZIP = _zip({
        "odm_orthophoto/odm_orthophoto.tif": b"II*\x00ortho",
        "odm_dem/dsm.tif": b"II*\x00dsm",
        "odm_georeferencing/odm_georeferenced_model.laz": b"LASF",
    })

    def test_metadata_from_cogify_response(self):
        georef = {"extent": {"type": "Polygon", "coordinates": []}, "epsg": 32613, "wkt": None,
                  "metadata": SERVICE_META}
        with patch.object(task_runner, "_cogify_raster", return_value=georef), \
             patch.object(rm, "fetch_raster_metadata") as fetch:
            task_runner._download_assets(_FakeClient(self.ZIP), "U-RM", self.task)

        fetch.assert_not_called()
        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(t.status, "Completed")
        self.assertEqual(sorted(r.dataset for r in t.raster_metadata), ["dsm", "orthophoto"])
        self.assertEqual(rm.get(t.name, "orthophoto")["epsg"], 32613)

    def test_falls_back_to_metadata_endpoint_when_cogify_fails(self):
        with patch.object(task_runner, "_cogify_raster", return_value=None), \
             patch.object(rm, "fetch_raster_metadata", return_value=DEM_META) as fetch:
            task_runner._download_assets(_FakeClient(self.ZIP), "U-RM", self.task)

        self.assertEqual(fetch.call_count, 2)
        for call in fetch.call_args_list:
            self.assertTrue(os.path.isabs(call.args[0]))
            self.assertTrue(os.path.isfile(call.args[0]))
        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(t.status, "Completed")
        self.assertEqual(len(t.raster_metadata), 2)

    def test_metadata_failure_does_not_fail_the_task(self):
        with patch.object(task_runner, "_cogify_raster", return_value=None), \
             patch.object(rm, "fetch_raster_metadata", side_effect=GeospatialError("corrupt")), \
             patch("frappe.log_error"):
            task_runner._download_assets(_FakeClient(self.ZIP), "U-RM", self.task)

        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(t.status, "Completed")
        self.assertTrue(t.orthophoto and t.dsm)
        self.assertEqual({r.dataset: r.error for r in t.raster_metadata},
                         {"orthophoto": "corrupt", "dsm": "corrupt"})
