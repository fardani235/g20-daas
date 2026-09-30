"""The dataset library: creation rules, immutability, sharing, deletion order, storage.

A dataset is the reusable, organization-scoped input of any number of tasks.
These tests pin the contract the frontend and the pipeline rely on:

* a dataset is never empty and its images are fixed once it exists;
* every task references exactly one dataset of its own organization;
* deleting a task never touches the dataset or its images; deleting a
  dataset is refused while a task uses it (naming the task) and otherwise
  removes its stored images by the recorded object keys — including a
  legacy ``tasks/<task>/inputs/`` key kept by the migration;
* the org boundary holds for the API and the storage helpers.
"""

from __future__ import annotations

import io
import os
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.datastructures import FileStorage, MultiDict

from webodm_core import datasets
from webodm_core.api import dataset as dataset_api
from webodm_core.api import task as task_api
from webodm_core.plugins.files import abs_path_for_file_url
from webodm_core.storage import cache
from webodm_core.storage.testing import use_fake_storage
from webodm_core.testing import make_dataset, tiny_jpeg
from webodm_core.webodm_core.doctype.webodm_dataset.webodm_dataset import DatasetImagesFixed, DatasetInUse


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                        "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email)
    u.roles = []
    u.append("roles", {"role": "WebODM User"})
    u.save(ignore_permissions=True)
    return email


def _org(name):
    existing = frappe.db.get_value("WebODM Organization", {"organization_name": name}, "name")
    if existing:
        return existing
    return frappe.get_doc({"doctype": "WebODM Organization", "organization_name": name}
                          ).insert(ignore_permissions=True).name


def _join(user, org, role="Owner"):
    if not frappe.db.exists("WebODM Org Membership", {"user": user, "organization": org}):
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": user, "organization": org,
                        "role": role}).insert(ignore_permissions=True)


def _as(user):
    frappe.set_user(user)
    frappe.local.webodm_org_cache = {}


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = _user("dataset_owner@example.com")
        cls.member = _user("dataset_member@example.com")
        cls.outsider = _user("dataset_outsider@example.com")
        cls.org = _org("Dataset Org")
        cls.other_org = _org("Dataset Other Org")
        _join(cls.owner, cls.org)
        _join(cls.member, cls.org, role="Member")
        _join(cls.outsider, cls.other_org)
        _as(cls.owner)
        cls.project = frappe.get_doc({"doctype": "WebODM Project", "title": "Dataset Project"}).insert().name
        _as(cls.outsider)
        cls.other_project = frappe.get_doc({"doctype": "WebODM Project", "title": "Dataset Other Project"}).insert().name
        _as("Administrator")

    def setUp(self):
        _as(self.owner)
        patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)
        self._saved_request = getattr(frappe.local, "request", None)
        self.addCleanup(lambda: setattr(frappe.local, "request", self._saved_request))

    def tearDown(self):
        _as("Administrator")
        for org in (self.org, self.other_org):
            for t in frappe.get_all("WebODM Task", filters={"organization": org}, pluck="name"):
                frappe.delete_doc("WebODM Task", t, force=True, ignore_permissions=True)
            for ds in frappe.get_all("WebODM Dataset", filters={"organization": org}, pluck="name"):
                frappe.delete_doc("WebODM Dataset", ds, force=True, ignore_permissions=True)

    def _task(self, dataset, title="A task", project=None, **fields):
        return frappe.get_doc({"doctype": "WebODM Task", "project": project or self.project, "title": title,
                               "status": "Pending", "dataset": dataset, **fields}).insert()

    def _request(self, files=None, form=None):
        """Install a fake request for the multipart endpoints (restored after the test)."""
        # A plain _dict, not a MagicMock: frappe.utils.get_url() reads
        # request.host when a File is saved, and a mock there breaks every
        # later File insert in the run.
        req = frappe._dict(files=MultiDict([("files", f) for f in (files or [])]), data=b"",
                           environ={"REQUEST_METHOD": "GET"})
        frappe.local.request = req
        frappe.local.form_dict = frappe._dict(form or {})
        return req

    @staticmethod
    def _upload(name, data):
        return FileStorage(stream=io.BytesIO(data), filename=name, content_type="image/jpeg")


class TestDatasetRules(_Base):
    def test_summary_fields_and_creator(self):
        ds = make_dataset("Survey A", images=[("a.jpg", b"1234"), ("b.jpg", b"12")])
        self.assertEqual(ds.image_count, 2)
        self.assertEqual(ds.total_size, 6)
        self.assertEqual(ds.organization, self.org)
        self.assertEqual(ds.created_by, self.owner)
        self.assertEqual(frappe.db.get_value("File", {"attached_to_doctype": "WebODM Dataset",
                                                      "attached_to_name": ds.name}, "attached_to_doctype"),
                         "WebODM Dataset")

    def test_empty_dataset_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "WebODM Dataset", "title": "Nothing here"}).insert()
        self.assertFalse(frappe.db.exists("WebODM Dataset", {"title": "Nothing here"}))
        # ...also from the upload path, before anything is written
        with self.assertRaises(frappe.ValidationError):
            datasets.create_from_uploads([], title="Nothing here")

    def test_images_are_fixed_but_title_and_description_are_editable(self):
        ds = make_dataset("Fixed", n=2)
        ds.title = "Renamed"
        ds.description = "now with a description"
        ds.save()
        ds = frappe.get_doc("WebODM Dataset", ds.name)
        self.assertEqual((ds.title, ds.description), ("Renamed", "now with a description"))

        ds.append("images", {"image": ds.images[0].image, "filename": "dup.jpg", "file_size": 1})
        with self.assertRaises(DatasetImagesFixed):
            ds.save()

        ds = frappe.get_doc("WebODM Dataset", ds.name)
        ds.images = ds.images[:1]
        with self.assertRaises(DatasetImagesFixed):
            ds.save()

        ds = frappe.get_doc("WebODM Dataset", ds.name)
        ds.images[0].image = "/private/files/somewhere_else.jpg"
        with self.assertRaises(DatasetImagesFixed):
            ds.save()
        self.assertEqual(frappe.db.count("WebODM Dataset Image", {"parent": ds.name}), 2)

    def test_task_requires_a_dataset(self):
        with self.assertRaises(frappe.MandatoryError):
            frappe.get_doc({"doctype": "WebODM Task", "project": self.project, "title": "no inputs",
                            "status": "Pending"}).insert()

    def test_task_cannot_use_another_organizations_dataset(self):
        _as(self.outsider)
        theirs = make_dataset("Theirs")
        _as(self.owner)
        with self.assertRaises(frappe.PermissionError):
            self._task(theirs.name)
        with self.assertRaises((frappe.DoesNotExistError, frappe.LinkValidationError)):
            self._task("no-such-dataset")

    def test_many_tasks_share_one_dataset(self):
        ds = make_dataset("Shared", n=1)
        a, b = self._task(ds.name, "one"), self._task(ds.name, "two")
        self.assertEqual({t.name for t in frappe.get_all("WebODM Task", filters={"dataset": ds.name})},
                         {a.name, b.name})
        self.assertEqual([t["name"] for t in datasets.referencing_tasks(ds.name)], [a.name, b.name])
        self.assertEqual(datasets.task_image_count(a), 1)


class TestDeletionOrder(_Base):
    def test_deleting_a_task_leaves_the_dataset_and_its_images(self):
        ds = make_dataset("Keep me", n=2)
        a, b = self._task(ds.name, "one"), self._task(ds.name, "two")
        paths = [abs_path_for_file_url(r.image) for r in ds.images]

        frappe.delete_doc("WebODM Task", a.name)
        frappe.delete_doc("WebODM Task", b.name)  # the last task using it

        self.assertTrue(frappe.db.exists("WebODM Dataset", ds.name))
        self.assertEqual(frappe.db.count("WebODM Dataset Image", {"parent": ds.name}), 2)
        self.assertTrue(all(os.path.exists(p) for p in paths))
        self.assertEqual(frappe.db.count("File", {"attached_to_doctype": "WebODM Dataset",
                                                  "attached_to_name": ds.name}), 2)

    def test_referenced_dataset_cannot_be_deleted_and_the_error_names_the_tasks(self):
        ds = make_dataset("In use", n=1)
        t1 = self._task(ds.name, "Survey flight 1")
        t2 = self._task(ds.name, "Survey flight 2")
        with self.assertRaises(DatasetInUse) as ctx:
            frappe.delete_doc("WebODM Dataset", ds.name)
        msg = str(ctx.exception)
        self.assertIn("Survey flight 1", msg)
        self.assertIn("Survey flight 2", msg)
        self.assertIn("2 task(s)", msg)
        self.assertTrue(frappe.db.exists("WebODM Dataset", ds.name))
        # force=True skips Frappe's link check but not our guard
        with self.assertRaises(DatasetInUse):
            frappe.delete_doc("WebODM Dataset", ds.name, force=True, ignore_permissions=True)
        # the API surfaces the same message
        frappe.local.form_dict = frappe._dict(name=ds.name)
        frappe.local.request = frappe._dict(data=b"")
        with self.assertRaises(DatasetInUse):
            dataset_api.delete_dataset(ds.name)

        frappe.delete_doc("WebODM Task", t1.name)
        frappe.delete_doc("WebODM Task", t2.name)
        frappe.delete_doc("WebODM Dataset", ds.name)
        self.assertFalse(frappe.db.exists("WebODM Dataset", ds.name))

    def test_deleting_an_unreferenced_dataset_removes_objects_by_key_files_and_thumbnails(self):
        ds = make_dataset("Doomed", images=[("new.jpg", tiny_jpeg()), ("legacy.jpg", tiny_jpeg((1, 2, 3)))])
        paths = [abs_path_for_file_url(r.image) for r in ds.images]
        with use_fake_storage() as store:
            from webodm_core.storage import assets

            assets.sync_inputs(ds)
            ds = frappe.get_doc("WebODM Dataset", ds.name)
            new_key = ds.images[0].storage_key
            self.assertTrue(new_key.startswith(f"orgs/dataset-org/datasets/{ds.name}/inputs/"), new_key)
            # simulate a migrated row: key under a task prefix, in the same org namespace
            legacy_key = "orgs/dataset-org/tasks/OLD-TASK/inputs/legacy.jpg"
            store.objects[legacy_key] = store.objects.pop(ds.images[1].storage_key)
            frappe.db.set_value("WebODM Dataset Image", ds.images[1].name, "storage_key", legacy_key)
            # a key from another org's namespace must never be deleted through this dataset
            store.objects["orgs/someone-else/datasets/x/inputs/theirs.jpg"] = b"theirs"
            store.objects["orgs/dataset-org/tasks/OLD-TASK/assets/orthophoto.tif"] = b"an output, not ours"
            thumb = datasets.ensure_thumbnail(ds, ds.images[0], 128)
            self.assertTrue(os.path.exists(thumb))

            frappe.delete_doc("WebODM Dataset", ds.name)

            self.assertEqual(sorted(store.objects), ["orgs/dataset-org/tasks/OLD-TASK/assets/orthophoto.tif",
                                                     "orgs/someone-else/datasets/x/inputs/theirs.jpg"])
        self.assertFalse(frappe.db.exists("WebODM Dataset", ds.name))
        self.assertEqual(frappe.db.count("WebODM Dataset Image", {"parent": ds.name}), 0)
        self.assertFalse(any(os.path.exists(p) for p in paths))
        self.assertFalse(frappe.db.exists("File", {"attached_to_doctype": "WebODM Dataset", "attached_to_name": ds.name}))
        self.assertFalse(os.path.exists(thumb))

    def test_identical_images_in_two_datasets_do_not_leave_orphan_blobs(self):
        # Frappe keeps a blob on File delete when another File shares its
        # content_hash (it assumes dedup); our streamed uploads never share a
        # blob, so the dataset delete must remove the file itself.
        same = tiny_jpeg((7, 7, 7))
        a = make_dataset("Copy A", images=[("photo.jpg", same)])
        b = make_dataset("Copy B", images=[("photo.jpg", same)])
        path_a = abs_path_for_file_url(a.images[0].image)
        path_b = abs_path_for_file_url(b.images[0].image)
        self.assertNotEqual(path_a, path_b)
        frappe.delete_doc("WebODM Dataset", a.name)
        self.assertFalse(os.path.exists(path_a))
        self.assertTrue(os.path.exists(path_b))  # the other dataset is intact
        frappe.delete_doc("WebODM Dataset", b.name)
        self.assertFalse(os.path.exists(path_b))

    def test_tampered_key_outside_the_org_is_skipped_on_delete(self):
        ds = make_dataset("Tampered", n=1)
        with use_fake_storage() as store:
            foreign = "orgs/someone-else/datasets/x/inputs/a.jpg"
            store.objects[foreign] = b"theirs"
            frappe.db.set_value("WebODM Dataset Image", ds.images[0].name, "storage_key", foreign)
            frappe.delete_doc("WebODM Dataset", ds.name)
            self.assertIn(foreign, store.objects)


class TestStorageIntegration(_Base):
    def test_cache_lookup_and_eviction_go_through_the_dataset(self):
        ds = make_dataset("Cached", images=[("a.jpg", b"\xff\xd8" + b"a" * 1000)])
        path = abs_path_for_file_url(ds.images[0].image)
        with use_fake_storage():
            from webodm_core.storage import assets

            assets.sync_inputs(ds)
            ds = frappe.get_doc("WebODM Dataset", ds.name)
            key = ds.images[0].storage_key
            # file-url -> key lookup resolves the org from the parent dataset
            self.assertEqual(cache.storage_key_for_file_url(ds.images[0].image), (key, self.org))

            task = self._task(ds.name, status="Running")
            old = 0
            os.utime(path, (old, old))
            # busy: a running task references the dataset -> never evicted
            self.assertEqual(cache.evict(max_bytes=0, idle_seconds=1)["evicted"], 0)
            self.assertTrue(os.path.exists(path))
            task.db_set("status", "Completed")
            self.assertEqual(cache.evict(max_bytes=0, idle_seconds=1)["evicted"], 1)
            self.assertFalse(os.path.exists(path))
            # ...and comes back from storage on demand (the private-file route / thumbnails use this)
            self.assertEqual(cache.ensure_local(ds.images[0].image), path)
            self.assertTrue(os.path.exists(path))

    def test_pending_sync_backfills_dataset_images(self):
        ds = make_dataset("Unsynced", n=2)
        with use_fake_storage() as store:
            from webodm_core.storage import assets

            stats = assets.sync_pending()
            self.assertGreaterEqual(stats["inputs"], 2)
            keys = frappe.get_all("WebODM Dataset Image", filters={"parent": ds.name}, pluck="storage_key")
            self.assertTrue(all(k and k in store.objects for k in keys), keys)


class TestThumbnails(_Base):
    def test_thumbnail_is_small_cached_and_regenerated_from_storage(self):
        big = tiny_jpeg((200, 30, 30), size=(1600, 1200))
        ds = make_dataset("Thumbs", images=[("big.jpg", big)])
        row = ds.images[0]
        with use_fake_storage():
            from webodm_core.storage import assets

            assets.sync_inputs(ds)
            ds = frappe.get_doc("WebODM Dataset", ds.name)
            row = ds.images[0]
            path = datasets.ensure_thumbnail(ds, row, 256)
            from PIL import Image

            with Image.open(path) as im:
                self.assertEqual(im.format, "JPEG")
                self.assertLessEqual(max(im.size), 256)
            self.assertLess(os.path.getsize(path), len(big))
            first = os.path.getmtime(path)
            self.assertEqual(datasets.ensure_thumbnail(ds, row, 256), path)  # cached
            self.assertEqual(os.path.getmtime(path), first)
            # original evicted: rebuilt from object storage
            os.remove(path)
            os.remove(abs_path_for_file_url(row.image))
            self.assertTrue(os.path.exists(datasets.ensure_thumbnail(ds, row, 300)))  # 300 -> 512 bucket
            self.assertTrue(os.path.exists(datasets.thumbnail_path(ds.name, row.name, 512)))
        self.assertEqual(datasets.normalize_thumbnail_size(0), 256)
        self.assertEqual(datasets.normalize_thumbnail_size(10_000), 512)
        self.assertIn(f"image={row.name}", datasets.image_dict(ds.name, row)["thumbnail"])

    def test_thumbnail_endpoint_checks_permission_and_membership(self):
        ds = make_dataset("Guarded", images=[("a.jpg", tiny_jpeg())])
        self._request()
        resp = dataset_api.thumbnail(ds.name, ds.images[0].name, 128)
        self.assertEqual(resp.mimetype, "image/jpeg")
        self.assertIn("private", resp.headers.get("Cache-Control", ""))
        with self.assertRaises(frappe.DoesNotExistError):
            dataset_api.thumbnail(ds.name, "not-a-row", 128)
        _as(self.outsider)
        with self.assertRaises(frappe.PermissionError):
            dataset_api.thumbnail(ds.name, ds.images[0].name, 128)


class TestDatasetApi(_Base):
    def test_list_and_get_are_org_scoped(self):
        mine = make_dataset("Mine", n=1)
        _as(self.outsider)
        theirs = make_dataset("Theirs", n=1)
        _as(self.owner)
        names = [d["name"] for d in dataset_api.list_datasets()]
        self.assertIn(mine.name, names)
        self.assertNotIn(theirs.name, names)
        got = dataset_api.get_dataset(mine.name)
        self.assertEqual(got["image_count"], 1)
        self.assertEqual(len(got["images"]), 1)
        self.assertTrue(got["images"][0]["thumbnail"])
        self.assertEqual(got["tasks"], [])
        with self.assertRaises(frappe.PermissionError):
            dataset_api.get_dataset(theirs.name)
        _as(self.member)  # a plain member of the same org can read and rename
        self.assertIn(mine.name, [d["name"] for d in dataset_api.list_datasets()])
        frappe.local.request = frappe._dict(data=b"")
        frappe.local.form_dict = frappe._dict()
        self.assertEqual(dataset_api.update_dataset(mine.name, title="Renamed by member")["title"], "Renamed by member")

    def test_list_reports_task_usage(self):
        ds = make_dataset("Used", n=1)
        self._task(ds.name)
        row = next(d for d in dataset_api.list_datasets() if d["name"] == ds.name)
        self.assertEqual(row["task_count"], 1)
        self.assertEqual(dataset_api.get_dataset(ds.name)["tasks"][0]["title"], "A task")

    def test_create_dataset_from_uploads_reads_exif_and_syncs(self):
        self._request(files=[self._upload("DJI_0001.JPG", tiny_jpeg(datetime_tag="2024:05:01 10:20:30")),
                             self._upload("DJI_0002.JPG", tiny_jpeg((9, 9, 9)))],
                      form={"title": "Flight 12", "description": "north field"})
        with patch("webodm_core.datasets.schedule_input_sync") as sync, patch("frappe.db.commit"):
            out = dataset_api.create_dataset()
        self.assertEqual(out["title"], "Flight 12")
        self.assertEqual(out["image_count"], 2)
        self.assertEqual(out["images"][0]["capture_time"], "2024-05-01 10:20:30")
        self.assertEqual(out["images"][0]["filename"], "DJI_0001.JPG")
        sync.assert_called_once()
        ds = frappe.get_doc("WebODM Dataset", out["name"])
        self.assertEqual(ds.created_by, self.owner)
        self.assertEqual(frappe.db.count("File", {"attached_to_doctype": "WebODM Dataset",
                                                  "attached_to_name": ds.name}), 2)

    def test_create_dataset_without_files_is_refused(self):
        self._request(files=[], form={"title": "Empty"})
        with self.assertRaises(frappe.ValidationError):
            dataset_api.create_dataset()
        self.assertFalse(frappe.db.exists("WebODM Dataset", {"title": "Empty"}))

    def test_task_creation_from_existing_dataset(self):
        ds = make_dataset("Existing", n=1)
        self._request(form={"project_id": self.project, "dataset": ds.name, "title": "Reuse"})
        with patch("frappe.db.commit"), patch.object(task_api, "_maybe_autostart"):
            out = task_api.upload_images()
        self.assertEqual(out["dataset"], ds.name)
        self.assertEqual(out["dataset_summary"]["name"], ds.name)
        self.assertEqual(len(out["images"]), 1)
        self.assertEqual(out["title"], "Reuse")
        # the dataset was reused, not copied
        self.assertEqual(frappe.db.count("WebODM Dataset", {"organization": self.org, "title": "Existing"}), 1)

    def test_task_creation_from_uploads_creates_the_dataset(self):
        self._request(files=[self._upload("a.jpg", tiny_jpeg())],
                      form={"project_id": self.project, "dataset_title": "Fresh photos"})
        with patch("frappe.db.commit"), patch.object(task_api, "_maybe_autostart"), \
                patch("webodm_core.datasets.schedule_input_sync"):
            out = task_api.upload_images()
        self.assertEqual(out["dataset_summary"]["title"], "Fresh photos")
        self.assertEqual(out["dataset_summary"]["image_count"], 1)
        self.assertEqual(frappe.db.get_value("WebODM Task", out["name"], "dataset"), out["dataset"])

    def test_task_creation_rejects_neither_or_both_inputs(self):
        ds = make_dataset("Either", n=1)
        self._request(form={"project_id": self.project})
        with self.assertRaises(frappe.ValidationError):
            task_api.upload_images()
        self._request(files=[self._upload("a.jpg", tiny_jpeg())], form={"project_id": self.project, "dataset": ds.name})
        with self.assertRaises(frappe.ValidationError):
            task_api.upload_images()

    def test_task_creation_refuses_another_organizations_dataset(self):
        _as(self.outsider)
        theirs = make_dataset("Not yours", n=1)
        _as(self.owner)
        self._request(form={"project_id": self.project, "dataset": theirs.name})
        with self.assertRaises(frappe.PermissionError):
            task_api.upload_images()

    def test_task_progress_and_list_carry_the_dataset_images(self):
        ds = make_dataset("Progress", images=[("g.jpg", tiny_jpeg())], latitude=52.5, longitude=13.4)
        t = self._task(ds.name, "progress task")
        frappe.local.form_dict = frappe._dict(task_name=t.name)
        frappe.local.request = frappe._dict(data=b"")
        out = task_api.get_task_progress()
        self.assertEqual(out["dataset_summary"]["image_count"], 1)
        self.assertEqual(out["images"][0]["latitude"], 52.5)
        self.assertTrue(out["images"][0]["thumbnail"].startswith("/api/method/webodm_core.api.dataset.thumbnail?"))
        rows = task_api.list_tasks(self.project)
        row = next(r for r in rows if r["name"] == t.name)
        self.assertEqual(row["dataset_summary"]["title"], "Progress")
        _as(self.outsider)
        with self.assertRaises(frappe.PermissionError):
            task_api.list_tasks(self.project)


class TestTenantCoverage(FrappeTestCase):
    def test_dataset_is_wired_into_the_tenant_hooks(self):
        self.assertIn("WebODM Dataset", frappe.get_hooks("permission_query_conditions", {}))
        self.assertIn("WebODM Dataset", frappe.get_hooks("has_permission", {}))
        self.assertIn("WebODM Dataset", frappe.get_hooks("doc_events", {}))
