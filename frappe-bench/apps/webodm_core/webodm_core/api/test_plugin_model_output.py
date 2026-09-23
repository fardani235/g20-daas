"""The ``model`` (GLB) output kind, run context and live progress for user plugins."""

import json
import os
import tempfile
import time
import zipfile
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import plugins as plugins_api
from webodm_core.api.test_user_plugins import _join, _org, _user
from webodm_core.plugins import package as package_mod
from webodm_core.plugins import runner, sandbox

MODEL_MANIFEST = {
    "id": "recon-test",
    "label": "Recon",
    "version": "1.0.0",
    "entrypoint": "main.py",
    "inputs": [
        {"name": "dsm", "datasets": ["dsm"], "optional": True},
        {"name": "point_cloud", "datasets": ["point_cloud"], "optional": True},
        {"name": "model", "datasets": ["model"], "optional": True},
    ],
    "params_schema": {"type": "object", "properties": {"quality": {"type": "string", "default": "auto"}}},
    "output_kind": "model",
    "timeout_seconds": 600,
}


def _zip(manifest) -> str:
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("main.py", "print('hi')\n")
    return path


class TestModelManifest(FrappeTestCase):
    def test_model_output_kind_is_accepted_and_defaults_render_kind(self):
        m = package_mod.inspect_package(_zip(MODEL_MANIFEST))
        self.assertEqual(m["output_kind"], "model")
        self.assertEqual(m["render_kind"], "model")
        self.assertEqual([i["name"] for i in m["inputs"]], ["dsm", "point_cloud", "model"])

    def test_model_render_kind_must_be_model(self):
        with self.assertRaises(package_mod.PackageError):
            package_mod.inspect_package(_zip({**MODEL_MANIFEST, "render_kind": "dem"}))

    def test_output_extension_for_model_is_glb(self):
        self.assertEqual(runner._OUTPUT_EXT["model"], "glb")


class TestTaskContext(FrappeTestCase):
    def test_context_decodes_double_encoded_options(self):
        options = json.dumps(json.dumps([{"name": "pc-quality", "value": "high"}]))
        task = frappe._dict(name="t1", title="T", epsg=32632, wkt="PROJCS[...]", resolution=2.5,
                            processing_options=options)
        ctx = runner.task_context(task)
        self.assertEqual(ctx["task"]["epsg"], 32632)
        self.assertEqual(ctx["task"]["processing_options"], [{"name": "pc-quality", "value": "high"}])

    def test_context_tolerates_missing_options(self):
        ctx = runner.task_context(frappe._dict(name="t2", processing_options=None))
        self.assertEqual(ctx["task"]["processing_options"], [])
        self.assertIsNone(ctx["task"]["epsg"])


class TestProgressPolling(FrappeTestCase):
    def test_read_progress_validates(self):
        tmp = tempfile.mkdtemp()
        self.assertIsNone(sandbox.read_progress(tmp))
        with open(os.path.join(tmp, sandbox.PROGRESS_FILE), "w") as f:
            f.write("{not json")
        self.assertIsNone(sandbox.read_progress(tmp))
        with open(os.path.join(tmp, sandbox.PROGRESS_FILE), "w") as f:
            json.dump({"percent": 250, "message": "x" * 300}, f)
        percent, message = sandbox.read_progress(tmp)
        self.assertEqual(percent, 100)
        self.assertEqual(len(message), 140)

    def test_post_with_progress_forwards_updates_while_request_runs(self):
        tmp = tempfile.mkdtemp()
        seen = []

        class Resp:
            status_code = 200

        def fake_post(url, json=None, timeout=None):  # noqa: A002 (requests' keyword)
            for pct, msg in ((10, "reading"), (60, "meshing")):
                with open(os.path.join(tmp, sandbox.PROGRESS_FILE), "w") as f:
                    f.write('{"percent": %d, "message": "%s"}' % (pct, msg))
                time.sleep(0.15)
            return Resp()

        with patch.object(sandbox.requests, "post", fake_post), \
             patch.object(sandbox, "PROGRESS_POLL_SECONDS", 0.05):
            resp = sandbox._post_with_progress("http://x/run", {}, 5, tmp,
                                               lambda p, m: seen.append((p, m)))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seen[-1], (60, "meshing"))
        self.assertIn((10, "reading"), seen)

    def test_post_with_progress_reraises_request_errors(self):
        def fake_post(url, json=None, timeout=None):
            raise sandbox.requests.ConnectionError("down")

        with patch.object(sandbox.requests, "post", fake_post):
            with self.assertRaises(sandbox.requests.ConnectionError):
                sandbox._post_with_progress("http://x/run", {}, 5, tempfile.mkdtemp(), None)


class TestModelRunExecution(FrappeTestCase):
    """A model-kind user plugin run ends with a .glb attachment and live progress on the row."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = _user("model_plugin_owner@example.com")
        cls.org = _org("Model Plugin Org")
        _join(cls.owner, cls.org)
        frappe.set_user(cls.owner)
        frappe.local.webodm_org_cache = {}
        cls.project = frappe.get_doc({"doctype": "WebODM Project", "title": "Model Plugin Project"}).insert().name
        task = frappe.get_doc({"doctype": "WebODM Task", "project": cls.project, "title": "model task",
                               "status": "Completed", "epsg": 32632}).insert()
        for field, name in (("dsm", "dsm.tif"), ("point_cloud", "georeferenced_model.laz")):
            f = frappe.get_doc({
                "doctype": "File", "file_name": f"{task.name}_{name}", "is_private": 1,
                "content": b"bytes", "attached_to_doctype": "WebODM Task", "attached_to_name": task.name,
            }).save(ignore_permissions=True)
            task.db_set(field, f.file_url)
        cls.task = task.name
        cls.plugin_id = plugins_api.install_user_plugin(_zip(MODEL_MANIFEST), cls.org)["name"]
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        if frappe.db.exists("WebODM Plugin", cls.plugin_id):
            plugins_api._delete_runs({"plugin": cls.plugin_id})
            for s in frappe.get_all("WebODM Plugin Setting", filters={"plugin": cls.plugin_id}, pluck="name"):
                frappe.delete_doc("WebODM Plugin Setting", s, force=True, ignore_permissions=True)
            plugins_api._delete_attachments("WebODM Plugin", cls.plugin_id)
            frappe.delete_doc("WebODM Plugin", cls.plugin_id, force=True, ignore_permissions=True)
        super().tearDownClass()

    def setUp(self):
        plugins_api._delete_runs({"plugin": self.plugin_id})
        frappe.set_user(self.owner)
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def test_model_run_attaches_glb_and_records_progress(self):
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            run_name = plugins_api.run_plugin(plugin=self.plugin_id, task=self.task, params={})["run"]
        run = frappe.get_doc("WebODM Plugin Run", run_name)
        self.assertEqual(run.output_kind, "model")
        self.assertEqual(run.render_kind, "model")
        self.assertEqual(set(json.loads(run.parameters)["inputs"]), {"dsm", "point_cloud"})

        progress_seen = []

        def fake_sandbox(plugin, inputs, params, output_path, timeout=300, *, context=None, on_progress=None):
            self.assertEqual(context["task"]["epsg"], 32632)
            on_progress(45, "decimating")
            doc = frappe.get_doc("WebODM Plugin Run", run_name)
            progress_seen.append((doc.progress, doc.progress_message))
            with open(output_path, "wb") as f:
                f.write(b"glTF" + b"\0" * 12)
            return {"output_path": output_path,
                    "metadata": {"epsg": 32632, "extent": {"type": "Polygon", "coordinates": []},
                                 "triangles": 12, "workflow": "terrain"}}

        with patch.object(runner, "run_user_plugin", fake_sandbox):
            runner.execute_run(run_name)

        run.reload()
        self.assertEqual(run.status, "Completed")
        self.assertEqual(progress_seen, [(45.0, "decimating")])
        self.assertEqual(run.progress, 100)
        self.assertFalse(run.progress_message)
        self.assertTrue(run.output_file.endswith(".glb"), run.output_file)
        self.assertEqual(json.loads(run.output_metadata)["triangles"], 12)
        self.assertEqual(json.loads(run.output_extent)["type"], "Polygon")
        # The listing carries what the UI needs to offer the 3D viewer.
        row = next(r for r in plugins_api.list_runs(task=self.task) if r["name"] == run_name)
        self.assertEqual(row["output_kind"], "model")
        self.assertIn("progress_message", row)
