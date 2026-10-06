"""Ground control on datasets: stored with the images, synced like them,
handed to the node as ``gcp_list.txt``, and never silently dropped.

Pins the contract behind "attach ground control alongside the imagery and
have it actually reach the processing engine":

* a GCP file is validated before any photo is written and stored as a
  private File of the dataset, with a parsed summary (points, CRS, image
  names the dataset does not contain);
* it may be attached, replaced and removed after the dataset exists (the
  images stay fixed), by any member of the organization and nobody else;
* dispatch appends it *last* under the fixed name NodeODM recognises, from
  the cache or from object storage, and fails the task loudly when it can be
  read from neither;
* object storage sync, the cache lookup, the periodic backfill and dataset
  deletion all know about the extra object.
"""

from __future__ import annotations

import io
import os
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.datastructures import FileStorage, MultiDict

from webodm_core import datasets
from webodm_core.api import dataset as dataset_api
from webodm_core.api import task as task_api
from webodm_core.plugins.files import abs_path_for_file_url
from webodm_core.storage import assets, cache
from webodm_core.storage.testing import use_fake_storage
from webodm_core.testing import make_dataset, tiny_jpeg
from webodm_core.webodm_core.processing import task_runner

GCP_TEXT = b"""EPSG:32633
544256.7 5320919.9 5.1 3044 2622 IMG_0001.JPG gcp1
544257.7 5320920.9 5.2 3144 2722 IMG_0002.JPG gcp2
544258.7 5320921.9 5.3 3244 2822 IMG_0009.JPG gcp3
"""
GCP_TEXT_2 = b"EPSG:4326\n13.4 52.5 40 100 200 IMG_0001.JPG a\n13.5 52.6 41 300 400 IMG_0002.JPG b\n"


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


def _upload(name, data, content_type="image/jpeg"):
    return FileStorage(stream=io.BytesIO(data), filename=name, content_type=content_type)


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = _user("gcp_owner@example.com")
        cls.member = _user("gcp_member@example.com")
        cls.outsider = _user("gcp_outsider@example.com")
        cls.org = _org("GCP Org")
        cls.other_org = _org("GCP Other Org")
        _join(cls.owner, cls.org)
        _join(cls.member, cls.org, role="Member")
        _join(cls.outsider, cls.other_org)
        _as(cls.owner)
        cls.project = frappe.get_doc({"doctype": "WebODM Project", "title": "GCP Project"}).insert().name
        _as("Administrator")

    def setUp(self):
        _as(self.owner)
        patch("frappe.log_error").start()
        patch("frappe.db.commit").start()
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

    def _request(self, files=None, form=None, gcp=None, data=b""):
        parts = [("files", f) for f in (files or [])]
        if gcp is not None:
            parts.append(("gcp", gcp))
        frappe.local.request = frappe._dict(files=MultiDict(parts), data=data, environ={"REQUEST_METHOD": "POST"})
        frappe.local.form_dict = frappe._dict(form or {})

    def _task(self, dataset, title="A task", **fields):
        return frappe.get_doc({"doctype": "WebODM Task", "project": self.project, "title": title,
                               "status": "Pending", "dataset": dataset, **fields}).insert()

    def _with_gcp(self, title="With GCP", gcp=GCP_TEXT, images=None):
        images = images or [_upload("IMG_0001.JPG", tiny_jpeg()), _upload("IMG_0002.JPG", tiny_jpeg((3, 3, 3)))]
        with patch("webodm_core.datasets.schedule_input_sync"):
            return datasets.create_from_uploads(images, title=title, gcp_upload=_upload("survey_gcps.txt", gcp,
                                                                                         "text/plain"))

    @staticmethod
    def _dispatch(task):
        """Run the dispatch job against a fake node; returns the client mock."""
        client = MagicMock()
        client.info.return_value = {"version": "2.2.4"}
        client.create_task.return_value = {"uuid": "node-uuid-1"}
        node = {"name": "static", "hostname": "node", "port": 3000}
        with patch.object(task_runner, "_first_node", return_value=node), \
                patch.object(task_runner, "_client_for", return_value=client), \
                patch.object(task_runner.compute, "enabled", return_value=False):
            task_runner.process_task(task.name)
        return client


class TestDatasetGCP(_Base):
    def test_created_with_the_images_and_summarised(self):
        ds = self._with_gcp()
        self.assertTrue(ds.gcp_file.startswith("/private/files/"))
        self.assertEqual(ds.gcp_filename, "survey_gcps.txt")
        self.assertEqual(ds.image_count, 2)
        with open(abs_path_for_file_url(ds.gcp_file), "rb") as fh:
            self.assertEqual(fh.read(), GCP_TEXT)
        self.assertEqual(frappe.db.get_value("File", {"file_url": ds.gcp_file}, "attached_to_name"), ds.name)

        block = datasets.summary(frappe.get_doc("WebODM Dataset", ds.name))["gcp"]
        self.assertEqual(block["filename"], "survey_gcps.txt")
        self.assertEqual(block["upload_name"], "gcp_list.txt")
        self.assertEqual(block["crs"], "EPSG:32633")
        self.assertEqual(block["points"], 3)
        self.assertEqual(block["image_count"], 3)
        self.assertEqual(block["unknown_images"], ["IMG_0009.JPG"])
        self.assertFalse(block["few_points"])
        # ...and through the list endpoint's get_all rows too
        row = next(r for r in dataset_api.list_datasets() if r["name"] == ds.name)
        self.assertEqual(row["gcp"]["points"], 3)
        self.assertIsNone(next(r for r in dataset_api.list_datasets() if r["name"] != ds.name)["gcp"]
                          if frappe.db.count("WebODM Dataset", {"organization": self.org}) > 1 else None)

    def test_without_gcp_the_summary_says_none(self):
        ds = make_dataset("Plain", n=1)
        self.assertIsNone(datasets.summary(ds)["gcp"])
        self.assertIsNone(dataset_api.get_dataset(ds.name)["gcp"])

    def test_invalid_gcp_refuses_the_upload_before_any_photo_is_written(self):
        before = frappe.db.count("File", {"attached_to_doctype": "WebODM Dataset"})
        with self.assertRaises(datasets.GCPInvalid) as ctx:
            self._with_gcp("Broken", gcp=tiny_jpeg())  # a photo dropped into the GCP slot
        self.assertIn("Not a valid ground control file", str(ctx.exception))
        with self.assertRaises(datasets.GCPInvalid) as ctx:
            self._with_gcp("Broken", gcp=b"EPSG:32633\n1 2 3 4 a.jpg\n")
        self.assertIn("line 2", str(ctx.exception))
        self.assertFalse(frappe.db.exists("WebODM Dataset", {"title": "Broken"}))
        self.assertEqual(frappe.db.count("File", {"attached_to_doctype": "WebODM Dataset"}), before)

    def test_attach_replace_and_remove_through_the_api(self):
        ds = make_dataset("Later", images=[("IMG_0001.JPG", tiny_jpeg())])
        self.assertIsNone(dataset_api.get_dataset(ds.name)["gcp"])

        # attach (a plain member may, like renaming)
        _as(self.member)
        self._request(form={"name": ds.name}, gcp=_upload("gcp_list.txt", GCP_TEXT, "text/plain"))
        out = dataset_api.set_gcp()
        self.assertEqual(out["gcp"]["points"], 3)
        self.assertEqual(out["gcp"]["unknown_images"], ["IMG_0002.JPG", "IMG_0009.JPG"])
        first_url = out["gcp"]["url"]
        first_path = abs_path_for_file_url(first_url)
        self.assertTrue(os.path.exists(first_path))

        # replace: the old file and blob go, the summary follows the new file
        self._request(form={"name": ds.name}, gcp=_upload("corrected.txt", GCP_TEXT_2, "text/plain"))
        out = dataset_api.set_gcp()
        self.assertNotEqual(out["gcp"]["url"], first_url)
        self.assertEqual(out["gcp"]["filename"], "corrected.txt")
        self.assertEqual(out["gcp"]["crs"], "EPSG:4326")
        self.assertEqual(out["gcp"]["points"], 2)
        self.assertTrue(out["gcp"]["few_points"])
        self.assertFalse(frappe.db.exists("File", {"file_url": first_url}))
        self.assertFalse(os.path.exists(first_path))
        self.assertEqual(frappe.db.count("File", {"attached_to_doctype": "WebODM Dataset",
                                                  "attached_to_name": ds.name}), 2)  # 1 image + 1 gcp

        # a bad replacement leaves the current one in place
        self._request(form={"name": ds.name}, gcp=_upload("junk.txt", b"not a gcp file at all", "text/plain"))
        with self.assertRaises(datasets.GCPInvalid):
            dataset_api.set_gcp()
        self.assertEqual(dataset_api.get_dataset(ds.name)["gcp"]["filename"], "corrected.txt")

        # the images are still fixed
        doc = frappe.get_doc("WebODM Dataset", ds.name)
        doc.images = []
        with self.assertRaises(frappe.ValidationError):
            doc.save()

        # remove
        second_path = abs_path_for_file_url(out["gcp"]["url"])
        self._request(form={"name": ds.name})
        out = dataset_api.remove_gcp()
        self.assertIsNone(out["gcp"])
        doc = frappe.get_doc("WebODM Dataset", ds.name)
        self.assertFalse(doc.gcp_file or doc.gcp_filename or doc.gcp_summary or doc.gcp_storage_key)
        self.assertFalse(os.path.exists(second_path))
        self.assertEqual(frappe.db.count("File", {"attached_to_doctype": "WebODM Dataset",
                                                  "attached_to_name": ds.name}), 1)

    def test_missing_part_and_other_organization_are_refused(self):
        ds = make_dataset("Guarded", n=1)
        self._request(form={"name": ds.name})
        with self.assertRaises(frappe.ValidationError):
            dataset_api.set_gcp()
        _as(self.outsider)
        self._request(form={"name": ds.name}, gcp=_upload("g.txt", GCP_TEXT, "text/plain"))
        with self.assertRaises(frappe.PermissionError):
            dataset_api.set_gcp()
        self._request(form={"name": ds.name})
        with self.assertRaises(frappe.PermissionError):
            dataset_api.remove_gcp()
        _as(self.owner)
        self.assertIsNone(dataset_api.get_dataset(ds.name)["gcp"])

    def test_gcp_file_cannot_be_repointed_at_someone_elses_file(self):
        # The field is writable through /api/resource: a URL that is not an
        # attachment of this dataset is refused, so dispatch can never stream
        # a foreign file to a node.
        mine = make_dataset("Mine", n=1)
        _as(self.outsider)
        theirs = self._with_gcp("Theirs")
        _as(self.owner)
        doc = frappe.get_doc("WebODM Dataset", mine.name)
        doc.gcp_file = theirs.gcp_file
        with self.assertRaises(frappe.PermissionError):
            doc.save()
        doc = frappe.get_doc("WebODM Dataset", mine.name)
        doc.gcp_file = mine.images[0].image  # own attachment, but an image: allowed by the field rule
        doc.save()
        self.assertEqual(frappe.get_doc("WebODM Dataset", mine.name).gcp_filename,
                         mine.images[0].image.rsplit("/", 1)[-1])

    def test_deleting_the_dataset_removes_the_gcp_file_and_blob(self):
        ds = self._with_gcp()
        path = abs_path_for_file_url(ds.gcp_file)
        frappe.delete_doc("WebODM Dataset", ds.name)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(frappe.db.exists("File", {"file_url": ds.gcp_file}))


class TestDispatch(_Base):
    def test_gcp_is_sent_last_as_gcp_list_txt(self):
        ds = self._with_gcp()
        task = self._task(ds.name, status="Queued")
        client = self._dispatch(task)
        images = list(client.create_task.call_args.args[0])
        self.assertEqual([name for name, _ in images], ["IMG_0001.JPG", "IMG_0002.JPG", "gcp_list.txt"])
        with open(images[-1][1], "rb") as fh:
            self.assertEqual(fh.read(), GCP_TEXT)
        self.assertEqual(frappe.db.get_value("WebODM Task", task.name, "status"), "Running")

    def test_without_gcp_nothing_extra_is_sent(self):
        ds = make_dataset("No GCP", images=[("a.jpg", tiny_jpeg())])
        task = self._task(ds.name, status="Queued")
        client = self._dispatch(task)
        self.assertEqual([name for name, _ in client.create_task.call_args.args[0]], ["a.jpg"])

    def test_unreadable_gcp_fails_the_task_instead_of_dropping_it(self):
        ds = self._with_gcp()
        os.remove(abs_path_for_file_url(ds.gcp_file))  # evicted / lost, and no bucket configured
        task = self._task(ds.name, status="Queued")
        client = self._dispatch(task)
        client.create_task.assert_not_called()
        task.reload()
        self.assertEqual(task.status, "Failed")
        self.assertIn("ground control", task.last_error)

    def test_every_task_over_the_dataset_gets_it_including_a_duplicate(self):
        ds = self._with_gcp()
        first = self._task(ds.name, "first")
        frappe.local.request = frappe._dict(data=b"")
        frappe.local.form_dict = frappe._dict(task_name=first.name)
        copy = task_api.duplicate_task()
        self.assertEqual(copy["dataset"], ds.name)
        self.assertEqual(copy["dataset_summary"]["gcp"]["points"], 3)
        self.assertEqual([n for n, _ in assets.input_sources(frappe.get_doc("WebODM Task", copy["name"]))][-1],
                         "gcp_list.txt")


class TestObjectStorage(_Base):
    def test_synced_next_to_the_images_read_back_cold_and_deleted_with_the_dataset(self):
        ds = self._with_gcp()
        task = self._task(ds.name)
        with use_fake_storage() as store:
            self.assertEqual(assets.sync_inputs(ds), 3)  # 2 images + the GCP
            ds = frappe.get_doc("WebODM Dataset", ds.name)
            key = ds.gcp_storage_key
            self.assertTrue(key.startswith(f"orgs/gcp-org/datasets/{ds.name}/inputs/gcp_"), key)
            self.assertEqual(store.objects[key], GCP_TEXT)
            self.assertEqual(store.content_types[key], "text/plain")
            self.assertEqual(assets.sync_inputs(ds), 0)  # idempotent

            # the private-file cache fill knows the URL
            self.assertEqual(cache.storage_key_for_file_url(ds.gcp_file), (key, self.org))

            # cache cold -> dispatch reads it from the bucket
            os.remove(abs_path_for_file_url(ds.gcp_file))
            name, source = assets.input_sources(task)[-1]
            self.assertEqual(name, "gcp_list.txt")
            with source as fh:
                self.assertEqual(fh.read(), GCP_TEXT)

            # delete goes by the recorded key; other objects are untouched
            store.objects["orgs/gcp-org/tasks/X/assets/orthophoto.tif"] = b"not ours"
            frappe.delete_doc("WebODM Task", task.name)
            frappe.delete_doc("WebODM Dataset", ds.name)
            self.assertEqual(sorted(store.objects), ["orgs/gcp-org/tasks/X/assets/orthophoto.tif"])

    def test_replacing_the_gcp_resyncs_and_drops_the_old_object(self):
        ds = self._with_gcp()
        with use_fake_storage() as store:
            assets.sync_inputs(ds)
            old_key = frappe.db.get_value("WebODM Dataset", ds.name, "gcp_storage_key")
            self._request(form={"name": ds.name}, gcp=_upload("v2.txt", GCP_TEXT_2, "text/plain"))
            with patch("webodm_core.datasets.schedule_input_sync"):
                dataset_api.set_gcp()
            self.assertNotIn(old_key, store.objects)
            doc = frappe.get_doc("WebODM Dataset", ds.name)
            self.assertFalse(doc.gcp_storage_key)  # pending re-sync
            assets.sync_inputs(doc)
            new_key = frappe.db.get_value("WebODM Dataset", ds.name, "gcp_storage_key")
            self.assertTrue(new_key.endswith("gcp_v2.txt"), new_key)
            self.assertEqual(store.objects[new_key], GCP_TEXT_2)

    def test_pending_sync_backfills_a_host_only_gcp(self):
        ds = self._with_gcp()
        with use_fake_storage() as store:
            # images already synced earlier, GCP attached while storage was down
            for row in ds.images:
                frappe.db.set_value("WebODM Dataset Image", row.name, "storage_key",
                                    f"orgs/gcp-org/datasets/{ds.name}/inputs/{row.filename}")
            stats = assets.sync_pending()
            self.assertGreaterEqual(stats["inputs"], 1)
            key = frappe.db.get_value("WebODM Dataset", ds.name, "gcp_storage_key")
            self.assertTrue(key and key in store.objects, key)

    def test_a_tampered_key_is_never_deleted(self):
        ds = self._with_gcp()
        with use_fake_storage() as store:
            foreign = "orgs/someone-else/datasets/x/inputs/gcp_list.txt"
            store.objects[foreign] = b"theirs"
            frappe.db.set_value("WebODM Dataset", ds.name, "gcp_storage_key", foreign)
            frappe.delete_doc("WebODM Dataset", ds.name)
            self.assertIn(foreign, store.objects)


class TestTaskUpload(_Base):
    def test_task_upload_takes_a_gcp_with_the_photos(self):
        self._request(files=[_upload("IMG_0001.JPG", tiny_jpeg()), _upload("IMG_0002.JPG", tiny_jpeg((2, 2, 2)))],
                      form={"project_id": self.project, "dataset_title": "Flight with GCP"},
                      gcp=_upload("gcp_list.txt", GCP_TEXT, "text/plain"))
        with patch.object(task_api, "_maybe_autostart"), patch("webodm_core.datasets.schedule_input_sync"):
            out = task_api.upload_images()
        self.assertEqual(out["dataset_summary"]["gcp"]["points"], 3)
        self.assertEqual(out["dataset_summary"]["gcp"]["unknown_images"], ["IMG_0009.JPG"])
        self.assertEqual(frappe.db.get_value("WebODM Dataset", out["dataset"], "gcp_filename"), "gcp_list.txt")

    def test_gcp_with_an_existing_dataset_is_refused(self):
        ds = make_dataset("Shared", n=1)
        self._request(form={"project_id": self.project, "dataset": ds.name},
                      gcp=_upload("gcp_list.txt", GCP_TEXT, "text/plain"))
        with self.assertRaises(frappe.ValidationError) as ctx:
            task_api.upload_images()
        self.assertIn("dataset page", str(ctx.exception))
        self.assertEqual(frappe.db.count("WebODM Task", {"dataset": ds.name}), 0)

    def test_bad_gcp_creates_neither_dataset_nor_task(self):
        self._request(files=[_upload("IMG_0001.JPG", tiny_jpeg())],
                      form={"project_id": self.project, "dataset_title": "Doomed"},
                      gcp=_upload("gcp_list.txt", b"garbage", "text/plain"))
        with self.assertRaises(datasets.GCPInvalid):
            task_api.upload_images()
        self.assertFalse(frappe.db.exists("WebODM Dataset", {"title": "Doomed"}))
        self.assertEqual(frappe.db.count("WebODM Task", {"project": self.project}), 0)
