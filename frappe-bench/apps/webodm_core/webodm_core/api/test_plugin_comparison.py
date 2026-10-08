"""Comparison plugins: a run that reads a second survey ("reference task").

Covers the manifest (``"survey": "reference"`` inputs), who may compare what
(same organization, same project, readable, completed, not itself), input
resolution against the right survey, what the worker stages and tells the
plugin, the replace/active rules per pair, and the deletion cascade. The
isolation rules are tested at every layer that enforces them: the run API,
the run document itself and the worker.
"""

import json
import os
import tempfile
import zipfile
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import plugins as plugins_api
from webodm_core.plugins import package as package_mod
from webodm_core.plugins import runner
from webodm_core.testing import make_dataset

PLUGIN_ID = "test-compare-op"
PLAIN_ID = "test-compare-plain-op"
INPUTS = [
    {"name": "surface", "datasets": ["dsm", "dtm"]},
    {"name": "reference_surface", "datasets": ["dsm", "dtm"], "survey": "reference"},
    {"name": "orthophoto", "datasets": ["orthophoto"], "optional": True},
    {"name": "reference_orthophoto", "datasets": ["orthophoto"], "survey": "reference", "optional": True},
]
MANIFEST = {
    "id": "compare-two",
    "version": "1.0.0",
    "entrypoint": "main.py",
    "inputs": INPUTS,
    "output_kind": "vector",
    "render_kind": "change",
}


def _zip(manifest) -> str:
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("main.py", "print('hi')\n")
    return path


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({
            "doctype": "User", "email": email, "first_name": email.split("@")[0],
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
    return frappe.get_doc({"doctype": "WebODM Organization", "organization_name": name}).insert(
        ignore_permissions=True
    ).name


def _join(user, org, role="Owner"):
    if frappe.db.exists("WebODM Org Membership", {"user": user, "organization": org}):
        return
    frappe.get_doc({"doctype": "WebODM Org Membership", "user": user,
                    "organization": org, "role": role}).insert(ignore_permissions=True)


class TestComparisonManifest(FrappeTestCase):
    def _inspect(self, manifest):
        path = _zip(manifest)
        try:
            return package_mod.inspect_package(path)
        finally:
            os.remove(path)

    def test_reference_inputs_are_kept_and_primary_is_the_default(self):
        m = self._inspect({**MANIFEST, "inputs": [
            {"name": "surface", "datasets": ["dsm"], "survey": "primary"},
            {"name": "reference_surface", "datasets": ["dsm"], "survey": "reference", "label": "Before"},
        ]})
        self.assertEqual(m["inputs"], [
            {"name": "surface", "datasets": ["dsm"]},
            {"name": "reference_surface", "datasets": ["dsm"], "label": "Before", "survey": "reference"},
        ])
        self.assertEqual(package_mod.reference_input_names(m["inputs"]), ["reference_surface"])
        self.assertEqual(package_mod.reference_input_names([{"name": "raster", "datasets": ["dsm"]}]), [])
        self.assertEqual(package_mod.reference_input_names(None), [])

    def test_unknown_survey_is_rejected(self):
        for bad in ("before", "secondary", "", 2, None):
            with self.assertRaises(package_mod.PackageError) as ctx:
                self._inspect({**MANIFEST, "inputs": [{"name": "surface", "datasets": ["dsm"], "survey": bad}]})
            self.assertIn("'survey'", str(ctx.exception))


class TestPluginComparison(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._cleanup()
        for plugin_id, inputs in ((PLUGIN_ID, INPUTS), (PLAIN_ID, [{"name": "raster", "datasets": ["dsm"]}])):
            frappe.get_doc({
                "doctype": "WebODM Plugin", "plugin_id": plugin_id, "label": plugin_id, "version": "1.0.0",
                "output_kind": "raster", "render_kind": "dem", "platform_enabled": 1, "available": 1,
                "params_schema": json.dumps({"type": "object", "properties": {}}),
                "inputs": json.dumps(inputs),
            }).insert(ignore_permissions=True)

        cls.owner = _user("compare_owner@example.com")
        cls.outsider = _user("compare_outsider@example.com")
        cls.org = _org("Plugin Compare Org")
        cls.org_other = _org("Plugin Compare Other Org")
        _join(cls.owner, cls.org)
        _join(cls.outsider, cls.org_other)

        cls.project = cls._project(cls.owner, "Plugin Compare Project")
        cls.after = cls._task(cls.owner, cls.project, "October", dsm=True, orthophoto=True)
        cls.before = cls._task(cls.owner, cls.project, "September", dsm=True, dtm=True)
        cls.august = cls._task(cls.owner, cls.project, "August", dsm=True)
        cls.no_surface = cls._task(cls.owner, cls.project, "No surface", orthophoto=True)
        cls.pending = cls._task(cls.owner, cls.project, "Pending", status="Pending", dsm=True)
        cls.elsewhere = cls._task(cls.owner, cls._project(cls.owner, "Plugin Compare Second Project"),
                                  "Another site", dsm=True)
        cls.other_project = cls._project(cls.outsider, "Plugin Compare Other Org Project")
        cls.foreign = cls._task(cls.outsider, cls.other_project, "Foreign", dsm=True)
        cls.foreign_second = cls._task(cls.outsider, cls.other_project, "Foreign second", dsm=True)

        for user in (cls.owner, cls.outsider):
            frappe.set_user(user)
            frappe.local.webodm_org_cache = {}
            for plugin_id in (PLUGIN_ID, PLAIN_ID):
                plugins_api.save_plugin_setting(plugin=plugin_id, enabled=1)
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        cls._cleanup()
        super().tearDownClass()

    @classmethod
    def _cleanup(cls):
        frappe.set_user("Administrator")
        for plugin_id in (PLUGIN_ID, PLAIN_ID):
            for name in frappe.get_all("WebODM Plugin Run", filters={"plugin": plugin_id}, pluck="name"):
                frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
            for name in frappe.get_all("WebODM Plugin Setting", filters={"plugin": plugin_id}, pluck="name"):
                frappe.delete_doc("WebODM Plugin Setting", name, force=True, ignore_permissions=True)
            if frappe.db.exists("WebODM Plugin", plugin_id):
                frappe.delete_doc("WebODM Plugin", plugin_id, force=True, ignore_permissions=True)
        for project in frappe.get_all("WebODM Project", filters={"title": ["like", "Plugin Compare%"]}, pluck="name"):
            for task in frappe.get_all("WebODM Task", filters={"project": project}, pluck="name"):
                frappe.delete_doc("WebODM Task", task, force=True, ignore_permissions=True)
            frappe.delete_doc("WebODM Project", project, force=True, ignore_permissions=True)

    @classmethod
    def _project(cls, user, title):
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}
        doc = frappe.get_doc({"doctype": "WebODM Project", "title": title}).insert()
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        return doc.name

    @classmethod
    def _task(cls, user, project, title, status="Completed", **datasets):
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}
        doc = frappe.get_doc({
            "doctype": "WebODM Task", "dataset": make_dataset().name, "project": project,
            "title": title, "status": status,
        }).insert()
        for field, present in datasets.items():
            if not present:
                continue
            f = frappe.get_doc({
                "doctype": "File", "file_name": f"{doc.name}_{field}.tif", "is_private": 1,
                # unique per (task, field): Frappe dedups identical file content
                "content": f"fake-{field}-{doc.name}".encode(),
                "attached_to_doctype": "WebODM Task", "attached_to_name": doc.name,
            }).save(ignore_permissions=True)
            doc.db_set(field, f.file_url)
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        return doc.name

    def setUp(self):
        frappe.set_user("Administrator")
        for plugin_id in (PLUGIN_ID, PLAIN_ID):
            for name in frappe.get_all("WebODM Plugin Run", filters={"plugin": plugin_id}, pluck="name"):
                frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
        frappe.db.set_value("WebODM Plugin", PLUGIN_ID, "inputs", json.dumps(INPUTS))
        self._as(self.owner)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def _as(self, user):
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}

    def _run(self, task=None, reference=None, plugin=PLUGIN_ID, **payload):
        if reference is not None:
            payload["reference_task"] = reference
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            return plugins_api.run_plugin(plugin=plugin, task=task or self.after, **payload)["run"]

    def _stored(self, run_name):
        return json.loads(frappe.db.get_value("WebODM Plugin Run", run_name, "parameters"))

    def _run_count(self):
        return frappe.db.count("WebODM Plugin Run", {"plugin": ["in", (PLUGIN_ID, PLAIN_ID)]})

    def _refused(self, exc, fragment, **kw):
        before = self._run_count()
        with self.assertRaises(exc) as ctx:
            self._run(**kw)
        self.assertIn(fragment, str(ctx.exception))
        self.assertEqual(self._run_count(), before)   # nothing was queued

    # --- queueing -----------------------------------------------------------

    def test_run_records_the_pair_and_where_each_input_comes_from(self):
        run_name = self._run(reference=self.before)
        run = frappe.get_doc("WebODM Plugin Run", run_name)
        self.assertEqual((run.task, run.reference_task, run.organization), (self.after, self.before, self.org))
        stored = self._stored(run_name)
        # Optional inputs resolve per survey: October has an orthophoto, September does not.
        self.assertEqual(stored["inputs"], {"surface": "dsm", "reference_surface": "dsm", "orthophoto": "orthophoto"})
        self.assertEqual(stored["reference_inputs"], ["reference_surface"])
        self.assertEqual(plugins_api.get_run(run_name)["reference_task"], self.before)
        listed = plugins_api.list_runs(task=self.after)
        self.assertEqual([(r["name"], r["reference_task"]) for r in listed], [(run_name, self.before)])

    def test_selection_picks_datasets_on_the_right_survey(self):
        # Only September has a DTM: it can feed the reference input, not this survey's.
        run_name = self._run(reference=self.before, inputs={"surface": "dsm", "reference_surface": "dtm"})
        self.assertEqual(self._stored(run_name)["inputs"], {"surface": "dsm", "reference_surface": "dtm"})
        frappe.db.set_value("WebODM Plugin Run", run_name, "status", "Completed")
        self._refused(frappe.ValidationError, "Task has no 'dtm'", reference=self.before,
                      inputs={"surface": "dtm", "reference_surface": "dsm"})
        self._refused(frappe.ValidationError, "The comparison survey has no 'orthophoto'", reference=self.before,
                      inputs={"surface": "dsm", "reference_surface": "dsm", "reference_orthophoto": "orthophoto"})

    def test_reference_survey_missing_a_required_input_is_refused(self):
        self._refused(frappe.ValidationError, "The comparison survey is missing the required input 'reference_surface'",
                      reference=self.no_surface)
        self._refused(frappe.ValidationError, "Task is missing the required input 'surface'",
                      task=self.no_surface, reference=self.before)

    def test_comparison_plugin_needs_a_reference_and_plain_plugins_take_none(self):
        self._refused(frappe.ValidationError, "compares two surveys")
        self._refused(frappe.ValidationError, "compares two surveys", reference="")
        self._refused(frappe.ValidationError, "does not compare surveys", plugin=PLAIN_ID, reference=self.before)
        # A plain plugin is untouched by any of this.
        run_name = self._run(plugin=PLAIN_ID)
        self.assertIsNone(frappe.db.get_value("WebODM Plugin Run", run_name, "reference_task"))
        self.assertNotIn("reference_inputs", self._stored(run_name))

    # --- who may compare what -------------------------------------------------

    def test_a_survey_cannot_be_compared_with_itself(self):
        self._refused(frappe.ValidationError, "cannot be compared with itself", reference=self.after)

    def test_another_organizations_survey_cannot_be_pulled_in(self):
        self._refused(frappe.PermissionError, "Comparison survey not found", reference=self.foreign)
        # From the other side too: the outsider cannot reach into this organization.
        self._as(self.outsider)
        self._refused(frappe.PermissionError, "Comparison survey not found", task=self.foreign, reference=self.before)
        # ...while comparing two of their own surveys works.
        run_name = self._run(task=self.foreign, reference=self.foreign_second)
        self.assertEqual(frappe.db.get_value("WebODM Plugin Run", run_name, "organization"), self.org_other)

    def test_missing_and_foreign_surveys_are_indistinguishable(self):
        errors = []
        for name in (self.foreign, "no-such-task-xyz"):
            with self.assertRaises(frappe.PermissionError) as ctx:
                self._run(reference=name)
            errors.append(str(ctx.exception))
        self.assertEqual(errors[0], errors[1])
        self.assertNotIn(self.foreign, errors[0])

    def test_reference_must_be_a_plain_task_name(self):
        for bad in ({"name": self.before}, [self.before], 7):
            self._refused(frappe.ValidationError, "must be a task name", reference=bad)

    def test_survey_of_another_project_is_refused(self):
        self._refused(frappe.ValidationError, "same project", reference=self.elsewhere)

    def test_unfinished_survey_is_refused(self):
        self._refused(frappe.ValidationError, "has not finished processing", reference=self.pending)

    def test_the_rule_lives_on_the_run_document_too(self):
        """Not only in the run API: a run created any other way (Desk,
        /api/resource) cannot point at another tenant's survey either."""
        def insert(reference, task=None):
            return frappe.get_doc({
                "doctype": "WebODM Plugin Run", "plugin": PLUGIN_ID, "task": task or self.after,
                "reference_task": reference, "status": "Queued",
            }).insert()

        with self.assertRaises(frappe.PermissionError):
            insert(self.foreign)
        with self.assertRaises(frappe.ValidationError):
            insert(self.elsewhere)
        with self.assertRaises(frappe.ValidationError):
            insert(self.after)
        # A payload that claims the other organization does not help: it is re-stamped.
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc({
                "doctype": "WebODM Plugin Run", "plugin": PLUGIN_ID, "task": self.after,
                "reference_task": self.foreign, "organization": self.org_other, "status": "Queued",
            }).insert()
        self.assertEqual(insert(self.before).reference_task, self.before)

    # --- worker ---------------------------------------------------------------

    def _fake_operation(self, seen):
        def op(op_id, inputs, params, output_path, timeout=600):
            seen.update(inputs)
            with open(output_path, "wb") as f:
                f.write(b"fake-output")
            return {"output_path": output_path, "metadata": {"epsg": 32633}}
        return op

    def test_worker_stages_each_input_from_its_own_survey(self):
        run_name = self._run(reference=self.before, inputs={
            "surface": "dsm", "reference_surface": "dtm", "orthophoto": "orthophoto"})
        seen = {}
        with patch.object(runner, "run_operation", self._fake_operation(seen)):
            runner.execute_run(run_name)
        run = frappe.get_doc("WebODM Plugin Run", run_name)
        self.assertEqual(run.status, "Completed", run.error)
        self.assertEqual(set(seen), {"surface", "reference_surface", "orthophoto"})
        self.assertIn(self.after, os.path.basename(seen["surface"]))
        self.assertIn(self.after, os.path.basename(seen["orthophoto"]))
        self.assertIn(self.before, os.path.basename(seen["reference_surface"]))
        self.assertTrue(seen["reference_surface"].endswith("_dtm.tif"))
        with open(seen["reference_surface"], "rb") as f:
            self.assertEqual(f.read(), f"fake-dtm-{self.before}".encode())

    def test_worker_refuses_a_pair_that_no_longer_holds(self):
        """The worker has no session; it re-checks the stored pair itself."""
        def never(*a, **k):
            raise AssertionError("nothing may be staged for an invalid pair")

        for tampered, fragment in ((self.foreign, "another organization"), (self.elsewhere, "same project")):
            run_name = self._run(reference=self.before)
            frappe.db.set_value("WebODM Plugin Run", run_name, "reference_task", tampered)
            with patch.object(runner, "run_operation", never), patch.object(runner, "run_user_plugin", never):
                runner.execute_run(run_name)
            run = frappe.get_doc("WebODM Plugin Run", run_name)
            self.assertEqual(run.status, "Failed")
            self.assertIn(fragment, run.error)
            self.assertFalse(run.output_file)
            frappe.delete_doc("WebODM Plugin Run", run_name, force=True, ignore_permissions=True)

    def test_worker_fails_cleanly_when_the_reference_lost_its_dataset(self):
        task = self._task(self.owner, self.project, "Short-lived", dsm=True)
        self._as(self.owner)
        run_name = self._run(reference=task)
        frappe.db.set_value("WebODM Task", task, "dsm", None)
        with patch.object(runner, "run_operation", self._fake_operation({})):
            runner.execute_run(run_name)
        run = frappe.get_doc("WebODM Plugin Run", run_name)
        self.assertEqual(run.status, "Failed")
        self.assertIn("The comparison survey no longer has 'dsm'", run.error)

    def test_user_plugin_is_told_about_both_surveys(self):
        installed = plugins_api.install_user_plugin(_zip(MANIFEST), self.org)
        plugin_id = installed["name"]
        self.assertEqual([i.get("survey") for i in installed["inputs"]], [None, "reference", None, "reference"])
        try:
            with patch.object(frappe, "enqueue", lambda *a, **k: None):
                run_name = plugins_api.run_plugin(plugin=plugin_id, task=self.after,
                                                  reference_task=self.before)["run"]
            seen = {}

            def fake_sandbox(plugin, inputs, params, output_path, timeout=300, *, context=None, on_progress=None):
                seen.update(inputs=inputs, context=context)
                with open(output_path, "w") as f:
                    json.dump({"type": "FeatureCollection", "features": []}, f)
                return {"output_path": output_path, "metadata": {"feature_count": 0}}

            with patch.object(runner, "run_user_plugin", fake_sandbox):
                runner.execute_run(run_name)
            self.assertEqual(frappe.db.get_value("WebODM Plugin Run", run_name, "status"), "Completed")
            context = seen["context"]
            self.assertEqual(context["task"]["name"], self.after)
            self.assertEqual(context["task"]["title"], "October")
            self.assertEqual(context["reference_task"]["name"], self.before)
            self.assertEqual(context["reference_task"]["title"], "September")
            self.assertEqual(set(context["reference_task"]), set(context["task"]))
            self.assertEqual(context["inputs"], {
                "surface": {"dataset": "dsm", "survey": "primary"},
                "reference_surface": {"dataset": "dsm", "survey": "reference"},
                "orthophoto": {"dataset": "orthophoto", "survey": "primary"},
            })
            self.assertIn(self.before, os.path.basename(seen["inputs"]["reference_surface"]))
        finally:
            frappe.set_user("Administrator")
            plugins_api._delete_runs({"plugin": plugin_id})
            for s in frappe.get_all("WebODM Plugin Setting", filters={"plugin": plugin_id}, pluck="name"):
                frappe.delete_doc("WebODM Plugin Setting", s, force=True, ignore_permissions=True)
            plugins_api._delete_attachments("WebODM Plugin", plugin_id)
            frappe.delete_doc("WebODM Plugin", plugin_id, force=True, ignore_permissions=True)

    def test_plain_run_context_is_unchanged_in_shape(self):
        task = frappe.get_doc("WebODM Task", self.after)
        context = runner.task_context(task)
        self.assertEqual(set(context), {"task"})
        self.assertEqual(runner.task_context(task, None, {"raster": "dsm"})["inputs"],
                         {"raster": {"dataset": "dsm", "survey": "primary"}})

    # --- one result per pair ---------------------------------------------------

    def test_rerun_replaces_the_same_pair_only(self):
        first = self._run(reference=self.before)
        frappe.db.set_value("WebODM Plugin Run", first, "status", "Completed")
        other = self._run(reference=self.august)          # a different pair: both stay
        frappe.db.set_value("WebODM Plugin Run", other, "status", "Completed")
        self.assertTrue(frappe.db.exists("WebODM Plugin Run", first))
        again = self._run(reference=self.before)           # the same pair: replaced
        self.assertFalse(frappe.db.exists("WebODM Plugin Run", first))
        remaining = frappe.get_all("WebODM Plugin Run", filters={"plugin": PLUGIN_ID, "task": self.after},
                                   fields=["name", "reference_task"])
        self.assertEqual({(r.name, r.reference_task) for r in remaining},
                         {(other, self.august), (again, self.before)})

    def test_active_run_blocks_its_own_pair_only(self):
        self._run(reference=self.before)                   # stays Queued
        self._refused(frappe.ValidationError, "already running", reference=self.before)
        self._run(reference=self.august)

    def test_plain_plugin_still_keeps_one_run_per_task(self):
        first = self._run(plugin=PLAIN_ID)
        frappe.db.set_value("WebODM Plugin Run", first, "status", "Completed")
        second = self._run(plugin=PLAIN_ID)
        self.assertEqual(frappe.get_all("WebODM Plugin Run", filters={"plugin": PLAIN_ID}, pluck="name"), [second])

    # --- deletion ----------------------------------------------------------------

    def test_deleting_either_survey_removes_the_comparison(self):
        base = self._task(self.owner, self.project, "Baseline", dsm=True)
        newer = self._task(self.owner, self.project, "Newer", dsm=True)
        self._as(self.owner)
        run_name = self._run(task=newer, reference=base)
        unrelated = self._run(reference=self.before)
        # Deleting the *reference* survey would otherwise fail on the link.
        frappe.delete_doc("WebODM Task", base)
        self.assertFalse(frappe.db.exists("WebODM Plugin Run", run_name))
        self.assertTrue(frappe.db.exists("WebODM Task", newer))
        self.assertTrue(frappe.db.exists("WebODM Plugin Run", unrelated))

        base = self._task(self.owner, self.project, "Baseline 2", dsm=True)
        self._as(self.owner)
        run_name = self._run(task=newer, reference=base)
        frappe.delete_doc("WebODM Task", newer)
        self.assertFalse(frappe.db.exists("WebODM Plugin Run", run_name))
        self.assertTrue(frappe.db.exists("WebODM Task", base))
