"""On-demand Potree octree: state machine, dedup, retry, storage, cleanup, serving.

The geospatial call is faked (``geospatial.to_potree``): the fake writes the
three octree files where the real converter would, so everything downstream
— File documents, asset rows, caching from the fake bucket, range serving —
runs for real.
"""

from __future__ import annotations

import io
import json
import os
import threading
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, get_test_client, now_datetime

from webodm_core.api import pointcloud as api
from webodm_core.plugins import geospatial
from webodm_core.plugins.files import abs_path_for_file_url, save_private_file_from_stream
from webodm_core import storage
from webodm_core.storage import assets, cache
from webodm_core.storage.testing import use_fake_storage
from webodm_core.testing import make_dataset
from webodm_core.webodm_core.processing import potree

OCTREE = {
    "metadata.json": json.dumps({"version": "2.0", "points": 3, "boundingBox": {"min": [0, 0, 0], "max": [1, 1, 1]}}).encode(),
    "hierarchy.bin": bytes(range(22)),
    "octree.bin": bytes(range(256)) * 4,
}
SUMMARY = {
    "points": 3, "spacing": 0.5, "bounding_box": {"min": [0, 0, 0], "max": [1, 1, 1]}, "projection": "EPSG:32632",
    "encoding": "UNCOMPRESSED", "duration_s": 1.5,
    "attributes": [{"name": "rgb", "min": [0, 0, 0], "max": [255, 255, 255]},
                   {"name": "classification", "min": [2], "max": [6], "present_values": [2, 6]}],
}


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                        "roles": [{"role": "WebODM User"}], "send_welcome_email": 0}).insert(ignore_permissions=True)
    return email


def _org(name, owner):
    org = frappe.db.get_value("WebODM Organization", {"organization_name": name})
    if not org:
        org = frappe.get_doc({"doctype": "WebODM Organization", "organization_name": name}).insert(ignore_permissions=True).name
    if not frappe.db.exists("WebODM Org Membership", {"user": owner, "organization": org}):
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": owner, "organization": org,
                        "role": "Owner"}).insert(ignore_permissions=True)
    return org


def fake_to_potree_local(calls):
    def to_potree(path, output_path, projection=None, name=None, timeout=None):
        calls.append({"path": path, "output_path": output_path, "projection": projection, "name": name})
        os.makedirs(output_path, exist_ok=True)
        for fname, data in OCTREE.items():
            with open(os.path.join(output_path, fname), "wb") as fh:
                fh.write(data)
        return {**SUMMARY, "files": {f: {"path": os.path.join(output_path, f), "size": len(d)} for f, d in OCTREE.items()},
                "output_path": output_path}
    return to_potree


def fake_to_potree_s3(store, calls):
    def to_potree(path, output_path, projection=None, name=None, timeout=None):
        calls.append({"path": path, "output_path": output_path, "projection": projection, "name": name})
        assert path.startswith("s3://") and output_path.startswith("s3://"), (path, output_path)
        prefix = storage.key_from_uri(output_path)
        for fname, data in OCTREE.items():
            key = f"{prefix}/{fname}"
            store.objects[key] = data
            store.content_types[key] = "application/octet-stream"
        return {**SUMMARY, "files": {f: {"path": f"{output_path}/{f}", "size": len(d)} for f, d in OCTREE.items()},
                "output_path": output_path}
    return to_potree


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The job code commits, so fixtures survive between classes: get-or-create.
        cls.user = _user("potree_owner@example.com")
        cls.outsider = _user("potree_outsider@example.com")
        cls.org = _org("Potree Org", cls.user)
        _org("Potree Other Org", cls.outsider)
        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.user)
        existing = frappe.db.get_value("WebODM Project", {"title": "Potree Project", "organization": cls.org})
        cls.project = existing or frappe.get_doc({"doctype": "WebODM Project", "title": "Potree Project"}).insert().name
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def setUp(self):
        frappe.local.webodm_org_cache = {}
        frappe.set_user(self.user)
        self.dataset = make_dataset("Potree inputs")
        self.task = frappe.get_doc({"doctype": "WebODM Task", "dataset": self.dataset.name, "project": self.project,
                                    "title": "Potree Task", "status": "Completed", "epsg": 32632}).insert()
        frappe.set_user("Administrator")
        self.laz = save_private_file_from_stream(io.BytesIO(b"LASF" + bytes(300)), f"{self.task.name}_georeferenced_model.laz",
                                                 attached_to_doctype="WebODM Task", attached_to_name=self.task.name,
                                                 ignore_permissions=True)
        self.task.db_set("point_cloud", self.laz.file_url)
        self.task.reload()
        self.deleted = False
        patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        frappe.set_user("Administrator")
        if not self.deleted:
            for f in frappe.get_all("File", filters={"attached_to_doctype": "WebODM Task",
                                                     "attached_to_name": self.task.name}, pluck="name"):
                frappe.delete_doc("File", f, ignore_permissions=True, force=True)
            frappe.delete_doc("WebODM Task", self.task.name, ignore_permissions=True, force=True)
        for ds in frappe.get_all("WebODM Dataset", filters={"organization": self.org}, pluck="name"):
            if not frappe.db.exists("WebODM Task", {"dataset": ds}):
                frappe.delete_doc("WebODM Dataset", ds, ignore_permissions=True, force=True)
        # The job commits mid-test; commit the clean-up too so a class-level
        # rollback cannot resurrect this test's File rows for the next class.
        frappe.db.commit()

    # helpers
    def _status(self):
        return frappe.db.get_value("WebODM Task", self.task.name, "potree_status")

    def _convert_locally(self):
        calls = []
        with patch.object(geospatial, "to_potree", side_effect=fake_to_potree_local(calls)):
            potree.convert_job(self.task.name)
        self.task.reload()
        return calls

    def _attached(self, suffix):
        return frappe.get_all("File", filters={"attached_to_doctype": "WebODM Task", "attached_to_name": self.task.name,
                                               "file_name": ("like", f"%{suffix}")}, fields=["name", "file_url"])


class TestEnsure(_Base):
    def test_opening_enqueues_once_and_is_idempotent(self):
        with patch("frappe.enqueue") as enqueue:
            first = potree.ensure(self.task)
            second = potree.ensure(self.task)
            third = api.potree_state(self.task.name, start=1)
        self.assertEqual(first["status"], "Queued")
        self.assertEqual(second["status"], "Queued")
        self.assertEqual(third["status"], "Queued")
        self.assertEqual(enqueue.call_count, 1, "two openers -> one job")
        kw = enqueue.call_args.kwargs
        self.assertEqual(kw["job_id"], f"webodm:potree:{self.task.name}")
        self.assertTrue(kw["deduplicate"])
        self.assertEqual(kw["queue"], "long")
        self.assertGreaterEqual(kw["timeout"], potree.CONVERSION_TIMEOUT)
        self.assertEqual(kw["task_name"], self.task.name)

    def test_reading_state_never_starts_a_conversion(self):
        with patch("frappe.enqueue") as enqueue:
            state = api.potree_state(self.task.name)
        self.assertEqual(state["status"], "")
        self.assertIsNone(state["files"])
        self.assertEqual(state["point_cloud"], self.laz.file_url)
        self.assertFalse(state["has_dsm"])
        enqueue.assert_not_called()

    def test_task_without_point_cloud_is_not_converted(self):
        self.task.db_set("point_cloud", None)
        self.task.reload()
        with patch("frappe.enqueue") as enqueue:
            state = potree.ensure(self.task)
        self.assertEqual(state["status"], "")
        enqueue.assert_not_called()

    def test_failed_is_only_retried_on_request(self):
        self.task.db_set({"potree_status": "Failed", "potree_error": "boom", "potree_updated": now_datetime()})
        with patch("frappe.enqueue") as enqueue:
            state = potree.ensure(self.task)
            self.assertEqual(state["status"], "Failed")
            self.assertEqual(state["error"], "boom")
            enqueue.assert_not_called()
            retried = api.potree_state(self.task.name, retry=1)
            self.assertEqual(retried["status"], "Queued")
            self.assertIsNone(retried["error"])
            enqueue.assert_called_once()

    def test_stale_running_state_is_restarted(self):
        self.task.db_set({"potree_status": "Running",
                          "potree_updated": add_to_date(now_datetime(), seconds=-(potree.STALE_SECONDS + 60))})
        with patch("frappe.enqueue") as enqueue:
            state = potree.ensure(self.task)
        self.assertEqual(state["status"], "Queued")
        enqueue.assert_called_once()

    def test_fresh_running_state_is_left_alone(self):
        self.task.db_set({"potree_status": "Running", "potree_updated": now_datetime()})
        with patch("frappe.enqueue") as enqueue:
            state = potree.ensure(self.task)
        self.assertEqual(state["status"], "Running")
        enqueue.assert_not_called()

    def test_outsider_cannot_read_state(self):
        frappe.set_user(self.outsider)
        frappe.local.webodm_org_cache = {}
        try:
            with self.assertRaises(frappe.PermissionError):
                api.potree_state(self.task.name, start=1)
        finally:
            frappe.set_user("Administrator")
            frappe.local.webodm_org_cache = {}


class TestConvertJob(_Base):
    def test_host_mode_attaches_the_three_files(self):
        calls = self._convert_locally()
        self.assertEqual(self._status(), "Ready")
        self.assertEqual(len(calls), 1)
        self.assertTrue(os.path.isabs(calls[0]["path"]))
        self.assertTrue(calls[0]["path"].endswith("_georeferenced_model.laz"))
        self.assertEqual(calls[0]["projection"], "EPSG:32632")
        self.assertEqual(calls[0]["name"], "Potree Task")
        self.assertFalse(os.path.exists(calls[0]["output_path"]), "scratch dir removed")

        state = potree.state(self.task)
        self.assertEqual(state["status"], "Ready")
        self.assertEqual(set(state["files"]), {"metadata.json", "hierarchy.bin", "octree.bin"})
        self.assertEqual(state["cache"], "warm")
        self.assertEqual(state["summary"]["points"], 3)
        self.assertEqual(state["summary"]["storage"], "host")
        self.assertEqual(state["summary"]["files"]["octree.bin"], len(OCTREE["octree.bin"]))
        for fname, url in state["files"].items():
            self.assertTrue(url.startswith("/private/files/"))
            self.assertIn(f"{self.task.name}_potree_", url)
            with open(abs_path_for_file_url(url), "rb") as fh:
                self.assertEqual(fh.read(), OCTREE[fname])
        rows = assets.potree_rows(self.task)
        self.assertEqual({r.kind for r in rows}, set(assets.POTREE_KINDS))
        self.assertTrue(all(not r.storage_key for r in rows))
        self.assertEqual(frappe.db.get_value("WebODM Task", self.task.name, "potree_source"), self.laz.file_url)

    def test_failure_records_error_and_can_be_retried(self):
        with patch.object(geospatial, "to_potree", side_effect=geospatial.GeospatialError("conversion failed: bad LAZ")):
            potree.convert_job(self.task.name)
        self.assertEqual(self._status(), "Failed")
        state = potree.state(self.task)
        self.assertIn("bad LAZ", state["error"])
        self.assertIsNone(state["files"])
        self.assertEqual(assets.potree_rows(self.task), [])
        # retry succeeds and replaces the state
        self._convert_locally()
        self.assertEqual(self._status(), "Ready")
        self.assertIsNone(potree.state(self.task)["error"])

    def test_reconversion_replaces_files_without_leaving_orphans(self):
        self._convert_locally()
        first = potree.state(self.task)["files"]
        self._convert_locally()
        second = potree.state(self.task)["files"]
        self.assertEqual(sorted(first.values()), sorted(second.values()), "names stay stable")
        self.assertEqual(len(self._attached("octree.bin")), 1)
        self.assertEqual(len(assets.potree_rows(self.task)), 3)

    def test_changed_point_cloud_invalidates_the_octree(self):
        self._convert_locally()
        self.task.db_set("point_cloud", "/private/files/other.laz")
        self.task.reload()
        self.assertEqual(potree.state(self.task)["status"], "", "octree built from a different cloud")
        with patch("frappe.enqueue") as enqueue:
            self.assertEqual(potree.ensure(self.task)["status"], "Queued")
        enqueue.assert_called_once()

    def test_missing_files_on_host_without_storage_reports_failed(self):
        self._convert_locally()
        url = potree.state(self.task)["files"]["octree.bin"]
        os.remove(abs_path_for_file_url(url))
        state = potree.state(self.task)
        self.assertEqual(state["status"], "Failed")
        self.assertIn("no longer on this host", state["error"])

    def test_s3_mode_stores_under_the_task_prefix_and_caches(self):
        calls = []
        with use_fake_storage() as store:
            laz_key = storage.task_key(self.task, "assets", "georeferenced_model.laz")
            store.objects[laz_key] = b"LASF" + bytes(300)
            assets.record_asset(self.task, "point_cloud", filename="georeferenced_model.laz",
                                file_url=self.laz.file_url, storage_key=laz_key, size=304)
            with patch.object(geospatial, "to_potree", side_effect=fake_to_potree_s3(store, calls)):
                potree.convert_job(self.task.name)
            self.task.reload()
            self.assertEqual(self._status(), "Ready")
            prefix = storage.task_prefix(self.task)
            self.assertEqual(calls[0]["path"], storage.uri(laz_key))
            self.assertEqual(calls[0]["output_path"], storage.uri(f"{prefix}assets/potree"))
            for fname in OCTREE:
                self.assertIn(f"{prefix}assets/potree/{fname}", store.objects)
            rows = {r.kind: r for r in assets.potree_rows(self.task)}
            self.assertEqual(rows["potree_octree"].storage_key, f"{prefix}assets/potree/octree.bin")
            self.assertEqual(rows["potree_octree"].file_size, len(OCTREE["octree.bin"]))
            state = potree.state(self.task)
            self.assertEqual(state["cache"], "warm")
            self.assertEqual(state["summary"]["storage"], "s3")

            # Evicted host copy: served again after a background refill.
            path = abs_path_for_file_url(state["files"]["octree.bin"])
            os.remove(path)
            with patch("frappe.enqueue") as enqueue:
                warming = potree.state(self.task)
            self.assertEqual(warming["status"], "Ready")
            self.assertEqual(warming["cache"], "warming")
            self.assertEqual(enqueue.call_args.kwargs["job_id"], f"webodm:cachefill:{state['files']['octree.bin']}")
            cache.fill_job(state["files"]["octree.bin"])
            self.assertTrue(os.path.exists(path))
            self.assertEqual(potree.state(self.task)["cache"], "warm")

            # Eviction treats the octree like any other cached artifact.
            self.assertIn(path, [c["path"] for c in cache._candidates()])

    def test_s3_mode_without_point_cloud_object_falls_back_to_host(self):
        calls = []
        with use_fake_storage():
            with patch.object(geospatial, "to_potree", side_effect=fake_to_potree_local(calls)):
                potree.convert_job(self.task.name)
        self.task.reload()
        self.assertEqual(self._status(), "Ready")
        self.assertFalse(calls[0]["path"].startswith("s3://"), "legacy host-only LAZ converts on the host")


class TestCleanup(_Base):
    def test_reprocessing_discards_the_octree(self):
        self._convert_locally()
        paths = [abs_path_for_file_url(u) for u in potree.state(self.task)["files"].values()]
        self.assertTrue(all(os.path.exists(p) for p in paths))

        assets.reset_outputs(self.task)

        for field in potree.STATE_FIELDS:
            self.assertFalse(frappe.db.get_value("WebODM Task", self.task.name, field), field)
        self.assertEqual(assets.potree_rows(self.task), [])
        self.assertEqual(self._attached("octree.bin"), [])
        self.assertFalse(any(os.path.exists(p) for p in paths))
        self.task.reload()
        self.assertEqual(potree.state(self.task)["status"], "")

    def test_reprocessing_removes_octree_objects(self):
        calls = []
        with use_fake_storage() as store:
            laz_key = storage.task_key(self.task, "assets", "georeferenced_model.laz")
            store.objects[laz_key] = b"LASF"
            assets.record_asset(self.task, "point_cloud", filename="georeferenced_model.laz",
                                file_url=self.laz.file_url, storage_key=laz_key, size=4)
            with patch.object(geospatial, "to_potree", side_effect=fake_to_potree_s3(store, calls)):
                potree.convert_job(self.task.name)
            self.task.reload()
            assets.reset_outputs(self.task)
            self.assertFalse([k for k in store.objects if "/assets/potree/" in k])

    def test_deleting_the_task_removes_octree_files_and_objects(self):
        calls = []
        with use_fake_storage() as store:
            laz_key = storage.task_key(self.task, "assets", "georeferenced_model.laz")
            store.objects[laz_key] = b"LASF"
            assets.record_asset(self.task, "point_cloud", filename="georeferenced_model.laz",
                                file_url=self.laz.file_url, storage_key=laz_key, size=4)
            with patch.object(geospatial, "to_potree", side_effect=fake_to_potree_s3(store, calls)):
                potree.convert_job(self.task.name)
            self.task.reload()
            paths = [abs_path_for_file_url(u) for u in potree.state(self.task)["files"].values()]
            with patch("webodm_core.webodm_core.processing.compute.release_for_task"):
                frappe.delete_doc("WebODM Task", self.task.name, ignore_permissions=True, force=True)
            self.deleted = True
            self.assertFalse([k for k in store.objects if k.startswith(storage.task_prefix(self.task))])
        self.assertFalse(any(os.path.exists(p) for p in paths))
        self.assertFalse(frappe.db.exists("File", {"file_url": ("like", f"%{self.task.name}_potree_%")}))


class TestServing(_Base):
    """The octree files are private files: cookie auth, org isolation, byte ranges."""

    def _sid(self, user):
        from frappe.auth import CookieManager, LoginManager
        from frappe.utils import set_request

        set_request(path="/")
        frappe.local.cookie_manager = CookieManager()
        frappe.local.login_manager = LoginManager()
        frappe.local.login_manager.login_as(user)
        sid = frappe.session.sid
        frappe.db.commit()
        frappe.set_user("Administrator")
        return sid

    def _get(self, path, headers=None):
        site = frappe.local.site
        box = {}

        def run():
            with patch("frappe.app.get_site_name", return_value=site):
                client = get_test_client(use_cookies=False)
                box["resp"] = client.get(path, headers=headers or {})

        t = threading.Thread(target=run)
        t.start()
        t.join()
        return box["resp"]

    def test_private_file_route_serves_byte_ranges(self):
        self._convert_locally()
        frappe.db.commit()  # the request thread uses its own connection
        url = potree.state(self.task)["files"]["octree.bin"]
        sid = self._sid(self.user)
        cookie = {"Cookie": f"sid={sid}"}

        full = self._get(url, cookie)
        self.assertEqual(full.status_code, 200, full.data[:200])
        self.assertEqual(full.data, OCTREE["octree.bin"])

        part = self._get(url, {**cookie, "Range": "bytes=10-19"})
        self.assertEqual(part.status_code, 206, part.data[:200])
        self.assertEqual(part.headers.get("Content-Range"), f"bytes 10-19/{len(OCTREE['octree.bin'])}")
        self.assertEqual(part.headers.get("Accept-Ranges"), "bytes")
        self.assertEqual(part.headers.get("Content-Length"), "10")
        self.assertEqual(part.data, OCTREE["octree.bin"][10:20])

        tail = self._get(url, {**cookie, "Range": "bytes=1000-"})
        self.assertEqual(tail.status_code, 206)
        self.assertEqual(tail.data, OCTREE["octree.bin"][1000:])

        beyond = self._get(url, {**cookie, "Range": "bytes=99999-100000"})
        self.assertEqual(beyond.status_code, 416)

    def test_private_file_route_enforces_session_and_organization(self):
        self._convert_locally()
        frappe.db.commit()
        url = potree.state(self.task)["files"]["metadata.json"]
        anonymous = self._get(url, {"Range": "bytes=0-3"})
        self.assertIn(anonymous.status_code, (401, 403))
        outsider = self._get(url, {"Cookie": f"sid={self._sid(self.outsider)}", "Range": "bytes=0-3"})
        self.assertEqual(outsider.status_code, 403)
        owner = self._get(url, {"Cookie": f"sid={self._sid(self.user)}", "Range": "bytes=0-3"})
        self.assertEqual(owner.status_code, 206)
        self.assertEqual(owner.data, OCTREE["metadata.json"][:4])
