"""User plugins: package validation, install/upgrade/remove, org isolation,
catalog sync independence, sandbox dispatch in the worker."""

import json
import os
import tempfile
import zipfile
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import plugins as plugins_api
from webodm_core.plugins import package as package_mod
from webodm_core.plugins import runner, sandbox, sync

MANIFEST = {
    "id": "elevation-mask",
    "label": "Elevation Mask",
    "version": "1.0.0",
    "description": "test plugin",
    "entrypoint": "main.py",
    "inputs": [{"name": "raster", "datasets": ["dsm", "dtm"]}],
    "params_schema": {
        "type": "object",
        "properties": {"threshold": {"type": "number", "default": 100}},
        "required": ["threshold"],
    },
    "output_kind": "raster",
    "render_kind": "dem",
    "timeout_seconds": 120,
}


def _zip(manifest=MANIFEST, files=None, raw_members=None) -> str:
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as zf:
        if manifest is not None:
            zf.writestr("plugin.json", json.dumps(manifest) if not isinstance(manifest, str) else manifest)
        for name, text in (files or {"main.py": "print('hi')\n"}).items():
            zf.writestr(name, text)
        for info, data in (raw_members or []):
            zf.writestr(info, data)
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


class TestPackageValidation(FrappeTestCase):
    def _bad(self, path, fragment):
        try:
            with self.assertRaises(package_mod.PackageError) as ctx:
                package_mod.inspect_package(path)
            self.assertIn(fragment, str(ctx.exception))
        finally:
            os.remove(path)

    def test_valid_package_is_normalized(self):
        path = _zip()
        try:
            m = package_mod.inspect_package(path)
        finally:
            os.remove(path)
        self.assertEqual(m["id"], "elevation-mask")
        self.assertEqual(m["timeout_seconds"], 120)
        self.assertEqual(m["params_schema"]["required"], ["threshold"])

    def test_defaults_fill_optional_fields(self):
        minimal = {"id": "x-y", "version": "2", "inputs": [{"name": "raster", "datasets": ["dsm"]}]}
        path = _zip(minimal)
        try:
            m = package_mod.inspect_package(path)
        finally:
            os.remove(path)
        self.assertEqual(m["label"], "x-y")
        self.assertEqual(m["entrypoint"], "main.py")
        self.assertEqual(m["output_kind"], "raster")
        self.assertEqual(m["render_kind"], "dem")
        self.assertEqual(m["timeout_seconds"], package_mod.DEFAULT_TIMEOUT)

    def test_not_a_zip(self):
        fd, path = tempfile.mkstemp(suffix=".zip")
        os.write(fd, b"nope")
        os.close(fd)
        self._bad(path, "not a valid zip")

    def test_missing_manifest(self):
        self._bad(_zip(manifest=None), "plugin.json")

    def test_manifest_not_json(self):
        self._bad(_zip(manifest="{not json"), "not valid JSON")

    def test_missing_entrypoint(self):
        self._bad(_zip(files={"other.py": ""}), "entrypoint 'main.py' not found")

    def test_path_traversal_member(self):
        self._bad(_zip(raw_members=[("../evil.py", "")]), "unsafe path")

    def test_symlink_member(self):
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        self._bad(_zip(raw_members=[(info, "/etc/passwd")]), "symlink")

    def test_bad_id_version_dataset_kind_timeout(self):
        self._bad(_zip({**MANIFEST, "id": "Bad Id"}), "'id'")
        self._bad(_zip({**MANIFEST, "version": "latest"}), "'version'")
        self._bad(_zip({**MANIFEST, "inputs": [{"name": "raster", "datasets": ["nope"]}]}), "unknown dataset")
        self._bad(_zip({**MANIFEST, "inputs": []}), "'inputs'")
        self._bad(_zip({**MANIFEST, "output_kind": "table"}), "'output_kind'")
        self._bad(_zip({**MANIFEST, "render_kind": "rainbow"}), "render_kind")
        self._bad(_zip({**MANIFEST, "timeout_seconds": 99999}), "'timeout_seconds'")
        self._bad(_zip({**MANIFEST, "params_schema": {"type": "array"}}), "describe an object")
        self._bad(_zip({**MANIFEST, "params_schema": {"properties": {}, "required": ["ghost"]}}), "required")

    def test_optional_inputs_are_normalized(self):
        manifest = {**MANIFEST, "inputs": [
            {"name": "ortho", "datasets": ["orthophoto"], "optional": True, "label": "Orthophoto"},
            {"name": "surface", "datasets": ["dsm"], "optional": False, "extra": "dropped"},
        ]}
        path = _zip(manifest)
        try:
            m = package_mod.inspect_package(path)
        finally:
            os.remove(path)
        self.assertEqual(m["inputs"], [
            {"name": "ortho", "datasets": ["orthophoto"], "optional": True, "label": "Orthophoto"},
            {"name": "surface", "datasets": ["dsm"]},
        ])
        self._bad(_zip({**MANIFEST, "inputs": [{"name": "raster", "datasets": ["dsm"], "optional": "yes"}]}),
                  "'optional'")

    def test_namespaced_id(self):
        self.assertEqual(package_mod.namespaced_id("acme-corp", "elevation-mask"), "acme-corp.elevation-mask")


class TestUserPlugins(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = _user("uplugin_owner@example.com")
        cls.member = _user("uplugin_member@example.com")
        cls.outsider = _user("uplugin_outsider@example.com")
        cls.org = _org("User Plugin Org")
        cls.org_other = _org("User Plugin Other Org")
        _join(cls.owner, cls.org)
        _join(cls.member, cls.org, role="Member")
        _join(cls.outsider, cls.org_other)
        cls.slug = frappe.db.get_value("WebODM Organization", cls.org, "slug")
        cls.plugin_id = f"{cls.slug}.elevation-mask"
        cls._cleanup()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        cls._cleanup()
        super().tearDownClass()

    @classmethod
    def _cleanup(cls):
        # upsert_catalog() commits, so rows from a previous run can survive the
        # class rollback; only ever touch this class's organizations.
        for name in frappe.get_all("WebODM Plugin", filters={"plugin_type": "User",
                                   "organization": ["in", [cls.org, cls.org_other]]}, pluck="name"):
            plugins_api._delete_runs({"plugin": name})
            for s in frappe.get_all("WebODM Plugin Setting", filters={"plugin": name}, pluck="name"):
                frappe.delete_doc("WebODM Plugin Setting", s, force=True, ignore_permissions=True)
            plugins_api._delete_attachments("WebODM Plugin", name)
            frappe.delete_doc("WebODM Plugin", name, force=True, ignore_permissions=True)

    def setUp(self):
        self._cleanup()
        self._as("Administrator")

    def tearDown(self):
        self._as("Administrator")

    def _as(self, user):
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}

    def _install(self, org=None, manifest=MANIFEST):
        # Acts as that org's admin, like the upload endpoint: the setting row
        # created on install is stamped with the acting user's organization.
        org = org or self.org
        prior = frappe.session.user
        self._as(self.owner if org == self.org else self.outsider)
        try:
            return plugins_api.install_user_plugin(_zip(manifest), org)
        finally:
            self._as(prior)

    # --- install / upgrade ---

    def test_install_creates_org_scoped_row_and_enables_it(self):
        result = self._install()
        self.assertEqual(result["name"], self.plugin_id)
        self.assertEqual(result["plugin_type"], "User")
        self.assertTrue(result["created"])
        self.assertTrue(result["enabled"])
        self.assertTrue(result["runnable"])

        doc = frappe.get_doc("WebODM Plugin", self.plugin_id)
        self.assertEqual(doc.organization, self.org)
        self.assertEqual(doc.entrypoint, "main.py")
        self.assertEqual(doc.timeout_seconds, 120)
        self.assertTrue(doc.package)
        self.assertEqual(len(doc.package_hash), 64)
        self.assertTrue(frappe.db.exists("File", {"file_url": doc.package}))
        self.assertEqual(json.loads(doc.inputs), MANIFEST["inputs"])

    def test_reupload_upgrades_in_place_and_keeps_settings(self):
        self._install()
        old_package = frappe.db.get_value("WebODM Plugin", self.plugin_id, "package")
        # Org disables it, then uploads a new version.
        self._as(self.owner)
        plugins_api.save_plugin_setting(plugin=self.plugin_id, enabled=False, settings={"threshold": 5})
        self._as("Administrator")

        result = self._install(manifest={**MANIFEST, "version": "1.1.0", "label": "Elevation Mask v2"})
        self.assertFalse(result["created"])
        self.assertFalse(result["enabled"])  # choice preserved
        self.assertEqual(result["settings"], {"threshold": 5})
        doc = frappe.get_doc("WebODM Plugin", self.plugin_id)
        self.assertEqual(doc.version, "1.1.0")
        self.assertEqual(doc.label, "Elevation Mask v2")
        self.assertNotEqual(doc.package, old_package)
        self.assertFalse(frappe.db.exists("File", {"file_url": old_package}))
        self.assertEqual(frappe.db.count("WebODM Plugin", {"plugin_type": "User", "organization": self.org}), 1)

    def test_invalid_package_is_rejected_and_nothing_created(self):
        self._as(self.owner)
        with self.assertRaises(frappe.ValidationError) as ctx:
            plugins_api.install_user_plugin(_zip(files={"other.py": ""}), self.org)
        self.assertIn("Invalid plugin package", str(ctx.exception))
        self.assertFalse(frappe.db.exists("WebODM Plugin", self.plugin_id))

    def test_same_manifest_id_in_two_orgs_does_not_collide(self):
        a = self._install(org=self.org)
        b = self._install(org=self.org_other)
        self.assertNotEqual(a["name"], b["name"])
        self.assertEqual(frappe.db.get_value("WebODM Plugin", b["name"], "organization"), self.org_other)

    # --- upload endpoint auth ---

    def test_upload_requires_org_admin(self):
        self._as(self.member)
        with self.assertRaises(frappe.PermissionError):
            plugins_api.upload_plugin()

    # --- visibility / isolation ---

    def test_list_plugins_hides_other_orgs_user_plugins(self):
        self._install()
        self._as(self.owner)
        names = {p["name"] for p in plugins_api.list_plugins()}
        self.assertIn(self.plugin_id, names)

        self._as(self.outsider)
        names = {p["name"] for p in plugins_api.list_plugins()}
        self.assertNotIn(self.plugin_id, names)

    def test_other_org_cannot_enable_or_run_or_remove(self):
        self._install()
        self._as(self.outsider)
        with self.assertRaises(frappe.DoesNotExistError):
            plugins_api.save_plugin_setting(plugin=self.plugin_id, enabled=True)
        with self.assertRaises(frappe.DoesNotExistError):
            plugins_api.run_plugin(plugin=self.plugin_id, task="whatever")
        with self.assertRaises(frappe.DoesNotExistError):
            plugins_api.remove_plugin(self.plugin_id)
        self.assertTrue(frappe.db.exists("WebODM Plugin", self.plugin_id))

    def test_permission_hooks_scope_user_plugins(self):
        self._install()
        self._as(self.outsider)
        visible = frappe.get_list("WebODM Plugin", pluck="name")
        self.assertNotIn(self.plugin_id, visible)
        self.assertFalse(frappe.has_permission("WebODM Plugin", "read",
                                               frappe.get_doc("WebODM Plugin", self.plugin_id)))
        self._as(self.owner)
        visible = frappe.get_list("WebODM Plugin", pluck="name")
        self.assertIn(self.plugin_id, visible)
        self.assertTrue(frappe.has_permission("WebODM Plugin", "read",
                                              frappe.get_doc("WebODM Plugin", self.plugin_id)))

    def test_system_plugin_is_read_only_for_members(self):
        if not frappe.db.exists("WebODM Plugin", "test-uplugin-system"):
            frappe.get_doc({"doctype": "WebODM Plugin", "plugin_id": "test-uplugin-system",
                            "label": "Sys", "output_kind": "raster"}).insert(ignore_permissions=True)
        try:
            doc = frappe.get_doc("WebODM Plugin", "test-uplugin-system")
            self.assertEqual(doc.plugin_type, "System")
            self._as(self.owner)
            self.assertTrue(frappe.has_permission("WebODM Plugin", "read", doc))
            self.assertFalse(frappe.has_permission("WebODM Plugin", "write", doc))
        finally:
            self._as("Administrator")
            frappe.delete_doc("WebODM Plugin", "test-uplugin-system", force=True, ignore_permissions=True)

    # --- catalog sync leaves user plugins alone ---

    def test_sync_does_not_touch_user_plugins(self):
        self._install()
        sync.upsert_catalog([])  # geospatial says: no operations at all
        doc = frappe.get_doc("WebODM Plugin", self.plugin_id)
        self.assertEqual(doc.available, 1)
        self.assertEqual(doc.plugin_type, "User")

    # --- remove ---

    def test_remove_deletes_row_settings_and_package(self):
        self._install()
        package_url = frappe.db.get_value("WebODM Plugin", self.plugin_id, "package")
        self._as(self.owner)
        result = plugins_api.remove_plugin(self.plugin_id)
        self.assertTrue(result["removed"])
        self.assertFalse(frappe.db.exists("WebODM Plugin", self.plugin_id))
        self.assertFalse(frappe.db.exists("WebODM Plugin Setting", {"plugin": self.plugin_id}))
        self.assertFalse(frappe.db.exists("File", {"file_url": package_url}))

    def test_remove_rejects_system_plugins_and_non_admins(self):
        self._install()
        self._as(self.member)
        with self.assertRaises(frappe.PermissionError):
            plugins_api.remove_plugin(self.plugin_id)
        if not frappe.db.exists("WebODM Plugin", "test-uplugin-system2"):
            frappe.get_doc({"doctype": "WebODM Plugin", "plugin_id": "test-uplugin-system2",
                            "label": "Sys", "output_kind": "raster"}).insert(ignore_permissions=True)
        try:
            self._as(self.owner)
            with self.assertRaises(frappe.ValidationError):
                plugins_api.remove_plugin("test-uplugin-system2")
        finally:
            self._as("Administrator")
            frappe.delete_doc("WebODM Plugin", "test-uplugin-system2", force=True, ignore_permissions=True)


class TestUserPluginExecution(FrappeTestCase):
    """The worker dispatches User plugins to the sandbox client, and the client
    stages/collects/cleans up around the runner call."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = _user("uplugin_exec_owner@example.com")
        cls.org = _org("User Plugin Exec Org")
        _join(cls.owner, cls.org)
        frappe.set_user(cls.owner)
        frappe.local.webodm_org_cache = {}
        cls.project = frappe.get_doc({"doctype": "WebODM Project", "title": "User Plugin Exec Project"}).insert().name
        task = frappe.get_doc({"doctype": "WebODM Task", "project": cls.project,
                               "title": "exec task", "status": "Completed"}).insert()
        f = frappe.get_doc({
            "doctype": "File", "file_name": f"{task.name}_dsm.tif", "is_private": 1,
            "content": b"fake-dsm-bytes", "attached_to_doctype": "WebODM Task",
            "attached_to_name": task.name,
        }).save(ignore_permissions=True)
        task.db_set("dsm", f.file_url)
        cls.task = task.name
        cls.plugin_id = plugins_api.install_user_plugin(_zip(), cls.org)["name"]
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

    def _queue_run(self):
        with patch.object(frappe, "enqueue", lambda *a, **k: None):
            return plugins_api.run_plugin(plugin=self.plugin_id, task=self.task, params={"threshold": 7})["run"]

    def test_run_plugin_uses_manifest_timeout(self):
        calls = []
        with patch.object(frappe, "enqueue", lambda *a, **k: calls.append(k)):
            plugins_api.run_plugin(plugin=self.plugin_id, task=self.task, params={"threshold": 7})
        self.assertEqual(calls[0]["timeout"], 120)

    def test_worker_dispatches_user_plugin_to_sandbox(self):
        run_name = self._queue_run()
        seen = {}

        def fake_sandbox(plugin, inputs, params, output_path, timeout=300):
            seen.update(plugin=plugin.name, inputs=inputs, params=params, timeout=timeout)
            with open(output_path, "wb") as f:
                f.write(b"fake-output")
            return {"output_path": output_path, "metadata": {"epsg": 32633, "extent": {"type": "Polygon", "coordinates": []}}}

        def never(*a, **k):
            raise AssertionError("system backend must not be called for a user plugin")

        with patch.object(runner, "run_user_plugin", fake_sandbox), patch.object(runner, "run_operation", never):
            runner.execute_run(run_name)

        run = frappe.get_doc("WebODM Plugin Run", run_name)
        self.assertEqual(run.status, "Completed")
        self.assertTrue(run.output_file)
        self.assertEqual(seen["plugin"], self.plugin_id)
        self.assertEqual(seen["params"], {"threshold": 7})
        self.assertEqual(seen["timeout"], 120)
        self.assertTrue(seen["inputs"]["raster"].endswith("_dsm.tif"))
        self.assertEqual(json.loads(run.output_metadata)["epsg"], 32633)

    def test_sandbox_failure_marks_run_failed(self):
        run_name = self._queue_run()

        def boom(*a, **k):
            raise sandbox.PluginRunnerError("plugin run failed: plugin exited with status 1:\nkaboom")

        with patch.object(runner, "run_user_plugin", boom):
            runner.execute_run(run_name)
        run = frappe.get_doc("WebODM Plugin Run", run_name)
        self.assertEqual(run.status, "Failed")
        self.assertIn("kaboom", run.error)

    def test_sandbox_client_stages_calls_collects_and_cleans_up(self):
        plugin = frappe.get_doc("WebODM Plugin", self.plugin_id)
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, "dsm.tif")
        with open(src, "wb") as f:
            f.write(b"dem-bytes")
        out = os.path.join(tmp, "final.tif")
        posted = {}

        class FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"output_path": posted["json"]["output_path"], "metadata": {"epsg": 1}, "log": ""}

        def fake_post(url, json=None, timeout=None):
            posted.update(url=url, json=json, timeout=timeout)
            run_dir = json["run_dir"]
            # Everything the runner may touch is inside the run dir...
            for p in (json["package_path"], json["output_path"], *json["inputs"].values()):
                self.assertTrue(p.startswith(run_dir + os.sep))
            self.assertTrue(run_dir.startswith(sandbox.sandbox_dir() + os.sep))
            # ...the input is a copy, the package is the uploaded zip...
            with open(json["inputs"]["raster"], "rb") as f:
                self.assertEqual(f.read(), b"dem-bytes")
            self.assertTrue(zipfile.is_zipfile(json["package_path"]))
            self.assertEqual(json["output_kind"], "raster")
            self.assertEqual(json["timeout_seconds"], 42)
            # ...and the runner writes its output there.
            with open(json["output_path"], "wb") as f:
                f.write(b"result-bytes")
            return FakeResp()

        with patch.object(sandbox.requests, "post", fake_post), \
             patch.object(sandbox, "sandbox_dir", lambda: os.path.join(tmp, "sandbox")):
            result = sandbox.run_user_plugin(plugin, {"raster": src}, {"threshold": 1}, out, timeout=42)

        self.assertEqual(result["metadata"], {"epsg": 1})
        with open(out, "rb") as f:
            self.assertEqual(f.read(), b"result-bytes")
        self.assertFalse(os.path.exists(posted["json"]["run_dir"]))  # cleaned up
        self.assertTrue(posted["url"].endswith("/run"))
        self.assertGreater(posted["timeout"], 42)

    def test_sandbox_client_maps_runner_errors(self):
        plugin = frappe.get_doc("WebODM Plugin", self.plugin_id)
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, "dsm.tif")
        with open(src, "wb") as f:
            f.write(b"x")

        class Resp:
            def __init__(self, code, detail):
                self.status_code = code
                self._detail = detail
                self.text = detail

            def json(self):
                return {"detail": self._detail}

        def failing(code, detail):
            def post(*a, **k):
                err = sandbox.requests.HTTPError(detail)
                err.response = Resp(code, detail)
                raise err
            return post

        with patch.object(sandbox, "sandbox_dir", lambda: os.path.join(tmp, "sandbox")):
            with patch.object(sandbox.requests, "post", failing(422, "plugin exited with status 1")):
                with self.assertRaises(sandbox.PluginRunnerError) as ctx:
                    sandbox.run_user_plugin(plugin, {"raster": src}, {}, os.path.join(tmp, "o.tif"))
                self.assertNotIsInstance(ctx.exception, sandbox.PluginRunnerUnavailable)
                self.assertIn("status 1", str(ctx.exception))

            with patch.object(sandbox.requests, "post", failing(500, "sandbox failure")):
                with self.assertRaises(sandbox.PluginRunnerUnavailable):
                    sandbox.run_user_plugin(plugin, {"raster": src}, {}, os.path.join(tmp, "o.tif"))

            def unreachable(*a, **k):
                raise sandbox.requests.ConnectionError("refused")

            with patch.object(sandbox.requests, "post", unreachable):
                with self.assertRaises(sandbox.PluginRunnerUnavailable):
                    sandbox.run_user_plugin(plugin, {"raster": src}, {}, os.path.join(tmp, "o.tif"))
        self.assertEqual(os.listdir(os.path.join(tmp, "sandbox", "runs")), [])
