"""Data-model tests for the analysis plugin system.

Covers organization stamping (never from the payload), the run status field,
the per-org uniqueness of plugin settings, and cross-organization access
control for both new org-scoped DocTypes.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

PLUGIN_ID = "test-contours-model"


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
    frappe.get_doc({
        "doctype": "WebODM Org Membership",
        "user": user,
        "organization": org,
        "role": role,
    }).insert(ignore_permissions=True)


class TestPluginModel(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("WebODM Plugin", PLUGIN_ID):
            frappe.get_doc({
                "doctype": "WebODM Plugin",
                "plugin_id": PLUGIN_ID,
                "label": "Test Contours",
                "version": "1.0.0",
                "output_kind": "vector",
                "render_kind": "contours",
                "platform_enabled": 1,
                "available": 1,
                "params_schema": "{}",
            }).insert(ignore_permissions=True)

        cls.owner = _user("plugin_owner@example.com")
        cls.intruder = _user("plugin_intruder@example.com")
        cls.org_a = _org("Plugin Model Org A")
        cls.org_b = _org("Plugin Model Org B")
        _join(cls.owner, cls.org_a)
        _join(cls.intruder, cls.org_b)

        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.owner)
        cls.project_name = frappe.db.get_value(
            "WebODM Project", {"title": "Plugin Model Project"}, "name"
        ) or frappe.get_doc({
            "doctype": "WebODM Project",
            "title": "Plugin Model Project",
        }).insert().name
        task = frappe.get_doc({
            "doctype": "WebODM Task",
            "project": cls.project_name,
            "title": "Plugin Model Task",
            "status": "Completed",
        }).insert()
        cls.task_name = task.name

        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        for name in frappe.get_all("WebODM Plugin Run", filters={"plugin": PLUGIN_ID}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Run", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("WebODM Plugin Setting", filters={"plugin": PLUGIN_ID}, pluck="name"):
            frappe.delete_doc("WebODM Plugin Setting", name, force=True, ignore_permissions=True)
        frappe.delete_doc("WebODM Task", cls.task_name, force=True, ignore_permissions=True)
        frappe.delete_doc("WebODM Project", cls.project_name, force=True, ignore_permissions=True)
        frappe.delete_doc("WebODM Plugin", PLUGIN_ID, force=True, ignore_permissions=True)
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def _setting(self, user, org_expected, enabled=1, settings=None):
        frappe.set_user(user)
        frappe.local.webodm_org_cache = {}
        doc = frappe.get_doc({
            "doctype": "WebODM Plugin Setting",
            "plugin": PLUGIN_ID,
            "enabled": enabled,
            "settings": settings or "{}",
        }).insert()
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        if org_expected:
            self.assertEqual(doc.organization, org_expected)
        return doc

    def test_setting_stamped_with_actor_org(self):
        doc = self._setting(self.owner, self.org_a)
        frappe.delete_doc("WebODM Plugin Setting", doc.name, force=True, ignore_permissions=True)

    def test_setting_spoofed_organization_is_overwritten(self):
        frappe.set_user(self.owner)
        frappe.local.webodm_org_cache = {}
        doc = frappe.get_doc({
            "doctype": "WebODM Plugin Setting",
            "plugin": PLUGIN_ID,
            "organization": self.org_b,  # spoof attempt
            "enabled": 1,
        }).insert()
        self.assertEqual(doc.organization, self.org_a)
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        frappe.delete_doc("WebODM Plugin Setting", doc.name, force=True, ignore_permissions=True)

    def test_setting_unique_per_org(self):
        first = self._setting(self.owner, self.org_a)
        frappe.set_user(self.owner)
        frappe.local.webodm_org_cache = {}
        try:
            with self.assertRaises(frappe.ValidationError):
                frappe.get_doc({
                    "doctype": "WebODM Plugin Setting",
                    "plugin": PLUGIN_ID,
                    "enabled": 1,
                }).insert()
        finally:
            frappe.set_user("Administrator")
            frappe.local.webodm_org_cache = {}
            frappe.delete_doc("WebODM Plugin Setting", first.name, force=True, ignore_permissions=True)

    def test_run_stamped_and_status_transitions(self):
        frappe.set_user(self.owner)
        frappe.local.webodm_org_cache = {}
        run = frappe.get_doc({
            "doctype": "WebODM Plugin Run",
            "plugin": PLUGIN_ID,
            "task": self.task_name,
        }).insert()
        self.assertEqual(run.organization, self.org_a)
        self.assertEqual(run.status, "Queued")

        run.status = "Running"
        run.progress = 50
        run.save()
        run.reload()
        self.assertEqual(run.status, "Running")
        self.assertEqual(run.progress, 50)

        run.status = "Completed"
        run.progress = 100
        run.save()
        run.reload()
        self.assertEqual(run.status, "Completed")

        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        frappe.delete_doc("WebODM Plugin Run", run.name, force=True, ignore_permissions=True)

    def test_cross_org_setting_hidden_and_denied(self):
        doc = self._setting(self.owner, self.org_a)

        frappe.set_user(self.intruder)
        frappe.local.webodm_org_cache = {}
        visible = frappe.get_list("WebODM Plugin Setting", pluck="name")
        self.assertNotIn(doc.name, visible)

        loaded = frappe.get_doc("WebODM Plugin Setting", doc.name)
        with self.assertRaises(frappe.PermissionError):
            loaded.check_permission("read")

        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        frappe.delete_doc("WebODM Plugin Setting", doc.name, force=True, ignore_permissions=True)

    def test_cross_org_run_denied(self):
        frappe.set_user(self.owner)
        frappe.local.webodm_org_cache = {}
        run = frappe.get_doc({
            "doctype": "WebODM Plugin Run",
            "plugin": PLUGIN_ID,
            "task": self.task_name,
        }).insert()
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

        frappe.set_user(self.intruder)
        frappe.local.webodm_org_cache = {}
        visible = frappe.get_list("WebODM Plugin Run", pluck="name")
        self.assertNotIn(run.name, visible)
        loaded = frappe.get_doc("WebODM Plugin Run", run.name)
        with self.assertRaises(frappe.PermissionError):
            loaded.check_permission("read")

        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        frappe.delete_doc("WebODM Plugin Run", run.name, force=True, ignore_permissions=True)
