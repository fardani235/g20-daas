"""Deleting a task or a project must cascade to what links to them.

A plugin run points at its task, so ``frappe.delete_doc("WebODM Task")`` used
to fail with ``LinkExistsError`` (HTTP 417) for any task that had been
analysed by a plugin, and a project delete failed the same way once its task
deletes failed. The cascade lives in the doctype controllers so every client
(REST resource API, map page, Projects page) gets it.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.webodm_core.doctype.webodm_plugin_run import webodm_plugin_run as run_module

PLUGIN_ID = "test-deletion-op"


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({
            "doctype": "User", "email": email,
            "first_name": email.split("@")[0], "send_welcome_email": 0,
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
    return frappe.get_doc({"doctype": "WebODM Organization", "organization_name": name}
                          ).insert(ignore_permissions=True).name


def _join(user, org, role="Owner"):
    if frappe.db.exists("WebODM Org Membership", {"user": user, "organization": org}):
        return
    frappe.get_doc({"doctype": "WebODM Org Membership", "user": user,
                    "organization": org, "role": role}).insert(ignore_permissions=True)


class TestDeletionCascade(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("WebODM Plugin", PLUGIN_ID):
            frappe.get_doc({
                "doctype": "WebODM Plugin", "plugin_id": PLUGIN_ID, "label": "Deletion Test Op",
                "version": "1.0.0", "output_kind": "vector", "render_kind": "detections",
                "platform_enabled": 1, "available": 1,
                "params_schema": json.dumps({"type": "object", "properties": {}}),
                "inputs": json.dumps([{"name": "orthophoto", "datasets": ["orthophoto"]}]),
            }).insert(ignore_permissions=True)
        cls.owner = _user("deletion_owner@example.com")
        cls.org = _org("Deletion Cascade Org")
        _join(cls.owner, cls.org)
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        for name in frappe.get_all("WebODM Plugin Run", filters={"plugin": PLUGIN_ID}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("WebODM Project", filters={"title": ["like", "Deletion Cascade%"]}, pluck="name"):
            for task in frappe.get_all("WebODM Task", filters={"project": name}, pluck="name"):
                frappe.delete_doc("WebODM Task", task, force=True, ignore_permissions=True)
            frappe.delete_doc("WebODM Project", name, force=True, ignore_permissions=True)
        frappe.delete_doc("WebODM Plugin", PLUGIN_ID, force=True, ignore_permissions=True)
        super().tearDownClass()

    def setUp(self):
        frappe.set_user(self.owner)
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    # -- helpers ---------------------------------------------------------------

    def _project(self, title):
        return frappe.get_doc({"doctype": "WebODM Project", "title": title}).insert().name

    def _task(self, project, title="Analysed task"):
        return frappe.get_doc({"doctype": "WebODM Task", "project": project, "title": title,
                               "status": "Completed"}).insert().name

    def _run(self, task, status="Completed"):
        run = frappe.get_doc({
            "doctype": "WebODM Plugin Run", "plugin": PLUGIN_ID, "task": task, "status": status,
            "parameters": json.dumps({"params": {}, "inputs": {"orthophoto": "orthophoto"}}),
            "output_kind": "vector", "render_kind": "detections",
        }).insert()
        if status == "Completed":
            f = frappe.get_doc({
                "doctype": "File", "file_name": f"{run.name}.geojson", "is_private": 1,
                "content": json.dumps({"type": "FeatureCollection", "features": [],
                                       "run": run.name}).encode(),
                "attached_to_doctype": "WebODM Plugin Run", "attached_to_name": run.name,
            }).save(ignore_permissions=True)
            run.db_set("output_file", f.file_url)
        return run.name

    # -- tests -----------------------------------------------------------------

    def test_task_with_plugin_runs_is_deletable(self):
        project = self._project("Deletion Cascade Project A")
        task = self._task(project)
        runs = [self._run(task), self._run(task, status="Failed")]
        self.assertTrue(frappe.db.exists("WebODM Plugin Run", runs[0]))

        with patch.object(run_module.WebODMPluginRun, "on_trash", autospec=True) as cleanup:
            frappe.delete_doc("WebODM Task", task)

        self.assertFalse(frappe.db.exists("WebODM Task", task))
        for name in runs:
            self.assertFalse(frappe.db.exists("WebODM Plugin Run", name))
        # each run cleaned up its own stored outputs
        self.assertEqual(sorted(call.args[0].name for call in cleanup.call_args_list), sorted(runs))
        # ...and Frappe dropped the output attachments with the runs
        self.assertEqual(frappe.get_all("File", filters={"attached_to_doctype": "WebODM Plugin Run",
                                                          "attached_to_name": ["in", runs]}), [])

    def test_task_delete_removes_run_outputs_for_real(self):
        project = self._project("Deletion Cascade Project B")
        task = self._task(project)
        run = self._run(task)
        file_name = frappe.db.get_value("File", {"attached_to_doctype": "WebODM Plugin Run",
                                                 "attached_to_name": run}, "name")
        self.assertTrue(file_name)

        frappe.delete_doc("WebODM Task", task)

        self.assertFalse(frappe.db.exists("WebODM Plugin Run", run))
        self.assertFalse(frappe.db.exists("File", file_name))

    def test_project_with_analysed_tasks_is_deletable(self):
        project = self._project("Deletion Cascade Project C")
        tasks = [self._task(project, "one"), self._task(project, "two")]
        runs = [self._run(tasks[0]), self._run(tasks[1]), self._run(tasks[1], status="Cancelled")]

        frappe.delete_doc("WebODM Project", project)

        self.assertFalse(frappe.db.exists("WebODM Project", project))
        for name in tasks:
            self.assertFalse(frappe.db.exists("WebODM Task", name))
        for name in runs:
            self.assertFalse(frappe.db.exists("WebODM Plugin Run", name))

    def test_task_without_runs_still_deletes(self):
        project = self._project("Deletion Cascade Project D")
        task = self._task(project)
        frappe.delete_doc("WebODM Task", task)
        self.assertFalse(frappe.db.exists("WebODM Task", task))

    def test_deleting_a_task_leaves_sibling_tasks_and_their_runs(self):
        project = self._project("Deletion Cascade Project E")
        doomed, kept = self._task(project, "doomed"), self._task(project, "kept")
        self._run(doomed)
        kept_run = self._run(kept)

        frappe.delete_doc("WebODM Task", doomed)

        self.assertTrue(frappe.db.exists("WebODM Task", kept))
        self.assertTrue(frappe.db.exists("WebODM Plugin Run", kept_run))
        self.assertEqual(frappe.get_all("WebODM Plugin Run", filters={"task": doomed}), [])
