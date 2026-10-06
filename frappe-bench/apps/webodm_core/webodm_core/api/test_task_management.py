"""Editing a task's settings after creation and duplicating a task.

* ``update_task`` changes title / processing options in Pending, Completed,
  Failed and Cancelled; it is refused in Queued, Provisioning and Running
  (the node has, or is about to get, the current options). It never starts
  or resets anything: outputs of the last run stay until the user re-runs.
* ``duplicate_task`` copies the inputs (dataset, hence images and ground
  control) and the settings into a fresh Pending task and nothing a run
  produced.
* Both are org-scoped like every other task endpoint, and both keep the
  options column in the delete-safe encoding on this database backend.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import task as task_api
from webodm_core.storage import assets
from webodm_core.testing import make_dataset

OPTS = [{"name": "dsm", "value": True}, {"name": "feature-quality", "value": "ultra"}]
NEW_OPTS = [{"name": "dsm", "value": False}, {"name": "feature-quality", "value": "high"},
            {"name": "orthophoto-resolution", "value": 2.5}]


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


def _body(**data):
    frappe.local.request = frappe._dict(data=json.dumps(data).encode(), files=None,
                                        environ={"REQUEST_METHOD": "POST"})
    frappe.local.form_dict = frappe._dict()


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = _user("taskmgmt_owner@example.com")
        cls.member = _user("taskmgmt_member@example.com")
        cls.outsider = _user("taskmgmt_outsider@example.com")
        cls.org = _org("TaskMgmt Org")
        cls.other_org = _org("TaskMgmt Other Org")
        _join(cls.owner, cls.org)
        _join(cls.member, cls.org, role="Member")
        _join(cls.outsider, cls.other_org)
        _as(cls.owner)
        cls.project = frappe.get_doc({"doctype": "WebODM Project", "title": "TaskMgmt Project"}).insert().name
        _as("Administrator")

    def setUp(self):
        _as(self.owner)
        patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)
        self._saved_request = getattr(frappe.local, "request", None)
        self.addCleanup(lambda: setattr(frappe.local, "request", self._saved_request))
        self.dataset = make_dataset("Task management inputs", n=1)

    def tearDown(self):
        _as("Administrator")
        for org in (self.org, self.other_org):
            for t in frappe.get_all("WebODM Task", filters={"organization": org}, pluck="name"):
                frappe.delete_doc("WebODM Task", t, force=True, ignore_permissions=True)
            for ds in frappe.get_all("WebODM Dataset", filters={"organization": org}, pluck="name"):
                frappe.delete_doc("WebODM Dataset", ds, force=True, ignore_permissions=True)

    def _task(self, title="Survey", status="Pending", options=OPTS, **fields):
        return frappe.get_doc({
            "doctype": "WebODM Task", "project": self.project, "title": title, "status": status,
            "dataset": self.dataset.name,
            "processing_options": task_api._encode_processing_options(json.dumps(options)) if options else None,
            **fields,
        }).insert()

    @staticmethod
    def _stored_options(name):
        return task_api._decode_processing_options(frappe.db.get_value("WebODM Task", name, "processing_options"))


class TestUpdateTask(_Base):
    def test_changes_title_and_options_of_a_pending_task(self):
        t = self._task()
        _body(task_name=t.name, title="  Survey v2 ", options=NEW_OPTS)
        out = task_api.update_task()
        self.assertEqual(out["title"], "Survey v2")
        self.assertEqual(out["options"], NEW_OPTS)
        self.assertTrue(out["can_edit"])
        self.assertEqual(frappe.db.get_value("WebODM Task", t.name, "title"), "Survey v2")
        self.assertEqual(self._stored_options(t.name), NEW_OPTS)
        # the column still holds the delete-safe scalar: a reload + delete works
        frappe.get_doc("WebODM Task", t.name)
        frappe.delete_doc("WebODM Task", t.name)

    def test_only_the_keys_sent_change(self):
        t = self._task()
        _body(task_name=t.name, title="Renamed")
        task_api.update_task()
        self.assertEqual(self._stored_options(t.name), OPTS)
        _body(task_name=t.name, options=NEW_OPTS)
        out = task_api.update_task()
        self.assertEqual(out["title"], "Renamed")
        self.assertEqual(self._stored_options(t.name), NEW_OPTS)

    def test_empty_options_means_engine_defaults(self):
        t = self._task()
        _body(task_name=t.name, options=[])
        out = task_api.update_task()
        self.assertEqual(out["options"], [])
        self.assertIsNone(frappe.db.get_value("WebODM Task", t.name, "processing_options"))

    def test_empty_title_is_refused(self):
        t = self._task()
        _body(task_name=t.name, title="   ")
        with self.assertRaises(frappe.ValidationError):
            task_api.update_task()
        self.assertEqual(frappe.db.get_value("WebODM Task", t.name, "title"), "Survey")

    def test_refused_while_queued_provisioning_or_running(self):
        for status in ("Queued", "Provisioning", "Running"):
            t = self._task(title=f"busy {status}", status=status)
            _body(task_name=t.name, title="nope", options=NEW_OPTS)
            with self.assertRaises(task_api.TaskBusy) as ctx:
                task_api.update_task()
            self.assertIn(status, str(ctx.exception))
            self.assertIn("cancel", str(ctx.exception).lower())
            self.assertEqual(frappe.db.get_value("WebODM Task", t.name, "title"), f"busy {status}")
            self.assertEqual(self._stored_options(t.name), OPTS)
            self.assertFalse(task_api._task_payload(t)["can_edit"])

    def test_allowed_after_a_run_and_outputs_stay_until_rerun(self):
        for status in ("Completed", "Failed", "Cancelled"):
            t = self._task(title=f"done {status}", status=status)
            t.db_set({"orthophoto": "/private/files/x_orthophoto.tif", "progress": 100, "epsg": 32633})
            _body(task_name=t.name, options=NEW_OPTS)
            out = task_api.update_task()
            self.assertEqual(out["status"], status)
            self.assertEqual(out["orthophoto"], "/private/files/x_orthophoto.tif")
            self.assertEqual(out["epsg"], 32633)
            self.assertEqual(self._stored_options(t.name), NEW_OPTS)

    def test_rerun_after_edit_uses_the_new_options(self):
        # process_task wipes the previous outputs and queues; dispatch then
        # reads the edited options (the same read-path _build_node_options sees).
        t = self._task(status="Completed")
        t.db_set({"orthophoto": "/private/files/x_orthophoto.tif", "progress": 100})
        _body(task_name=t.name, options=NEW_OPTS)
        task_api.update_task()
        _body(task_name=t.name)
        with patch("webodm_core.webodm_core.processing.task_runner.enqueue_process") as enq:
            task_api.process_task()
        enq.assert_called_once_with(t.name)
        t.reload()
        self.assertEqual(t.status, "Queued")
        self.assertFalse(t.orthophoto)
        from webodm_core.webodm_core.processing.task_runner import _build_node_options
        self.assertEqual(_build_node_options(task_api._decode_processing_options(t.processing_options)), NEW_OPTS)

    def test_org_scoped(self):
        t = self._task()
        _as(self.member)  # same org: may edit
        _body(task_name=t.name, title="by member")
        self.assertEqual(task_api.update_task()["title"], "by member")
        _as(self.outsider)
        _body(task_name=t.name, title="by outsider")
        with self.assertRaises(frappe.PermissionError):
            task_api.update_task()
        self.assertEqual(frappe.db.get_value("WebODM Task", t.name, "title"), "by member")

    def test_task_name_is_required(self):
        _body()
        with self.assertRaises(frappe.ValidationError):
            task_api.update_task()


class TestDuplicateTask(_Base):
    def _completed_source(self):
        t = self._task(title="Finished survey", status="Completed")
        t.db_set({
            "progress": 100, "orthophoto": "/private/files/src_orthophoto.tif", "dsm": "/private/files/src_dsm.tif",
            "orthophoto_extent": frappe.as_json({"type": "Polygon", "coordinates": []}), "epsg": 32633,
            "wkt": "PROJCS[...]", "node_task_id": "node-uuid", "dispatch_attempts": 3, "poll_failures": 2,
            "last_error": "an old error", "potree_status": "Ready", "resolution": 2.5,
        })
        assets.record_asset(t, "orthophoto", filename="orthophoto.tif", file_url="/private/files/src_orthophoto.tif",
                            storage_key="orgs/x/tasks/src/assets/orthophoto.tif")
        return frappe.get_doc("WebODM Task", t.name)

    def test_copies_inputs_and_settings_and_nothing_a_run_produced(self):
        src = self._completed_source()
        _body(task_name=src.name)
        out = task_api.duplicate_task()
        copy = frappe.get_doc("WebODM Task", out["name"])

        self.assertNotEqual(copy.name, src.name)
        self.assertEqual(copy.project, src.project)
        self.assertEqual(copy.dataset, src.dataset)
        self.assertEqual(copy.title, "Finished survey (copy)")
        self.assertEqual(copy.status, "Pending")
        self.assertEqual(self._stored_options(copy.name), OPTS)
        self.assertEqual(out["options"], OPTS)
        self.assertTrue(out["can_edit"])
        self.assertEqual(out["dataset_summary"]["name"], self.dataset.name)
        self.assertEqual(len(out["images"]), 1)

        for field in ("orthophoto", "dsm", "dtm", "point_cloud", "model", "orthophoto_extent", "wkt",
                      "node_task_id", "compute_instance", "last_error", "potree_status", "next_attempt_at"):
            self.assertFalse(copy.get(field), field)
        self.assertEqual((copy.progress or 0, copy.epsg or 0, copy.dispatch_attempts or 0, copy.poll_failures or 0),
                         (0, 0, 0, 0))
        self.assertEqual(copy.get("assets") or [], [])
        self.assertEqual(copy.get("raster_metadata") or [], [])
        self.assertFalse(copy.resolution)  # derived from the orthophoto of a run, so not a setting

        # the source is untouched and no dataset was copied
        src.reload()
        self.assertEqual((src.status, src.orthophoto, src.node_task_id), ("Completed", "/private/files/src_orthophoto.tif", "node-uuid"))
        self.assertEqual(len(src.assets), 1)
        self.assertEqual(frappe.db.count("WebODM Dataset", {"organization": self.org}), 1)

        # delete-safety of the copied options column on this backend
        frappe.get_doc("WebODM Task", copy.name)
        frappe.delete_doc("WebODM Task", copy.name)

    def test_title_suffix_increments_and_can_be_given(self):
        src = self._task(title="Flight")
        _body(task_name=src.name)
        self.assertEqual(task_api.duplicate_task()["title"], "Flight (copy)")
        _body(task_name=src.name)
        self.assertEqual(task_api.duplicate_task()["title"], "Flight (copy 2)")
        _body(task_name=src.name, title="  Flight, high quality ")
        self.assertEqual(task_api.duplicate_task()["title"], "Flight, high quality")
        self.assertEqual(task_api.duplicate_title("X", ["X (copy)", "X (copy 2)"]), "X (copy 3)")
        self.assertEqual(task_api.duplicate_title("", []), "Task (copy)")

    def test_works_from_any_status_without_options(self):
        for status in ("Pending", "Queued", "Running", "Failed", "Cancelled"):
            src = self._task(title=f"src {status}", status=status, options=None)
            _body(task_name=src.name)
            out = task_api.duplicate_task()
            self.assertEqual((out["status"], out["options"]), ("Pending", []))
            self.assertIsNone(frappe.db.get_value("WebODM Task", out["name"], "processing_options"))

    def test_org_scoped(self):
        src = self._task()
        _as(self.member)
        _body(task_name=src.name)
        self.assertEqual(task_api.duplicate_task()["organization"], self.org)
        _as(self.outsider)
        _body(task_name=src.name)
        with self.assertRaises(frappe.PermissionError):
            task_api.duplicate_task()
        _body(task_name="no-such-task")
        with self.assertRaises(frappe.DoesNotExistError):
            task_api.duplicate_task()

    def test_list_payload_reports_can_edit(self):
        a = self._task(title="editable", status="Pending")
        b = self._task(title="busy", status="Running")
        rows = {r["name"]: r for r in task_api.list_tasks(self.project)}
        self.assertTrue(rows[a.name]["can_edit"])
        self.assertFalse(rows[b.name]["can_edit"])


class TestDecodeProcessingOptions(FrappeTestCase):
    def test_decodes_every_encoding_level_and_legacy_dicts(self):
        d = task_api._decode_processing_options
        encoded = task_api._encode_processing_options(json.dumps(OPTS))
        self.assertEqual(d(encoded), OPTS)                       # as stored (MariaDB read)
        self.assertEqual(d(frappe.parse_json(encoded)), OPTS)    # once unwrapped (PostgreSQL read)
        self.assertEqual(d(OPTS), OPTS)                          # already a list
        self.assertEqual(d({"dsm": True}), [{"name": "dsm", "value": True}])  # legacy dict form
        self.assertEqual(d(None), [])
        self.assertEqual(d(""), [])
        self.assertEqual(d("not json"), [])
        self.assertEqual(d(42), [])
