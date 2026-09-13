"""Tests for plugin run eligibility, parameters, queueing, worker, cancel."""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import plugins as plugins_api
from webodm_core.plugins import runner

PLUGIN_ID = "test-run-op"
SCHEMA = {
    "type": "object",
    "properties": {"interval_m": {"type": "number", "exclusiveMinimum": 0}},
    "required": ["interval_m"],
}
INPUTS = [{"name": "raster", "datasets": ["dsm", "dtm"]}]


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": email.split("@")[0],
            "send_welcome_email": 0,
        }).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email)
    u.roles = []
    u.append("roles", {"role": "WebODM User"})
    u.save(ignore_permissions=True)
    return email


def _org(name):
    existing = frappe.db.get_value("WebODM Organization", {"organization_name": name}, "name")
    if existing:
        return existing
    return frappe.get_doc({
        "doctype": "WebODM Organization",
        "organization_name": name,
    }).insert(ignore_permissions=True).name


def _join(user, org, role="Owner"):
    if frappe.db.exists("WebODM Org Membership", {"user": user, "organization": org}):
        return
    frappe.get_doc({"doctype": "WebODM Org Membership", "user": user,
                    "organization": org, "role": role}).insert(ignore_permissions=True)


class TestPluginRun(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._cleanup()
        frappe.get_doc({
            "doctype": "WebODM Plugin",
            "plugin_id": PLUGIN_ID,
            "label": "Run Test Op",
            "version": "1.0.0",
            "output_kind": "raster",
            "render_kind": "dem",
            "platform_enabled": 1,
            "available": 1,
            "params_schema": json.dumps(SCHEMA),
            "inputs": json.dumps(INPUTS),
        }).insert(ignore_permissions=True)

        cls.owner = _user("run_owner@example.com")
        cls.outsider = _user("run_outsider@example.com")
        cls.org = _org("Plugin Run Org")
        cls.org_other = _org("Plugin Run Other Org")
        _join(cls.owner, cls.org)
        _join(cls.outsider, cls.org_other)

        cls.project = cls._project(cls.owner, "Plugin Run Project")
        cls.task_ok = cls._task(cls.owner, cls.project, "Completed", with_dsm=True)
        cls.task_noinput = cls._task(cls.owner, cls.project, "Completed", with_dsm=False)
        cls.task_pending = cls._task(cls.owner, cls.project, "Pending", with_dsm=True)

        other_project = cls._project(cls.outsider, "Plugin Run Other Project")
        cls.task_other = cls._task(cls.outsider, other_project, "Completed", with_dsm=True)

        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        cls._cleanup()
        frappe.delete_doc("WebODM Plugin", PLUGIN_ID, force=True, ignore_permissions=True)
        super().tearDownClass()

    @classmethod
    def _cleanup(cls):
        for name in frappe.get_all("WebODM Plugin Run", filters={"plugin": PLUGIN_ID}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("WebODM Plugin Setting", filters={"plugin": PLUGIN_ID}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Setting", name, force=True, ignore_permissions=True)

    @classmethod
    def _project(cls, user, title):
        existing = frappe.db.get_value("WebODM Project", {"title": title}, "name")
        if existing:
            return existing
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}
        doc = frappe.get_doc({"doctype": "WebODM Project", "title": title}).insert()
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        return doc.name

    @classmethod
    def _task(cls, user, project, status, with_dsm):
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}
        doc = frappe.get_doc({
            "doctype": "WebODM Task",
            "project": project,
            "title": f"Task {status} {with_dsm}",
            "status": status,
        }).insert()
        if with_dsm:
            f = frappe.get_doc({
                "doctype": "File",
                "file_name": f"{doc.name}_dsm.tif",
                "is_private": 1,
                "content": b"fake-dsm-bytes",
                "attached_to_doctype": "WebODM Task",
                "attached_to_name": doc.name,
            }).save(ignore_permissions=True)
            doc.db_set("dsm", f.file_url)
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        return doc.name

    def setUp(self):
        for name in frappe.get_all("WebODM Plugin Run", filters={"plugin": PLUGIN_ID}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("WebODM Plugin Setting", filters={"plugin": PLUGIN_ID}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Setting", name, force=True, ignore_permissions=True)
        frappe.db.set_value("WebODM Plugin", PLUGIN_ID, {"platform_enabled": 1, "available": 1})
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def _as(self, user):
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}

    def _enable(self, org=None, enabled=1, settings=None):
        org = org or self.org
        payload = {"plugin": PLUGIN_ID, "enabled": enabled}
        if settings is not None:
            payload["settings"] = settings
        # save_plugin_setting uses the acting user's org; set one that owns it.
        prior = frappe.session.user
        frappe.set_user(self.owner if org == self.org else self.outsider)
        frappe.local.webodm_org_cache = {}
        try:
            plugins_api.save_plugin_setting(**payload)
        finally:
            frappe.set_user(prior)
            frappe.local.webodm_org_cache = {}

    def _run(self):
        return plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_ok)

    # --- eligibility (4.3) ---

    def test_eligible_run_is_queued_and_enqueued(self):
        self._enable(settings={"interval_m": 5})
        self._as(self.owner)
        calls = []
        with patch.object(frappe, "enqueue", lambda *a, **k: calls.append((a, k))):
            result = plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_ok)
        self.assertEqual(result["status"], "Queued")
        self.assertTrue(frappe.db.exists("WebODM Plugin Run", result["run"]))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0], "webodm_core.plugins.runner.execute_run")
        self.assertEqual(calls[0][1]["run_name"], result["run"])

    def test_not_enabled_for_org_rejected(self):
        self._as(self.owner)
        with self.assertRaises(frappe.PermissionError):
            self._run()

    def test_platform_disabled_rejected(self):
        self._enable(settings={"interval_m": 5})
        frappe.db.set_value("WebODM Plugin", PLUGIN_ID, "platform_enabled", 0)
        self._as(self.owner)
        with self.assertRaises(frappe.PermissionError):
            self._run()

    def test_task_not_completed_rejected(self):
        self._enable(settings={"interval_m": 5})
        self._as(self.owner)
        with self.assertRaises(frappe.ValidationError):
            plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_pending)

    def test_cross_org_task_rejected(self):
        self._enable(org=self.org_other, settings={"interval_m": 5})
        self._as(self.outsider)
        with self.assertRaises(frappe.PermissionError):
            plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_ok)

    def test_missing_required_input_rejected(self):
        self._enable(settings={"interval_m": 5})
        self._as(self.owner)
        with self.assertRaises(frappe.ValidationError):
            plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_noinput)

    # --- parameters (4.4) ---

    def test_defaults_and_overrides_are_merged(self):
        self._enable(settings={"interval_m": 5})
        self._as(self.owner)
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            default = plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_ok)
            override = plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_ok,
                                              params={"interval_m": 9})
        p1 = json.loads(frappe.get_doc("WebODM Plugin Run", default["run"]).parameters)["params"]
        p2 = json.loads(frappe.get_doc("WebODM Plugin Run", override["run"]).parameters)["params"]
        self.assertEqual(p1["interval_m"], 5)
        self.assertEqual(p2["interval_m"], 9)

    def test_invalid_override_rejected_without_run(self):
        self._enable(settings={"interval_m": 5})
        self._as(self.owner)
        before = frappe.db.count("WebODM Plugin Run", {"plugin": PLUGIN_ID})
        with self.assertRaises(frappe.ValidationError):
            plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_ok, params={"interval_m": -3})
        self.assertEqual(frappe.db.count("WebODM Plugin Run", {"plugin": PLUGIN_ID}), before)

    # --- worker (4.6) ---

    def _fake_operation(self, op_id, inputs, params, output_path, timeout=600):
        with open(output_path, "wb") as f:
            f.write(b"fake-output")
        return {
            "op_id": op_id,
            "output_kind": "raster",
            "render_kind": "dem",
            "output_path": output_path,
            "metadata": {"extent": {"type": "Polygon", "coordinates": []}, "epsg": 32615},
        }

    def test_worker_completes_and_attaches_output(self):
        self._enable(settings={"interval_m": 5})
        self._as(self.owner)
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            run_name = plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_ok)["run"]

        with patch.object(runner, "run_operation", self._fake_operation):
            runner.execute_run(run_name)

        run = frappe.get_doc("WebODM Plugin Run", run_name)
        self.assertEqual(run.status, "Completed")
        self.assertEqual(run.progress, 100)
        self.assertTrue(run.output_file)
        self.assertTrue(frappe.db.exists("File", {"file_url": run.output_file}))
        self.assertEqual(json.loads(run.output_metadata)["epsg"], 32615)

    def test_worker_failure_marks_failed_with_error(self):
        self._enable(settings={"interval_m": 5})
        self._as(self.owner)
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            run_name = plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_ok)["run"]

        def _boom(*a, **k):
            raise RuntimeError("geospatial exploded")

        with patch.object(runner, "run_operation", _boom):
            runner.execute_run(run_name)

        run = frappe.get_doc("WebODM Plugin Run", run_name)
        self.assertEqual(run.status, "Failed")
        self.assertIn("geospatial exploded", run.error)
        self.assertFalse(run.output_file)

    # --- cancel (4.7) ---

    def test_cancel_marks_cancelled_without_output(self):
        self._enable(settings={"interval_m": 5})
        self._as(self.owner)
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            run_name = plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_ok)["run"]

        plugins_api.cancel_run(run_name)
        run = frappe.get_doc("WebODM Plugin Run", run_name)
        self.assertEqual(run.status, "Cancelled")
        self.assertFalse(run.output_file)

        # A cancelled run that reaches the worker must not execute.
        from unittest.mock import Mock

        mocked = Mock(side_effect=self._fake_operation)
        with patch.object(runner, "run_operation", mocked):
            runner.execute_run(run_name)
            mocked.assert_not_called()
        self.assertEqual(
            frappe.db.get_value("WebODM Plugin Run", run_name, "status"), "Cancelled"
        )

    def test_cancel_while_running(self):
        self._enable(settings={"interval_m": 5})
        self._as(self.owner)
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            run_name = plugins_api.run_plugin(plugin=PLUGIN_ID, task=self.task_ok)["run"]
        frappe.db.set_value("WebODM Plugin Run", run_name, "status", "Running")
        plugins_api.cancel_run(run_name)
        self.assertEqual(
            frappe.db.get_value("WebODM Plugin Run", run_name, "status"), "Cancelled"
        )
