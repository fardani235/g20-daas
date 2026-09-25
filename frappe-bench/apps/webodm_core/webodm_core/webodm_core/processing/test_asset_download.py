"""End-to-end (minus the network) test of _download_assets: a real all.zip is
streamed to disk by a fake client, members are streamed into private Files,
task fields/extents are set, and nothing is left in the scratch dir."""
import io
import os
import zipfile
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.plugins.files import abs_path_for_file_url
from webodm_core.webodm_core.processing import task_runner
from webodm_core.webodm_core.processing.node_client import NodeODMError, NodeODMTransportError


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": "dl",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email); u.roles = []
    u.append("roles", {"role": "WebODM User"}); u.save(ignore_permissions=True)
    return email


class _FakeClient:
    """Stands in for NodeODMClient: 'downloads' by writing a prepared zip."""

    def __init__(self, zip_bytes: bytes | None):
        self.zip_bytes = zip_bytes

    def download_asset(self, task_id, asset, dest_path):
        if self.zip_bytes is None:
            raise NodeODMError("Invalid asset")
        if self.zip_bytes == b"TRANSPORT":
            raise NodeODMTransportError("connection reset")
        with open(dest_path, "wb") as fh:
            fh.write(self.zip_bytes)
        return dest_path


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


class TestDownloadAssets(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = _user("dl_owner@example.com")
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "DL Org"}).insert(ignore_permissions=True).name
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.user,
                        "organization": cls.org, "role": "Owner"}).insert(ignore_permissions=True)
        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.user)
        cls.project = frappe.get_doc({"doctype": "WebODM Project", "title": "DL Project"}).insert().name
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def setUp(self):
        frappe.local.webodm_org_cache = {}
        frappe.set_user(self.user)
        self.task = frappe.get_doc({"doctype": "WebODM Task", "project": self.project,
                                    "title": "DL Task", "status": "Running",
                                    "node_task_id": "U-DL"}).insert()
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")
        for f in frappe.get_all("File", filters={"attached_to_doctype": "WebODM Task",
                                                 "attached_to_name": self.task.name}, pluck="name"):
            frappe.delete_doc("File", f, ignore_permissions=True, force=True)
        frappe.delete_doc("WebODM Task", self.task.name, ignore_permissions=True, force=True)

    def _scratch_entries(self):
        root = frappe.get_site_path("private", "processing")
        if not os.path.isdir(root):
            return []
        return [d for d in os.listdir(root) if d.startswith(self.task.name)]

    def test_assets_are_streamed_into_private_files_and_fields_set(self):
        ortho = b"II*\x00" + os.urandom(2 * 1024 * 1024)  # > one chunk
        dsm = b"II*\x00dsm"
        laz = b"LASF" + os.urandom(512)
        glb = b"glTF" + os.urandom(128)
        z = _zip({
            "odm_orthophoto/odm_orthophoto.tif": ortho,
            "odm_dem/dsm.tif": dsm,
            "odm_georeferencing/odm_georeferenced_model.laz": laz,
            "odm_texturing/odm_textured_model_geo.glb": glb,
            "odm_report/report.pdf": b"%PDF ignored",
        })
        metadata = {
            "driver": "GTiff", "width": 4000, "height": 3000, "band_count": 4, "dtype": "uint8",
            "crs": {"epsg": 32633, "wkt": "PROJCS[...]", "units": "metre"}, "georeference": "full",
            "geotransform": [500000.0, 0.05, 0.0, 4500000.0, 0.0, -0.05], "pixel_size": [0.05, 0.05],
            "bounds": [500000.0, 4499850.0, 500200.0, 4500000.0],
            "bounds_4326": [15.0, 40.6, 15.01, 40.61], "nodata": None,
            "color_interpretation": ["red", "green", "blue", "alpha"], "is_tiled": True,
            "block_size": [256, 256], "compression": "deflate", "interleave": "pixel",
            "overviews": [2, 4, 8], "is_cog": True, "file_size": len(ortho), "software": "ODM 3.5.6",
        }
        georef = {"extent": {"type": "Polygon", "coordinates": []}, "epsg": 32633, "wkt": "PROJCS[...]",
                  "metadata": metadata}

        with patch.object(task_runner, "_cogify_raster", return_value=georef) as cog, \
             patch.object(task_runner.raster_metadata.geospatial, "raster_metadata") as meta_svc:
            task_runner._download_assets(_FakeClient(z), "U-DL", self.task)
        # Metadata came with the cogify response: no second round trip.
        meta_svc.assert_not_called()

        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(t.status, "Completed")
        for field, expected in (("orthophoto", ortho), ("dsm", dsm), ("point_cloud", laz), ("model", glb)):
            self.assertTrue(t.get(field), field)
            with open(abs_path_for_file_url(t.get(field)), "rb") as fh:
                self.assertEqual(fh.read(), expected, field)
        self.assertFalse(t.dtm)
        # cogify called once per raster present (orthophoto + dsm), not for laz/glb
        self.assertEqual(cog.call_count, 2)
        self.assertEqual(t.epsg, 32633)
        self.assertEqual(t.wkt, "PROJCS[...]")
        self.assertTrue(t.orthophoto_extent)
        self.assertTrue(t.dsm_extent)
        self.assertFalse(t.dtm_extent)
        # One normalized metadata row per raster present, none for laz/glb.
        rows = {r.dataset: r for r in t.raster_metadata}
        self.assertEqual(set(rows), {"orthophoto", "dsm"})
        self.assertEqual(rows["orthophoto"].status, "Extracted")
        self.assertEqual((rows["orthophoto"].width, rows["orthophoto"].height), (4000, 3000))
        self.assertEqual(rows["orthophoto"].epsg, 32633)
        self.assertEqual(rows["orthophoto"].file_url, t.orthophoto)
        self.assertEqual(rows["dsm"].file_url, t.dsm)
        self.assertEqual(rows["orthophoto"].overview_levels, "2,4,8")
        # Orthophoto GSD (0.05 m) -> task resolution 5 cm/px
        self.assertAlmostEqual(float(t.resolution), 5.0, places=2)
        # File docs carry real sizes/hashes and are attached to the task
        ortho_file = frappe.get_doc("File", {"file_url": t.orthophoto})
        self.assertEqual(ortho_file.file_size, len(ortho))
        self.assertEqual(ortho_file.attached_to_name, self.task.name)
        self.assertEqual(self._scratch_entries(), [], "scratch dir must be removed")

    def test_geospatial_down_still_completes_and_records_failed_metadata(self):
        from webodm_core.plugins.geospatial import GeospatialUnavailable
        z = _zip({"odm_orthophoto/odm_orthophoto.tif": b"II*\x00ortho"})
        with patch.object(task_runner, "_cogify_raster", return_value=None), \
             patch.object(task_runner.raster_metadata.geospatial, "raster_metadata",
                          side_effect=GeospatialUnavailable("connection refused")) as meta_svc, \
             patch("frappe.log_error"):
            task_runner._download_assets(_FakeClient(z), "U-DL", self.task)
        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(t.status, "Completed", "metadata failure must never fail the task")
        meta_svc.assert_called_once()  # cogify carried nothing -> separate fetch attempted
        self.assertEqual(len(t.raster_metadata), 1)
        self.assertEqual(t.raster_metadata[0].status, "Failed")
        self.assertIn("connection refused", t.raster_metadata[0].error)

    def test_gltf_fallback_bundles_texturing_dir_when_no_glb(self):
        z = _zip({
            "odm_orthophoto/odm_orthophoto.tif": b"II*\x00",
            "odm_texturing/odm_textured_model_geo.obj": b"o mesh",
            "odm_texturing/odm_textured_model_geo.mtl": b"newmtl x",
            "odm_texturing/tex.png": b"\x89PNG",
        })
        with patch.object(task_runner, "_cogify_raster", return_value=None), \
             patch.object(task_runner.raster_metadata, "capture"):
            task_runner._download_assets(_FakeClient(z), "U-DL", self.task)

        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(t.status, "Completed")
        self.assertTrue(t.model.endswith(".zip"), t.model)
        with zipfile.ZipFile(abs_path_for_file_url(t.model)) as mz:
            self.assertEqual(sorted(mz.namelist()),
                             ["odm_textured_model_geo.mtl", "odm_textured_model_geo.obj", "tex.png"])
            self.assertEqual(mz.read("tex.png"), b"\x89PNG")
        self.assertEqual(self._scratch_entries(), [])

    def test_node_refusing_download_marks_failed_and_cleans_up(self):
        task_runner._download_assets(_FakeClient(None), "U-DL", self.task)
        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(t.status, "Failed")
        self.assertIn("Invalid asset", t.last_error)
        self.assertFalse(t.orthophoto)
        self.assertEqual(self._scratch_entries(), [])

    def test_transport_failure_propagates_and_leaves_task_running(self):
        with self.assertRaises(NodeODMTransportError):
            task_runner._download_assets(_FakeClient(b"TRANSPORT"), "U-DL", self.task)
        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(t.status, "Running")
        self.assertEqual(self._scratch_entries(), [])

    def test_zip_without_known_assets_is_failed(self):
        z = _zip({"odm_report/report.pdf": b"%PDF"})
        with patch.object(task_runner, "_cogify_raster", return_value=None), \
             patch.object(task_runner.raster_metadata, "capture"):
            task_runner._download_assets(_FakeClient(z), "U-DL", self.task)
        self.assertEqual(frappe.get_doc("WebODM Task", self.task.name).status, "Failed")
        self.assertEqual(self._scratch_entries(), [])

    def test_corrupt_zip_is_failed_not_raised(self):
        with patch.object(task_runner, "_cogify_raster", return_value=None):
            task_runner._download_assets(_FakeClient(b"not a zip"), "U-DL", self.task)
        self.assertEqual(frappe.get_doc("WebODM Task", self.task.name).status, "Failed")
        self.assertEqual(self._scratch_entries(), [])


class TestGetTaskImages(FrappeTestCase):
    """_get_task_images must hand back paths, never file contents."""

    def test_returns_filename_path_pairs_for_existing_files_only(self):
        import io as _io
        from webodm_core.plugins.files import save_private_file_from_stream

        f = save_private_file_from_stream(
            _io.BytesIO(b"\xff\xd8img"), "gti.jpg",
            attached_to_doctype="WebODM Task", attached_to_name="GTI-TASK",
            ignore_permissions=True,
        )
        try:
            task = frappe._dict(name="GTI-TASK", images=[
                frappe._dict(image=f.file_url, filename="DJI_0001.JPG"),
                frappe._dict(image="", filename="skipped.jpg"),
                frappe._dict(image="/private/files/does_not_exist_zzz.jpg", filename="gone.jpg"),
            ])
            with patch("frappe.log_error"):
                out = task_runner._get_task_images(task)
            self.assertEqual(len(out), 1)
            name, path = out[0]
            self.assertEqual(name, "DJI_0001.JPG")
            self.assertTrue(os.path.isabs(path))
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), b"\xff\xd8img")
        finally:
            frappe.delete_doc("File", f.name, ignore_permissions=True, force=True)
