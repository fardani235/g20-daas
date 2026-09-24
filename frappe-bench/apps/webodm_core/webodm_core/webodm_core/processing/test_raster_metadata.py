"""Raster metadata normalization + persistence + API.

``TestNormalize`` / ``TestRowToDict`` are pure (no DB): the mapping between the
geospatial service document and the child-row columns is where a NaN nodata,
a missing CRS or a stale value would silently corrupt what plugins and the
viewer read. ``TestRecordAndApi`` runs against the site DB and drives the
upsert, the Failed path, the lazy (re-)extraction in the API and permissions.
"""
import json
import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.plugins import geospatial
from webodm_core.webodm_core.processing import raster_metadata as rm

# A realistic /raster/metadata document for an ODM orthophoto (COG).
ORTHO_META = {
    "path": "/x/ortho.tif", "file_size": 214119658, "driver": "GTiff",
    "width": 14718, "height": 11640, "band_count": 4, "dtype": "uint8",
    "dtypes": ["uint8"] * 4,
    "crs": {"epsg": 32632, "wkt": "PROJCRS[...]", "units": "metre",
            "is_geographic": False, "is_projected": True},
    "georeference": "full",
    "geotransform": [321875.3933733602, 0.049997992101743646, 0.0,
                     5158255.823290414, 0.0, -0.049996591718544175],
    "pixel_size": [0.049997992101743646, 0.049996591718544175],
    "bounds": [321875.3933733602, 5157673.86296281, 322611.2638211137, 5158255.823290414],
    "bounds_4326": [6.676264031910565, 46.54902764000605, 6.686078414560036, 46.554455207908234],
    "extent": {"type": "Polygon", "coordinates": [[[6.67, 46.54], [6.68, 46.54], [6.68, 46.55],
                                                    [6.67, 46.55], [6.67, 46.54]]]},
    "nodata": None,
    "bands": [{"index": i, "dtype": "uint8", "color_interpretation": c, "nodata": None,
               "overviews": [2, 4, 8, 16, 32, 64], "block_size": [256, 256]}
              for i, c in enumerate(["red", "green", "blue", "alpha"], 1)],
    "color_interpretation": ["red", "green", "blue", "alpha"],
    "has_colormap": False, "is_tiled": True, "block_size": [256, 256],
    "compression": "deflate", "interleave": "pixel", "predictor": "2",
    "overviews": [2, 4, 8, 16, 32, 64], "overview_count": 6,
    "software": "ODM 3.5.6", "area_or_point": "Area", "is_cog": True,
}

DSM_META = {
    **ORTHO_META, "band_count": 1, "dtype": "float32", "dtypes": ["float32"],
    "nodata": -9999.0, "color_interpretation": ["gray"], "interleave": "band",
    "bands": [{"index": 1, "dtype": "float32", "color_interpretation": "gray",
               "nodata": -9999.0, "overviews": [2, 4], "block_size": [256, 256]}],
    "overviews": [2, 4],
}


class TestNormalize(unittest.TestCase):
    def test_orthophoto_document_maps_to_typed_columns(self):
        v = rm.normalize(ORTHO_META)
        self.assertEqual((v["width"], v["height"], v["band_count"]), (14718, 11640, 4))
        self.assertEqual(v["dtype"], "uint8")
        self.assertEqual(v["driver"], "GTiff")
        self.assertEqual(v["epsg"], 32632)
        self.assertIsNone(v["crs_wkt"], "WKT is redundant when an EPSG code exists")
        self.assertEqual(v["crs_units"], "metre")
        self.assertEqual(v["georeference"], "full")
        self.assertAlmostEqual(v["pixel_size_x"], 0.049997992101743646)
        self.assertEqual(json.loads(v["geotransform"]), ORTHO_META["geotransform"])
        self.assertEqual((v["min_x"], v["max_y"]), (321875.3933733602, 5158255.823290414))
        self.assertAlmostEqual(v["west"], 6.676264031910565)
        self.assertAlmostEqual(v["north"], 46.554455207908234)
        self.assertEqual((v["is_tiled"], v["block_width"], v["block_height"]), (1, 256, 256))
        self.assertEqual(v["compression"], "deflate")
        self.assertEqual(v["interleave"], "pixel")
        self.assertEqual(v["overview_levels"], "2,4,8,16,32,64")
        self.assertEqual(v["overview_count"], 6)
        self.assertEqual(v["is_cog"], 1)
        self.assertEqual(v["color_interpretation"], "red,green,blue,alpha")
        self.assertEqual(v["has_colormap"], 0)
        self.assertEqual(v["file_size"], 214119658)
        self.assertEqual(v["software"], "ODM 3.5.6")
        # No nodata -> explicitly "none", not 0
        self.assertIsNone(v["nodata"])
        self.assertEqual(v["has_nodata"], 0)

    def test_dsm_nodata_is_kept(self):
        v = rm.normalize(DSM_META)
        self.assertEqual(v["nodata"], "-9999.0")
        self.assertEqual(v["has_nodata"], 1)
        self.assertEqual(v["dtype"], "float32")
        self.assertEqual(v["color_interpretation"], "gray")

    def test_nan_nodata_survives_as_text(self):
        v = rm.normalize({**DSM_META, "nodata": "nan"})
        self.assertEqual(v["nodata"], "nan")
        self.assertEqual(v["has_nodata"], 1)

    def test_missing_crs_keeps_native_bounds_only(self):
        meta = {**ORTHO_META, "crs": {"epsg": None, "wkt": None, "units": None,
                                      "is_geographic": False, "is_projected": False},
                "georeference": "no_crs", "bounds_4326": None, "extent": None}
        v = rm.normalize(meta)
        self.assertIsNone(v["epsg"])
        self.assertIsNone(v["crs_wkt"])
        self.assertEqual(v["georeference"], "no_crs")
        self.assertEqual(v["min_x"], 321875.3933733602)
        self.assertIsNone(v["west"])

    def test_wkt_only_crs_is_stored(self):
        meta = {**ORTHO_META, "crs": {**ORTHO_META["crs"], "epsg": None}}
        v = rm.normalize(meta)
        self.assertIsNone(v["epsg"])
        self.assertEqual(v["crs_wkt"], "PROJCRS[...]")

    def test_untiled_raster_without_overviews(self):
        meta = {**ORTHO_META, "is_tiled": False, "block_size": [14718, 1], "overviews": [],
                "compression": None, "is_cog": False}
        v = rm.normalize(meta)
        self.assertEqual(v["is_tiled"], 0)
        self.assertEqual((v["block_width"], v["block_height"]), (14718, 1))
        self.assertEqual(v["overview_count"], 0)
        self.assertIsNone(v["overview_levels"])
        self.assertIsNone(v["compression"])
        self.assertEqual(v["is_cog"], 0)

    def test_garbage_document_does_not_raise(self):
        v = rm.normalize({"width": "wide", "crs": "not a dict", "geotransform": [1, 2],
                          "bounds": "x", "overviews": ["a", 2]})
        self.assertIsNone(v["width"])
        self.assertIsNone(v["geotransform"])
        self.assertEqual(v["overview_levels"], "2")
        self.assertEqual(v["georeference"], "none")

    def test_every_value_column_is_present(self):
        self.assertEqual(set(rm.normalize({})), set(rm.blank_values()))


class TestRowToDict(unittest.TestCase):
    def _row(self, meta, **extra):
        values = rm.normalize(meta)
        values.update({"dataset": "orthophoto", "file_url": "/private/files/o.tif",
                       "status": "Extracted", "error": None, "extracted_at": None})
        values.update(extra)
        return frappe._dict(values)

    def test_round_trip_restores_typed_shape(self):
        d = rm.row_to_dict(self._row(ORTHO_META))
        self.assertEqual(d["crs"], {"epsg": 32632, "wkt": None, "units": "metre"})
        self.assertEqual(d["geotransform"], ORTHO_META["geotransform"])
        # Full precision comes back via the geotransform, not the decimal(21,9) columns.
        self.assertEqual(d["pixel_size"], ORTHO_META["pixel_size"])
        for got, want in zip(d["bounds"], ORTHO_META["bounds"], strict=True):
            self.assertAlmostEqual(got, want, places=6)
        self.assertEqual(d["bounds_4326"], ORTHO_META["bounds_4326"])
        self.assertEqual(d["block_size"], [256, 256])
        self.assertEqual(d["overviews"], [2, 4, 8, 16, 32, 64])
        self.assertEqual(d["color_interpretation"], ["red", "green", "blue", "alpha"])
        self.assertIsNone(d["nodata"])
        self.assertTrue(d["is_cog"] and d["is_tiled"])
        self.assertEqual(d["file_size"], 214119658)
        json.dumps(d)  # plugin context / API must be JSON-serialisable

    def test_nodata_variants(self):
        self.assertEqual(rm.row_to_dict(self._row(DSM_META))["nodata"], -9999.0)
        self.assertEqual(rm.row_to_dict(self._row({**DSM_META, "nodata": "nan"}))["nodata"], "nan")
        # Frappe stores 0 for an unset Check; that must read as "no nodata", not 0.0.
        self.assertIsNone(rm.row_to_dict(self._row(ORTHO_META, has_nodata=0, nodata=None))["nodata"])

    def test_no_crs_row_hides_zeroed_4326_bounds(self):
        # Float columns come back as 0.0 for unset values; georeference decides
        # whether they mean anything.
        meta = {**ORTHO_META, "crs": {"epsg": None, "wkt": None, "units": None},
                "georeference": "no_crs", "bounds_4326": None}
        d = rm.row_to_dict(self._row(meta, west=0.0, south=0.0, east=0.0, north=0.0, epsg=0))
        self.assertIsNone(d["bounds_4326"])
        self.assertEqual(d["crs"], {"epsg": None, "wkt": None, "units": None})
        for got, want in zip(d["bounds"], ORTHO_META["bounds"], strict=True):
            self.assertAlmostEqual(got, want, places=6)

    def test_bounds_fall_back_to_columns_without_geotransform(self):
        d = rm.row_to_dict(self._row(ORTHO_META, geotransform=None))
        self.assertAlmostEqual(d["pixel_size"][0], ORTHO_META["pixel_size"][0], places=8)
        self.assertAlmostEqual(d["bounds"][0], ORTHO_META["bounds"][0], places=6)

    def test_extent_comes_from_the_task(self):
        task = frappe._dict(orthophoto_extent=json.dumps(ORTHO_META["extent"]))
        d = rm.row_to_dict(self._row(ORTHO_META), task)
        self.assertEqual(d["extent"]["type"], "Polygon")

    def test_failed_row(self):
        row = frappe._dict(rm.blank_values(), dataset="dsm", file_url="/private/files/d.tif",
                           status="Failed", error="cannot open raster", has_nodata=0)
        d = rm.row_to_dict(row)
        self.assertEqual(d["status"], "Failed")
        self.assertEqual(d["error"], "cannot open raster")
        self.assertIsNone(d["width"])
        self.assertEqual(d["georeference"], "none")
        self.assertIsNone(d["bounds"])


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": "rm",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email); u.roles = []
    u.append("roles", {"role": "WebODM User"}); u.save(ignore_permissions=True)
    return email


class TestRecordAndApi(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = _user("rm_owner@example.com")
        cls.other = _user("rm_other@example.com")
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "RM Org"}).insert(ignore_permissions=True).name
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.user,
                        "organization": cls.org, "role": "Owner"}).insert(ignore_permissions=True)
        other_org = frappe.get_doc({"doctype": "WebODM Organization",
                                    "organization_name": "RM Other Org"}).insert(ignore_permissions=True).name
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.other,
                        "organization": other_org, "role": "Owner"}).insert(ignore_permissions=True)
        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.user)
        cls.project = frappe.get_doc({"doctype": "WebODM Project", "title": "RM Project"}).insert().name
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def setUp(self):
        frappe.local.webodm_org_cache = {}
        frappe.set_user(self.user)
        self.task = frappe.get_doc({"doctype": "WebODM Task", "project": self.project,
                                    "title": "RM Task", "status": "Completed",
                                    "orthophoto": "/private/files/rm_ortho.tif",
                                    "dsm": "/private/files/rm_dsm.tif"}).insert()
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.delete_doc("WebODM Task", self.task.name, ignore_permissions=True, force=True)

    def _rows(self):
        return frappe.get_all(rm.CHILD_DOCTYPE, filters={"parent": self.task.name}, fields=["*"])

    def test_record_inserts_then_updates_one_row_per_dataset(self):
        rm.record(self.task.name, "orthophoto", self.task.orthophoto, metadata=ORTHO_META)
        rm.record(self.task.name, "dsm", self.task.dsm, metadata=DSM_META)
        rm.record(self.task.name, "orthophoto", self.task.orthophoto,
                  metadata={**ORTHO_META, "width": 100})
        rows = {r.dataset: r for r in self._rows()}
        self.assertEqual(set(rows), {"orthophoto", "dsm"})
        self.assertEqual(rows["orthophoto"].width, 100, "second record must update, not duplicate")
        self.assertEqual(rows["orthophoto"].file_size, 214119658)
        self.assertEqual(rows["orthophoto"].status, "Extracted")
        self.assertEqual(rows["dsm"].nodata, "-9999.0")
        self.assertEqual(rows["dsm"].has_nodata, 1)
        self.assertTrue(rows["dsm"].extracted_at)

        # The task document carries the rows (get_task_progress -> task.as_dict()).
        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual({r.dataset for r in t.raster_metadata}, {"orthophoto", "dsm"})
        self.assertEqual(rm.rows_for_task(t)["orthophoto"]["block_size"], [256, 256])

    def test_failed_extraction_is_recorded_not_raised(self):
        with patch("frappe.log_error") as log:
            row = rm.record(self.task.name, "orthophoto", self.task.orthophoto,
                            error="cannot open raster: boom")
        self.assertEqual(row.status, "Failed")
        self.assertIn("boom", row.error)
        self.assertFalse(row.width)  # Frappe stores 0 for an unset Int
        self.assertEqual(row.georeference, "none")
        self.assertTrue(log.called)
        d = rm.row_to_dict(row)
        self.assertIsNone(d["width"])
        self.assertIsNone(d["bounds"])

    def test_failed_re_extraction_of_a_new_file_blanks_old_values(self):
        rm.record(self.task.name, "orthophoto", "/private/files/old.tif", metadata=ORTHO_META)
        with patch("frappe.log_error"):
            rm.record(self.task.name, "orthophoto", "/private/files/new.tif", error="gone")
        row = rm.find_row(self.task.name, "orthophoto")
        self.assertEqual(row.status, "Failed")
        self.assertEqual(row.file_url, "/private/files/new.tif")
        self.assertFalse(row.width, "values of a different file must not be presented as this file's")
        self.assertEqual(row.georeference, "none")

    def test_failed_re_extraction_of_same_file_keeps_values(self):
        rm.record(self.task.name, "orthophoto", self.task.orthophoto, metadata=ORTHO_META)
        with patch("frappe.log_error"):
            rm.record(self.task.name, "orthophoto", self.task.orthophoto, error="service down")
        row = rm.find_row(self.task.name, "orthophoto")
        self.assertEqual(row.status, "Failed")
        self.assertEqual(row.width, 14718)

    def test_capture_uses_service_and_sets_resolution(self):
        task = frappe.get_doc("WebODM Task", self.task.name)
        self.assertFalse(task.resolution)
        with patch.object(geospatial, "raster_metadata", return_value=ORTHO_META) as svc, \
             patch.object(rm, "abs_path_for_file_url", return_value="/abs/ortho.tif"):
            row = rm.capture(task, "orthophoto", task.orthophoto)
        svc.assert_called_once_with("/abs/ortho.tif")
        self.assertEqual(row.status, "Extracted")
        # 0.049998 m/px -> 5.0 cm/px
        self.assertAlmostEqual(frappe.db.get_value("WebODM Task", task.name, "resolution"), 5.0, places=2)

    def test_capture_with_inline_metadata_skips_the_service(self):
        task = frappe.get_doc("WebODM Task", self.task.name)
        with patch.object(geospatial, "raster_metadata") as svc:
            row = rm.capture(task, "dsm", task.dsm, metadata=DSM_META)
        svc.assert_not_called()
        self.assertEqual(row.dtype, "float32")
        # A DSM never sets the orthophoto resolution.
        self.assertFalse(frappe.db.get_value("WebODM Task", task.name, "resolution"))

    def test_capture_service_down_records_failed_row(self):
        task = frappe.get_doc("WebODM Task", self.task.name)
        with patch.object(geospatial, "raster_metadata",
                          side_effect=geospatial.GeospatialUnavailable("connection refused")), \
             patch.object(rm, "abs_path_for_file_url", return_value="/abs/ortho.tif"), \
             patch("frappe.log_error"):
            row = rm.capture(task, "orthophoto", task.orthophoto)
        self.assertEqual(row.status, "Failed")
        self.assertIn("connection refused", row.error)

    def test_capture_corrupt_raster_records_failed_row(self):
        task = frappe.get_doc("WebODM Task", self.task.name)
        with patch.object(geospatial, "raster_metadata",
                          side_effect=geospatial.GeospatialError("raster metadata failed: cannot open raster")
                          ), \
             patch.object(rm, "abs_path_for_file_url", return_value="/abs/ortho.tif"), \
             patch("frappe.log_error"):
            row = rm.capture(task, "orthophoto", task.orthophoto)
        self.assertEqual(row.status, "Failed")
        self.assertIn("cannot open raster", row.error)

    # --- API -------------------------------------------------------------

    def _api(self, **kw):
        from webodm_core.api import task as task_api
        frappe.local.request = None
        frappe.local.form_dict = frappe._dict()
        return task_api.get_raster_metadata(task_name=self.task.name, **kw)

    def test_api_returns_stored_rows_without_calling_the_service(self):
        rm.record(self.task.name, "orthophoto", self.task.orthophoto, metadata=ORTHO_META)
        rm.record(self.task.name, "dsm", self.task.dsm, metadata=DSM_META)
        frappe.set_user(self.user)
        with patch.object(geospatial, "raster_metadata") as svc:
            out = self._api()
        svc.assert_not_called()
        self.assertEqual(out["orthophoto"]["width"], 14718)
        self.assertEqual(out["orthophoto"]["crs"]["epsg"], 32632)
        self.assertEqual(out["dsm"]["nodata"], -9999.0)
        self.assertIsNone(out["dtm"], "task has no DTM")

    def test_api_single_dataset_lazily_extracts_a_missing_row(self):
        frappe.set_user(self.user)
        with patch.object(geospatial, "raster_metadata", return_value=ORTHO_META) as svc, \
             patch.object(rm, "abs_path_for_file_url", return_value="/abs/ortho.tif"):
            out = self._api(dataset="orthophoto")
        svc.assert_called_once()
        self.assertEqual(out["dataset"], "orthophoto")
        self.assertEqual(out["pixel_size"][0], ORTHO_META["pixel_size"][0])
        self.assertEqual(len(self._rows()), 1)

    def test_api_re_extracts_when_the_task_file_changed(self):
        rm.record(self.task.name, "orthophoto", "/private/files/previous.tif", metadata=ORTHO_META)
        frappe.set_user(self.user)
        with patch.object(geospatial, "raster_metadata", return_value={**ORTHO_META, "width": 7}) as svc, \
             patch.object(rm, "abs_path_for_file_url", return_value="/abs/ortho.tif"):
            out = self._api(dataset="orthophoto")
        svc.assert_called_once()
        self.assertEqual(out["width"], 7)
        self.assertEqual(out["file_url"], self.task.orthophoto)

    def test_api_does_not_retry_failed_rows_unless_refresh(self):
        with patch("frappe.log_error"):
            rm.record(self.task.name, "orthophoto", self.task.orthophoto, error="down")
        frappe.set_user(self.user)
        with patch.object(geospatial, "raster_metadata", return_value=ORTHO_META) as svc:
            out = self._api(dataset="orthophoto")
        svc.assert_not_called()
        self.assertEqual(out["status"], "Failed")

        with patch.object(geospatial, "raster_metadata", return_value=ORTHO_META) as svc, \
             patch.object(rm, "abs_path_for_file_url", return_value="/abs/ortho.tif"):
            out = self._api(dataset="orthophoto", refresh=1)
        svc.assert_called_once()
        self.assertEqual(out["status"], "Extracted")

    def test_api_service_failure_yields_failed_dict_not_error(self):
        frappe.set_user(self.user)
        with patch.object(geospatial, "raster_metadata",
                          side_effect=geospatial.GeospatialUnavailable("refused")), \
             patch.object(rm, "abs_path_for_file_url", return_value="/abs/ortho.tif"), \
             patch("frappe.log_error"):
            out = self._api(dataset="orthophoto")
        self.assertEqual(out["status"], "Failed")
        self.assertIn("refused", out["error"])

    def test_api_rejects_unknown_dataset(self):
        frappe.set_user(self.user)
        with self.assertRaises(frappe.ValidationError):
            self._api(dataset="point_cloud")

    def test_api_denies_other_org(self):
        rm.record(self.task.name, "orthophoto", self.task.orthophoto, metadata=ORTHO_META)
        frappe.local.webodm_org_cache = {}
        frappe.set_user(self.other)
        with self.assertRaises(frappe.PermissionError):
            self._api()

    def test_plugin_context_carries_rasters(self):
        from webodm_core.plugins.runner import task_context
        rm.record(self.task.name, "orthophoto", self.task.orthophoto, metadata=ORTHO_META)
        ctx = task_context(frappe.get_doc("WebODM Task", self.task.name))
        rasters = ctx["task"]["rasters"]
        self.assertEqual(set(rasters), {"orthophoto"})
        self.assertEqual(rasters["orthophoto"]["crs"]["epsg"], 32632)
        self.assertEqual(rasters["orthophoto"]["overviews"], [2, 4, 8, 16, 32, 64])
        json.dumps(ctx)


if __name__ == "__main__":
    unittest.main()
