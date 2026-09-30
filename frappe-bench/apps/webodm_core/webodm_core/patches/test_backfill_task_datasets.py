"""The staged dataset migration: backfill (additive, idempotent) then verified drop.

The legacy ``tabWebODM Task Image`` table no longer exists on a migrated
site, so the test recreates a minimal copy with plain SQL, seeds pre-library
tasks (rows written with ``db_insert`` to bypass the now-required ``dataset``)
and runs the real patch code against it.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.patches import backfill_task_datasets as backfill
from webodm_core.patches import drop_task_image_table as drop
from webodm_core.plugins.files import save_private_file_from_stream

LEGACY_COLUMNS = """
    "name" varchar(140) primary key, "creation" timestamp, "modified" timestamp, "modified_by" varchar(140),
    "owner" varchar(140), "docstatus" smallint default 0, "idx" bigint default 0,
    "parent" varchar(140), "parentfield" varchar(140), "parenttype" varchar(140),
    "image" text, "filename" varchar(140), "file_size" bigint, "storage_key" varchar(1024),
    "latitude" decimal(21,9), "longitude" decimal(21,9), "altitude" decimal(21,9), "capture_time" timestamp
"""


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": "m", "send_welcome_email": 0}
                       ).insert(ignore_permissions=True)
        u = frappe.get_doc("User", email)
        u.append("roles", {"role": "WebODM User"})
        u.save(ignore_permissions=True)
    return email


class TestBackfillTaskDatasets(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = _user("migration_owner@example.com")
        cls.org = frappe.db.get_value("WebODM Organization", {"organization_name": "Migration Org"}, "name") or \
            frappe.get_doc({"doctype": "WebODM Organization", "organization_name": "Migration Org"}
                           ).insert(ignore_permissions=True).name
        if not frappe.db.exists("WebODM Org Membership", {"user": cls.user, "organization": cls.org}):
            frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.user, "organization": cls.org,
                            "role": "Owner"}).insert(ignore_permissions=True)
        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.user)
        cls.project = frappe.db.get_value("WebODM Project", {"title": "Migration Project"}, "name") or \
            frappe.get_doc({"doctype": "WebODM Project", "title": "Migration Project"}).insert().name
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        frappe.delete_doc("WebODM Project", cls.project, force=True, ignore_permissions=True)
        super().tearDownClass()

    def setUp(self):
        # A migrated site has no legacy table; a crashed earlier run may have left ours.
        frappe.db.sql_ddl(f'drop table if exists "{backfill.LEGACY_TABLE}"')
        frappe.db.sql_ddl(f'create table "{backfill.LEGACY_TABLE}" ({LEGACY_COLUMNS})')
        frappe.db.get_tables(cached=False)
        frappe.flags.in_patch = True
        patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)
        self.tasks = []

    def tearDown(self):
        frappe.flags.in_patch = False
        frappe.set_user("Administrator")
        for name in self.tasks:
            if frappe.db.exists("WebODM Task", name):
                frappe.delete_doc("WebODM Task", name, force=True, ignore_permissions=True)
        for ds in frappe.get_all("WebODM Dataset", filters={"organization": self.org}, pluck="name"):
            frappe.delete_doc("WebODM Dataset", ds, force=True, ignore_permissions=True)
        frappe.db.sql_ddl(f'drop table if exists "{backfill.LEGACY_TABLE}"')
        frappe.db.get_tables(cached=False)

    # -- fixtures -------------------------------------------------------------

    def _legacy_task(self, title, images, with_output=True):
        """A pre-library task: no dataset, image rows in the legacy table, Files attached to the task."""
        task = frappe.get_doc({"doctype": "WebODM Task", "project": self.project, "title": title,
                               "status": "Completed", "organization": self.org, "owner": self.user,
                               "creation": frappe.utils.now(), "modified": frappe.utils.now()})
        task.set_new_name()
        task.db_insert()  # no insert(): `dataset` is mandatory now, legacy rows never had it
        self.tasks.append(task.name)
        if with_output:
            out = save_private_file_from_stream(io.BytesIO(b"II*\x00ortho"), f"{task.name}_orthophoto.tif",
                                                attached_to_doctype="WebODM Task", attached_to_name=task.name,
                                                ignore_permissions=True)
            frappe.db.set_value("WebODM Task", task.name, "orthophoto", out.file_url, update_modified=False)
        for idx, (filename, key) in enumerate(images, start=1):
            f = save_private_file_from_stream(io.BytesIO(b"\xff\xd8" + filename.encode()), filename,
                                              attached_to_doctype="WebODM Task", attached_to_name=task.name,
                                              ignore_permissions=True)
            frappe.db.sql(
                f'''insert into "{backfill.LEGACY_TABLE}"
                    ("name", "creation", "modified", "owner", "idx", "parent", "parentfield", "parenttype",
                     "image", "filename", "file_size", "storage_key", "latitude", "longitude", "altitude", "capture_time")
                    values (%s, now(), now(), %s, %s, %s, 'images', 'WebODM Task', %s, %s, %s, %s, %s, %s, %s, %s)''',
                (frappe.generate_hash(length=10), self.user, idx, task.name, f.file_url, filename, f.file_size,
                 key, 40.0 + idx, -73.0 - idx, 120.5, "2024-05-01 10:20:30"),
            )
        return task.name

    # -- tests ----------------------------------------------------------------

    def test_backfill_creates_one_dataset_per_task_and_keeps_keys_and_files(self):
        a = self._legacy_task("Flight A", [("DJI_0001.JPG", None),
                                           ("DJI_0002.JPG", "orgs/migration-org/tasks/OLD/inputs/DJI_0002.JPG")])
        b = self._legacy_task("Flight B", [("DJI_0003.JPG", None)])
        empty = self._legacy_task("Never uploaded", [], with_output=False)

        report = backfill.report()
        self.assertEqual(report["tasks_without_dataset"], 3)
        stats = backfill.execute()
        self.assertEqual((stats["tasks"], stats["images"]), (3, 3))

        ds_a = frappe.get_doc("WebODM Dataset", frappe.db.get_value("WebODM Task", a, "dataset"))
        self.assertEqual(ds_a.title, "Flight A")
        self.assertEqual(ds_a.organization, self.org)
        self.assertEqual((ds_a.created_by, ds_a.owner), (self.user, self.user))
        self.assertEqual(ds_a.description, f"Migrated from task {a}")
        self.assertEqual(ds_a.image_count, 2)
        rows = {r.filename: r for r in ds_a.images}
        self.assertEqual(rows["DJI_0002.JPG"].storage_key, "orgs/migration-org/tasks/OLD/inputs/DJI_0002.JPG")
        self.assertIsNone(rows["DJI_0001.JPG"].storage_key)
        self.assertEqual((float(rows["DJI_0001.JPG"].latitude), float(rows["DJI_0001.JPG"].longitude)), (41.0, -74.0))
        self.assertEqual(float(rows["DJI_0001.JPG"].altitude), 120.5)
        self.assertEqual(str(rows["DJI_0001.JPG"].capture_time), "2024-05-01 10:20:30")

        # image Files moved to the dataset; the output File stayed on the task
        attached = frappe.get_all("File", filters={"attached_to_name": ds_a.name}, pluck="file_url")
        self.assertEqual(sorted(attached), sorted(r.image for r in ds_a.images))
        self.assertEqual(frappe.db.get_value("File", {"attached_to_doctype": "WebODM Task", "attached_to_name": a},
                                             "file_url"), frappe.db.get_value("WebODM Task", a, "orthophoto"))

        ds_b = frappe.db.get_value("WebODM Task", b, "dataset")
        self.assertNotEqual(ds_a.name, ds_b)
        # the legacy task without images still gets a (legally empty) dataset
        ds_empty = frappe.get_doc("WebODM Dataset", frappe.db.get_value("WebODM Task", empty, "dataset"))
        self.assertEqual(ds_empty.image_count, 0)
        ds_empty.title = "renamed anyway"
        ds_empty.save(ignore_permissions=True)  # editable, like any other dataset

        # the legacy rows are untouched (additive step)
        self.assertEqual(len(backfill.legacy_rows(a)), 2)

    def test_backfill_is_idempotent(self):
        a = self._legacy_task("Once", [("DJI_0001.JPG", None)])
        self.assertEqual(backfill.execute()["tasks"], 1)
        before = frappe.db.get_value("WebODM Task", a, "dataset")
        self.assertEqual(backfill.execute(), {"tasks": 0, "images": 0, "skipped": 0})
        self.assertEqual(frappe.db.get_value("WebODM Task", a, "dataset"), before)
        self.assertEqual(frappe.db.count("WebODM Dataset", {"organization": self.org}), 1)

    def test_rollback_reattaches_files_to_the_task(self):
        a = self._legacy_task("Back", [("DJI_0001.JPG", None)])
        backfill.execute()
        ds = frappe.db.get_value("WebODM Task", a, "dataset")
        self.assertEqual(backfill.rollback([ds]), {"datasets": 1, "files": 1})
        self.assertEqual(frappe.db.count("File", {"attached_to_doctype": "WebODM Task", "attached_to_name": a}), 2)
        self.assertEqual(frappe.db.count("File", {"attached_to_name": ds}), 0)

    def test_drop_refuses_until_the_backfill_is_complete_then_drops(self):
        a = self._legacy_task("Verified", [("DJI_0001.JPG", None), ("DJI_0002.JPG", None)])
        # nothing migrated yet: refuse, table intact
        with self.assertRaises(drop.MigrationIncomplete) as ctx:
            drop.execute()
        self.assertIn(a, str(ctx.exception))
        self.assertTrue(backfill.legacy_table_exists())

        backfill.execute()
        # a dataset that lost a row (or a task pointed at the wrong dataset) is caught too
        ds = frappe.db.get_value("WebODM Task", a, "dataset")
        victim = frappe.get_all("WebODM Dataset Image", filters={"parent": ds}, pluck="name")[0]
        frappe.db.delete("WebODM Dataset Image", {"name": victim})
        frappe.db.set_value("WebODM Dataset", ds, "image_count", 1, update_modified=False)
        problems = drop.verify()
        self.assertTrue(any("2 legacy image row(s) but dataset" in p for p in problems), problems)
        frappe.db.set_value("WebODM Dataset", ds, "image_count", 2, update_modified=False)
        frappe.get_doc({"doctype": "WebODM Dataset Image", "parent": ds, "parenttype": "WebODM Dataset",
                        "parentfield": "images", "idx": 3, "image": "/private/files/x.jpg", "filename": "x.jpg"}
                       ).db_insert()

        self.assertEqual(drop.verify(), [])
        with patch("frappe.delete_doc"):  # the DocType row is gone on a migrated site already
            drop.execute()
        self.assertFalse(backfill.legacy_table_exists())
        # nothing left to do, nothing to fail on
        drop.execute()
        self.assertEqual(backfill.execute(), {"tasks": 0, "images": 0, "skipped": 0})
