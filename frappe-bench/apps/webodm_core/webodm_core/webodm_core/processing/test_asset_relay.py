"""Object storage as the system of record, end to end minus the network:

* outputs relayed node -> S3 (all.zip streamed, members unpacked through range
  reads), COG-ified S3 -> S3, written through to the serving cache, recorded
  with org-namespaced keys; resumable after a transient failure;
* inputs synced to S3 and streamed to the node from S3 once the cache is gone;
* the serving cache: fill on miss, eviction rules, transparent private-file fill.

Uses the in-memory ``FakeObjectStorage`` so no S3 is needed."""

import io
import os
import time
import zipfile
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core import storage
from webodm_core.plugins import geospatial
from webodm_core.plugins.files import abs_path_for_file_url, save_private_file_from_stream
from webodm_core.storage import assets, cache, serving
from webodm_core.storage.testing import use_fake_storage
from webodm_core.webodm_core.processing import compute, task_runner
from webodm_core.webodm_core.processing.node_client import NodeODMError, NodeODMTransportError
from webodm_core.webodm_core.processing.testing import patch_local
from webodm_core.testing import make_dataset


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": "relay",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email); u.roles = []
    u.append("roles", {"role": "WebODM User"}); u.save(ignore_permissions=True)
    return email


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


class _FakeClient:
    """NodeODMClient stand-in: streams a prepared zip, or fails on request."""

    def __init__(self, zip_bytes):
        self.zip_bytes = zip_bytes
        self.downloads = 0

    def stream_asset(self, task_id, asset, sink):
        self.downloads += 1
        if self.zip_bytes is None:
            raise NodeODMError("Invalid asset")
        if self.zip_bytes == b"TRANSPORT":
            raise NodeODMTransportError("connection reset")
        return sink(io.BytesIO(self.zip_bytes))


METADATA = {
    "driver": "GTiff", "width": 4000, "height": 3000, "band_count": 4, "dtype": "uint8",
    "crs": {"epsg": 32633, "wkt": "PROJCS[...]", "units": "metre"}, "georeference": "full",
    "geotransform": [500000.0, 0.05, 0.0, 4500000.0, 0.0, -0.05], "pixel_size": [0.05, 0.05],
    "bounds": [500000.0, 4499850.0, 500200.0, 4500000.0], "bounds_4326": [15.0, 40.6, 15.01, 40.61],
    "nodata": None, "color_interpretation": ["red", "green", "blue", "alpha"], "is_tiled": True,
    "block_size": [256, 256], "compression": "deflate", "interleave": "pixel", "overviews": [2, 4, 8],
    "is_cog": True, "file_size": 123, "software": "ODM 3.5.6",
}


def _fake_cogify(store):
    """Stand in for the geospatial service: 'convert' by copying raw -> assets in the fake bucket."""

    def cogify(path, output_path=None, timeout=900):
        src = storage.key_from_uri(path)
        dst = storage.key_from_uri(output_path)
        assert src and dst, (path, output_path)
        assert src.split("/")[-2] == "raw" and dst.split("/")[-2] == "assets"
        store.objects[dst] = b"COG:" + store.objects[src]
        store.content_types[dst] = "image/tiff"
        return {"path": output_path, "epsg": 32633, "wkt": "PROJCS[...]",
                "extent": {"type": "Polygon", "coordinates": []}, "metadata": {**METADATA, "path": output_path}}

    return cogify


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = _user("relay_owner@example.com")
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "Relay Org"}).insert(ignore_permissions=True).name
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.user,
                        "organization": cls.org, "role": "Owner"}).insert(ignore_permissions=True)
        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.user)
        cls.project = frappe.get_doc({"doctype": "WebODM Project", "title": "Relay Project"}).insert().name
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def setUp(self):
        frappe.local.webodm_org_cache = {}
        frappe.set_user(self.user)
        # Inputs live on the dataset; tests that need specific images build
        # their own dataset with _dataset() and point the task at it.
        self.dataset = make_dataset("Relay inputs")
        self.task = frappe.get_doc({"doctype": "WebODM Task", "dataset": self.dataset.name, "project": self.project,
                                    "title": "Relay Task", "status": "Running",
                                    "node_task_id": "U-RELAY"}).insert()
        frappe.set_user("Administrator")
        patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        frappe.set_user("Administrator")
        for f in frappe.get_all("File", filters={"attached_to_doctype": "WebODM Task",
                                                 "attached_to_name": self.task.name}, pluck="name"):
            frappe.delete_doc("File", f, ignore_permissions=True, force=True)
        frappe.delete_doc("WebODM Task", self.task.name, ignore_permissions=True, force=True)
        for ds in frappe.get_all("WebODM Dataset", filters={"organization": self.org}, pluck="name"):
            if not frappe.db.exists("WebODM Task", {"dataset": ds}):
                frappe.delete_doc("WebODM Dataset", ds, ignore_permissions=True, force=True)

    def _prefix(self):
        return f"orgs/relay-org/tasks/{self.task.name}/"

    def _dataset(self, images):
        """A dataset with the given ``(filename, bytes)`` images, linked to self.task."""
        frappe.set_user(self.user)
        ds = make_dataset("Relay inputs", images=images)
        frappe.set_user("Administrator")
        self.task.db_set("dataset", ds.name)
        self.task.reload()
        return ds

    def _dataset_prefix(self, ds):
        return f"orgs/relay-org/datasets/{ds.name}/"


class TestRelayOutputs(_Base):
    def test_outputs_land_in_s3_as_cogs_and_in_the_cache(self):
        ortho = b"II*\x00" + os.urandom(2 * 1024 * 1024)
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
        with use_fake_storage() as store, \
                patch.object(geospatial, "cogify", side_effect=_fake_cogify(store)) as cog, \
                patch.object(task_runner.raster_metadata.geospatial, "raster_metadata") as meta_svc, \
                patch.object(compute, "release_for_task") as release:
            task_runner._download_assets(_FakeClient(z), "U-RELAY", self.task)

        p = self._prefix()
        # canonical objects, org-namespaced; rasters are the converted copies
        self.assertEqual(store.objects[p + "assets/orthophoto.tif"], b"COG:" + ortho)
        self.assertEqual(store.objects[p + "assets/dsm.tif"], b"COG:" + dsm)
        self.assertEqual(store.objects[p + "assets/georeferenced_model.laz"], laz)
        self.assertEqual(store.objects[p + "assets/model.glb"], glb)
        # transient objects are gone
        self.assertFalse([k for k in store.objects if "/raw/" in k], store.objects.keys())
        self.assertEqual(cog.call_count, 2)
        meta_svc.assert_not_called()  # metadata came from the cogify response (read off S3)

        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(t.status, "Completed")
        release.assert_called_once()
        self.assertEqual(t.epsg, 32633)
        self.assertTrue(t.orthophoto_extent and t.dsm_extent and not t.dtm_extent)
        rows = {r.kind: r for r in t.assets}
        self.assertEqual(set(rows), {"orthophoto", "dsm", "point_cloud", "model"})
        self.assertEqual(rows["orthophoto"].storage_key, p + "assets/orthophoto.tif")
        self.assertTrue(rows["orthophoto"].is_cog and rows["dsm"].is_cog)
        self.assertFalse(rows["model"].is_cog)
        self.assertEqual(rows["point_cloud"].file_size, len(laz))
        self.assertEqual(rows["model"].file_url, t.model)
        # write-through cache: the private files hold the same bytes as S3
        for kind in ("orthophoto", "dsm", "point_cloud", "model"):
            with open(abs_path_for_file_url(t.get(kind)), "rb") as fh:
                self.assertEqual(fh.read(), store.objects[rows[kind].storage_key], kind)
        self.assertEqual({r.dataset for r in t.raster_metadata}, {"orthophoto", "dsm"})
        self.assertEqual(t.raster_metadata[0].status, "Extracted")

    def test_relay_is_resumable_after_conversion_outage(self):
        z = _zip({"odm_orthophoto/odm_orthophoto.tif": b"II*\x00ortho",
                  "odm_georeferencing/odm_georeferenced_model.laz": b"LASF"})
        client = _FakeClient(z)
        with use_fake_storage() as store:
            # 1st poll: storage fine, geospatial down -> transient, task stays Running
            with patch.object(geospatial, "cogify", side_effect=geospatial.GeospatialUnavailable("refused")), \
                    patch.object(compute, "release_for_task") as release:
                with self.assertRaises(task_runner.AssetRelayRetry):
                    task_runner._download_assets(client, "U-RELAY", self.task)
            release.assert_not_called()
            t = frappe.get_doc("WebODM Task", self.task.name)
            self.assertEqual(t.status, "Running")
            p = self._prefix()
            self.assertIn(p + "raw/all.zip", store.objects)       # kept for the retry
            self.assertIn(p + "raw/orthophoto.tif", store.objects)  # member already relayed
            self.assertNotIn(p + "assets/orthophoto.tif", store.objects)

            # 2nd poll: resumes; all.zip is not fetched from the node again
            t = frappe.get_doc("WebODM Task", self.task.name)
            with patch.object(geospatial, "cogify", side_effect=_fake_cogify(store)) as cog, \
                    patch.object(task_runner.raster_metadata, "capture"), \
                    patch.object(compute, "release_for_task"):
                task_runner._download_assets(client, "U-RELAY", t)
            self.assertEqual(client.downloads, 1)
            self.assertEqual(cog.call_count, 1)
            t = frappe.get_doc("WebODM Task", self.task.name)
            self.assertEqual(t.status, "Completed")
            self.assertNotIn(p + "raw/all.zip", store.objects)

    def test_conversion_gives_up_gracefully_at_the_poll_budget(self):
        z = _zip({"odm_orthophoto/odm_orthophoto.tif": b"II*\x00raw-bytes"})
        self.task.db_set("poll_failures", task_runner.MAX_POLL_FAILURES - 1)
        with use_fake_storage() as store, \
                patch.object(geospatial, "cogify", side_effect=geospatial.GeospatialError("corrupt")), \
                patch.object(task_runner.raster_metadata, "capture"), \
                patch.object(compute, "release_for_task"):
            task_runner._download_assets(_FakeClient(z), "U-RELAY", self.task)
        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(t.status, "Completed", "the raw raster is kept rather than losing the output")
        row = {r.kind: r for r in t.assets}["orthophoto"]
        self.assertFalse(row.is_cog)
        self.assertEqual(store.objects[row.storage_key], b"II*\x00raw-bytes")

    def test_storage_outage_during_relay_is_transient(self):
        z = _zip({"odm_orthophoto/odm_orthophoto.tif": b"II*\x00"})
        with use_fake_storage() as store, patch.object(compute, "release_for_task"):
            store.fail_next = storage.StorageUnavailable("upload failed: storage unreachable")
            with self.assertRaises(task_runner.AssetRelayRetry):
                task_runner._download_assets(_FakeClient(z), "U-RELAY", self.task)
        self.assertEqual(frappe.get_doc("WebODM Task", self.task.name).status, "Running")

    def test_node_transport_error_propagates(self):
        with use_fake_storage(), patch.object(compute, "release_for_task"):
            with self.assertRaises(NodeODMTransportError):
                task_runner._download_assets(_FakeClient(b"TRANSPORT"), "U-RELAY", self.task)
        self.assertEqual(frappe.get_doc("WebODM Task", self.task.name).status, "Running")

    def test_node_refusal_and_empty_zip_are_terminal(self):
        with use_fake_storage(), patch.object(compute, "release_for_task") as release:
            task_runner._download_assets(_FakeClient(None), "U-RELAY", self.task)
        t = frappe.get_doc("WebODM Task", self.task.name)
        self.assertEqual(t.status, "Failed")
        release.assert_called_once()

        self.task.db_set({"status": "Running"})
        z = _zip({"odm_report/report.pdf": b"%PDF"})
        with use_fake_storage(), patch.object(compute, "release_for_task"):
            task_runner._download_assets(_FakeClient(z), "U-RELAY", self.task)
        self.assertEqual(frappe.get_doc("WebODM Task", self.task.name).status, "Failed")


class TestInputs(_Base):
    def test_sync_then_stream_from_s3_after_eviction(self):
        ds = self._dataset([("DJI_0001.JPG", b"\xff\xd8img1"), ("DJI_0002.JPG", b"\xff\xd8img2")])
        with use_fake_storage() as store:
            self.assertEqual(assets.sync_inputs(ds), 2)
            self.assertEqual(assets.sync_inputs(ds), 0)  # idempotent
            self.assertEqual(assets.sync_task_inputs(self.task), 0)  # the task path resolves the same dataset
            d = frappe.get_doc("WebODM Dataset", ds.name)
            keys = {r.filename: r.storage_key for r in d.images}
            # New uploads are namespaced by org and scoped to the dataset, not a task.
            self.assertTrue(all(k.startswith(self._dataset_prefix(ds) + "inputs/") for k in keys.values()), keys)
            self.assertEqual(store.objects[keys["DJI_0001.JPG"]], b"\xff\xd8img1")

            # warm: paths
            t = frappe.get_doc("WebODM Task", self.task.name)
            sources = dict(task_runner._get_task_images(t))
            self.assertTrue(os.path.isabs(sources["DJI_0001.JPG"]))

            # evict one blob: it streams from S3 instead
            os.remove(abs_path_for_file_url(d.images[0].image))
            sources = dict(task_runner._get_task_images(t))
            self.assertTrue(os.path.isabs(sources["DJI_0002.JPG"]))
            with sources["DJI_0001.JPG"] as fh:
                self.assertEqual(fh.read(), b"\xff\xd8img1")

    def test_a_second_task_reads_the_same_dataset_without_reupload(self):
        ds = self._dataset([("DJI_0001.JPG", b"\xff\xd8img1")])
        frappe.set_user(self.user)
        other = frappe.get_doc({"doctype": "WebODM Task", "dataset": ds.name, "project": self.project,
                                "title": "Second Task", "status": "Pending"}).insert()
        frappe.set_user("Administrator")
        try:
            with use_fake_storage():
                assets.sync_task_inputs(self.task)
                a = dict(task_runner._get_task_images(frappe.get_doc("WebODM Task", self.task.name)))
                b = dict(task_runner._get_task_images(frappe.get_doc("WebODM Task", other.name)))
            self.assertEqual(a, b)
            self.assertEqual(frappe.db.count("WebODM Dataset Image", {"parent": ds.name}), 1)
        finally:
            frappe.delete_doc("WebODM Task", other.name, ignore_permissions=True, force=True)

    def test_org_boundary_is_enforced_on_input_keys(self):
        ds = self._dataset([("DJI_0001.JPG", b"\xff\xd8img1")])
        with use_fake_storage() as store:
            assets.sync_inputs(ds)
            d = frappe.get_doc("WebODM Dataset", ds.name)
            # tamper with the row: a key from another org's namespace
            frappe.db.set_value("WebODM Dataset Image", d.images[0].name, "storage_key",
                                "orgs/someone-else/tasks/x/inputs/DJI_0001.JPG")
            store.objects["orgs/someone-else/tasks/x/inputs/DJI_0001.JPG"] = b"theirs"
            os.remove(abs_path_for_file_url(d.images[0].image))
            t = frappe.get_doc("WebODM Task", self.task.name)
            with self.assertRaises(storage.OrgBoundaryError):
                task_runner._get_task_images(t)

    def test_migrated_key_under_the_task_prefix_is_accepted(self):
        # A dataset split out of a pre-library task keeps tasks/<task>/inputs/ keys;
        # the org namespace is what matters, so dispatch reads them unchanged.
        ds = self._dataset([("DJI_0001.JPG", b"\xff\xd8img1")])
        with use_fake_storage() as store:
            legacy_key = self._prefix() + "inputs/DJI_0001.JPG"
            store.objects[legacy_key] = b"\xff\xd8legacy"
            d = frappe.get_doc("WebODM Dataset", ds.name)
            frappe.db.set_value("WebODM Dataset Image", d.images[0].name, "storage_key", legacy_key)
            os.remove(abs_path_for_file_url(d.images[0].image))
            self.assertEqual(assets.sync_inputs(frappe.get_doc("WebODM Dataset", ds.name)), 0)  # key kept
            sources = dict(task_runner._get_task_images(frappe.get_doc("WebODM Task", self.task.name)))
            with sources["DJI_0001.JPG"] as fh:
                self.assertEqual(fh.read(), b"\xff\xd8legacy")

    def test_storage_outage_does_not_block_sync_caller(self):
        ds = self._dataset([("DJI_0001.JPG", b"\xff\xd8img1")])
        with use_fake_storage() as store:
            store.fail_next = storage.StorageUnavailable("upload failed: storage unreachable")
            self.assertEqual(assets.sync_inputs(ds), 0)
            with self.assertRaises(storage.StorageUnavailable):
                store.fail_next = storage.StorageUnavailable("again")
                assets.sync_inputs(ds, raise_on_error=True)


class TestServingCache(_Base):
    def _asset(self, kind="orthophoto", data=b"II*\x00cog"):
        f = save_private_file_from_stream(io.BytesIO(data), f"{self.task.name}_{kind}.tif",
                                          attached_to_doctype="WebODM Task", attached_to_name=self.task.name,
                                          ignore_permissions=True)
        self.task.db_set(kind, f.file_url)
        return f

    def test_resolve_source_and_ensure_local(self):
        f = self._asset()
        with use_fake_storage() as store:
            key = storage.task_key(self.task, "assets", "orthophoto.tif")
            store.objects[key] = b"II*\x00cog"
            assets.record_asset(self.task, "orthophoto", filename="orthophoto.tif", file_url=f.file_url,
                                storage_key=key, size=8, is_cog=True)
            t = frappe.get_doc("WebODM Task", self.task.name)
            self.assertEqual(assets.raster_source(t, "orthophoto"), ("local", abs_path_for_file_url(f.file_url)))

            os.remove(abs_path_for_file_url(f.file_url))
            with patch.object(cache, "enqueue_fill") as fill:
                kind, uri = assets.raster_source(t, "orthophoto")
            self.assertEqual((kind, uri), ("s3", f"s3://fake-bucket/{key}"))
            fill.assert_called_once_with(f.file_url)

            path = assets.ensure_asset_local(t, "orthophoto")
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), b"II*\x00cog")
            self.assertEqual(assets.raster_source(t, "orthophoto")[0], "local")

    def test_miss_without_key_is_a_cache_miss(self):
        f = self._asset()
        os.remove(abs_path_for_file_url(f.file_url))
        t = frappe.get_doc("WebODM Task", self.task.name)
        with use_fake_storage():
            with self.assertRaises(cache.CacheMiss):
                assets.ensure_asset_local(t, "orthophoto")
        with self.assertRaises(cache.CacheMiss):
            assets.ensure_asset_local(t, "orthophoto")  # storage not configured either

    def test_raster_source_rejects_a_file_not_attached_to_the_task(self):
        # The Attach field is user-writable: pointing it at another record's
        # private blob must not resolve, or a member could read files they do
        # not own by URL.
        frappe.set_user(self.user)
        frappe.local.webodm_org_cache = {}
        other = frappe.get_doc({"doctype": "WebODM Task", "dataset": make_dataset().name, "project": self.project,
                                "title": "Other Task", "status": "Pending"}).insert()
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        f = save_private_file_from_stream(io.BytesIO(b"x"), "other.tif",
                                          attached_to_doctype="WebODM Task", attached_to_name=other.name,
                                          ignore_permissions=True)
        self.task.db_set("orthophoto", f.file_url)
        t = frappe.get_doc("WebODM Task", self.task.name)
        with self.assertRaises(frappe.PermissionError):
            assets.raster_source(t, "orthophoto")
        with self.assertRaises(frappe.PermissionError):
            assets.ensure_asset_local(t, "orthophoto")

    def test_eviction_rules(self):
        f = self._asset(data=b"x" * 1000)
        path = abs_path_for_file_url(f.file_url)
        with use_fake_storage() as store:
            key = storage.task_key(self.task, "assets", "orthophoto.tif")
            store.objects[key] = b"x" * 1000
            assets.record_asset(self.task, "orthophoto", filename="orthophoto.tif", file_url=f.file_url,
                                storage_key=key, size=1000)
            old = time.time() - 10 * 24 * 3600
            os.utime(path, (old, old))

            # busy task: never evicted
            stats = cache.evict(max_bytes=0, idle_seconds=1)
            self.assertTrue(os.path.exists(path))
            self.assertEqual(stats["evicted"], 0)

            self.task.db_set("status", "Completed")
            # idle rule
            stats = cache.evict(max_bytes=10**12, idle_seconds=7 * 24 * 3600)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(stats["evicted"], 1)

            # size rule (LRU), fresh file
            store.get_to_path(key, path)
            stats = cache.evict(max_bytes=10, idle_seconds=10**9)
            self.assertFalse(os.path.exists(path))

            # data is not lost: S3 is authoritative
            t = frappe.get_doc("WebODM Task", self.task.name)
            self.assertEqual(open(assets.ensure_asset_local(t, "orthophoto"), "rb").read(), b"x" * 1000)

    def test_eviction_never_touches_host_only_blobs(self):
        f = self._asset()
        path = abs_path_for_file_url(f.file_url)
        self.task.db_set("status", "Completed")
        old = time.time() - 10 ** 6
        os.utime(path, (old, old))
        with use_fake_storage():
            cache.evict(max_bytes=0, idle_seconds=1)
        self.assertTrue(os.path.exists(path), "no storage_key -> not a cache entry")

    def test_private_file_request_refills_from_storage(self):
        f = self._asset(kind="model", data=b"glTF-bytes")
        path = abs_path_for_file_url(f.file_url)
        with use_fake_storage() as store:
            key = storage.task_key(self.task, "assets", "model.glb")
            store.objects[key] = b"glTF-bytes"
            assets.record_asset(self.task, "model", filename="model.glb", file_url=f.file_url, storage_key=key)
            os.remove(path)

            request = MagicMock(); request.path = f.file_url
            frappe.set_user(self.user)
            with patch_local("request", request):
                serving.materialize_private_file()
            self.assertTrue(os.path.exists(path))
            self.assertEqual(open(path, "rb").read(), b"glTF-bytes")

            # guests never trigger a fill
            os.remove(path)
            frappe.set_user("Guest")
            with patch_local("request", request):
                serving.materialize_private_file()
            self.assertFalse(os.path.exists(path))

            # ...and neither does a logged-in user who cannot read the file
            # (path is already gone: the guest step never refilled it)
            outsider = _user("relay_outsider@example.com")
            frappe.set_user(outsider)
            frappe.local.webodm_org_cache = {}
            with patch_local("request", request):
                serving.materialize_private_file()
            self.assertFalse(os.path.exists(path), "unauthorized user must not fill the cache")
        frappe.set_user("Administrator")


class TestTaskDeletion(_Base):
    def test_deleting_a_task_removes_only_its_outputs_and_releases_compute(self):
        ds = self._dataset([("a.jpg", b"\xff\xd8a")])
        with use_fake_storage() as store, patch.object(compute, "release_for_task") as release:
            p = self._prefix()
            store.objects[p + "assets/orthophoto.tif"] = b"x"
            store.objects[p + "raw/all.zip"] = b"z"
            # A migrated dataset keeps its images under the *task* prefix — they
            # are the dataset's, not the task's, and must survive the delete.
            legacy_input = p + "inputs/a.jpg"
            store.objects[legacy_input] = b"y"
            frappe.db.set_value("WebODM Dataset Image", frappe.get_doc("WebODM Dataset", ds.name).images[0].name,
                                "storage_key", legacy_input)
            store.objects["orgs/relay-org/tasks/other/assets/x.tif"] = b"keep"
            image_path = abs_path_for_file_url(ds.images[0].image)

            frappe.delete_doc("WebODM Task", self.task.name, ignore_permissions=True, force=True)

            self.assertEqual(sorted(store.objects), sorted([legacy_input, "orgs/relay-org/tasks/other/assets/x.tif"]))
            release.assert_called_once()
        # the dataset outlives the task, with its rows and files
        self.assertTrue(frappe.db.exists("WebODM Dataset", ds.name))
        self.assertEqual(frappe.db.count("WebODM Dataset Image", {"parent": ds.name}), 1)
        self.assertTrue(os.path.exists(image_path))
        self.assertTrue(frappe.db.exists("File", {"attached_to_doctype": "WebODM Dataset", "attached_to_name": ds.name}))
        # recreate so tearDown has something to delete
        frappe.set_user(self.user)
        self.task = frappe.get_doc({"doctype": "WebODM Task", "dataset": ds.name, "project": self.project,
                                    "title": "Relay Task 2", "status": "Pending"}).insert()
        frappe.set_user("Administrator")


class TestBackfill(_Base):
    """Legacy, host-only data converges to object storage through the periodic backfill."""

    def test_legacy_outputs_inputs_and_runs_are_copied_up(self):
        # A completed task from before object storage: Attach fields set, no asset rows, no keys.
        ortho = save_private_file_from_stream(io.BytesIO(b"II*\x00legacy"), f"{self.task.name}_orthophoto.tif",
                                              attached_to_doctype="WebODM Task", attached_to_name=self.task.name,
                                              ignore_permissions=True)
        ds = self._dataset([("DJI_0009.JPG", b"\xff\xd8legacy")])  # host-only dataset image, no key
        self.task.status = "Completed"
        self.task.orthophoto = ortho.file_url
        self.task.save(ignore_permissions=True)

        with use_fake_storage() as store:
            stats = assets.sync_pending()
            # other Completed tasks in the shared test DB may be picked up too
            self.assertGreaterEqual(stats["inputs"], 1)
            self.assertGreaterEqual(stats["assets"], 1)
            t = frappe.get_doc("WebODM Task", self.task.name)
            d = frappe.get_doc("WebODM Dataset", ds.name)
            p = self._prefix()
            # The key follows the unique on-disk name (Frappe suffixes a colliding
            # upload name), so match on prefix and original filename.
            key = d.images[0].storage_key
            self.assertTrue(key.startswith(self._dataset_prefix(ds) + "inputs/") and key.endswith("DJI_0009.JPG"), key)
            self.assertEqual(store.objects[key], b"\xff\xd8legacy")
            row = {r.kind: r for r in t.assets}["orthophoto"]
            self.assertEqual(row.storage_key, p + "assets/orthophoto.tif")
            self.assertEqual(store.objects[row.storage_key], b"II*\x00legacy")
            self.assertFalse(row.is_cog)  # copied as-is; not converted by the backfill
            # second pass: nothing left to do for this task
            before = dict(store.objects)
            assets.sync_pending()
            self.assertEqual([k for k in store.objects if k.startswith(p)], [k for k in before if k.startswith(p)])
            # and now the blobs are evictable (and recoverable)
            self.task.db_set("status", "Completed")
            path = abs_path_for_file_url(ortho.file_url)
            old = time.time() - 10 ** 6
            os.utime(path, (old, old))
            cache.evict(max_bytes=0, idle_seconds=1)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(open(assets.ensure_asset_local(t, "orthophoto"), "rb").read(), b"II*\x00legacy")


class TestReprocessing(_Base):
    """Restarting a task must not let the relay's resume logic mistake the
    previous run's outputs for the current run's."""

    def _complete(self, store, ortho: bytes, laz: bytes):
        z = _zip({"odm_orthophoto/odm_orthophoto.tif": ortho,
                  "odm_georeferencing/odm_georeferenced_model.laz": laz})
        with patch.object(geospatial, "cogify", side_effect=_fake_cogify(store)), \
                patch.object(task_runner.raster_metadata, "capture"), \
                patch.object(compute, "release_for_task"):
            task_runner._download_assets(_FakeClient(z), "U-RELAY", frappe.get_doc("WebODM Task", self.task.name))
        return frappe.get_doc("WebODM Task", self.task.name)

    def test_restart_serves_the_new_outputs_not_the_old(self):
        from webodm_core.api import task as task_api

        with use_fake_storage() as store:
            t = self._complete(store, b"II*\x00run-one", b"LASF-one")
            self.assertEqual(t.status, "Completed")
            old_ortho_url, old_laz_url = t.orthophoto, t.point_cloud
            p = self._prefix()
            self.assertEqual(store.objects[p + "assets/orthophoto.tif"], b"COG:II*\x00run-one")

            # Restart through the API, as the Start button does.
            frappe.set_user(self.user)
            with patch.object(frappe, "request", MagicMock(data=b'{"task_name": "%s"}' % self.task.name.encode())), \
                    patch.object(task_runner, "enqueue_process"):
                task_api.process_task()
            frappe.set_user("Administrator")

            t = frappe.get_doc("WebODM Task", self.task.name)
            self.assertEqual(t.status, "Queued")
            # previous bookkeeping is gone: fields, rows, metadata, objects, cache files
            self.assertFalse(t.orthophoto or t.point_cloud or t.orthophoto_extent or t.epsg)
            self.assertEqual(t.assets, [])
            self.assertEqual(t.raster_metadata, [])
            self.assertFalse([k for k in store.objects if "/assets/" in k or "/raw/" in k], list(store.objects))
            self.assertFalse(frappe.db.exists("File", {"file_url": old_ortho_url}))
            self.assertFalse(frappe.db.exists("File", {"file_url": old_laz_url}))
            # inputs are untouched by a restart (they belong to the dataset)
            self.assertEqual(frappe.db.count("WebODM Dataset Image", {"parent": t.dataset}), 1)

            # Second run with different outputs: everything reflects run two.
            t.db_set({"status": "Running", "node_task_id": "U-RELAY-2"})
            t = self._complete(store, b"II*\x00run-two", b"LASF-two")
            self.assertEqual(t.status, "Completed")
            rows = {r.kind: r for r in t.assets}
            self.assertEqual(store.objects[rows["orthophoto"].storage_key], b"COG:II*\x00run-two")
            self.assertEqual(store.objects[rows["point_cloud"].storage_key], b"LASF-two")
            with open(abs_path_for_file_url(t.orthophoto), "rb") as fh:
                self.assertEqual(fh.read(), b"COG:II*\x00run-two")
            with open(abs_path_for_file_url(t.point_cloud), "rb") as fh:
                self.assertEqual(fh.read(), b"LASF-two")

    def test_without_reset_stale_rows_would_be_skipped(self):
        # Documents the failure mode the reset prevents: relay skips a kind whose
        # row + field already exist.
        with use_fake_storage() as store:
            t = self._complete(store, b"II*\x00run-one", b"LASF-one")
            t.db_set({"status": "Running"})
            t = self._complete(store, b"II*\x00run-two", b"LASF-two")
            self.assertEqual(store.objects[self._prefix() + "assets/orthophoto.tif"], b"COG:II*\x00run-one")

    def test_reset_keeps_inputs_and_tolerates_storage_outage(self):
        ds = self._dataset([("DJI_0001.JPG", b"\xff\xd8img")])
        with use_fake_storage() as store:
            assets.sync_task_inputs(self.task)
            t = self._complete(store, b"II*\x00one", b"LASF")
            input_key = frappe.get_doc("WebODM Dataset", ds.name).images[0].storage_key
            store.fail_next = storage.StorageUnavailable("storage unreachable")
            stats = assets.reset_outputs(t)
            self.assertEqual(stats["objects"], 0)  # outage: object deletion skipped, logged
            t = frappe.get_doc("WebODM Task", self.task.name)
            self.assertEqual(t.assets, [])
            self.assertFalse(t.orthophoto)
            d = frappe.get_doc("WebODM Dataset", ds.name)
            self.assertEqual(d.images[0].storage_key, input_key)
            self.assertIn(input_key, store.objects)
            self.assertTrue(os.path.exists(abs_path_for_file_url(d.images[0].image)))
