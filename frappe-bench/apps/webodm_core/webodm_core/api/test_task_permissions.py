"""Cross-user access control for the custom task endpoints.

Regression tests for the IDOR where any authenticated user could read/act on
another user's task via the custom API (frappe.get_doc does NOT enforce read
permission unless check_permission is passed). These prove a non-owner is
denied and the owner is allowed.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import task as task_api
from webodm_core.api import tiles as tiles_api


def _make_user(email):
    """A WebODM end-user: has the 'WebODM User' role (DocType access) but is NOT
    an admin, so the owner-scoped permission_query_conditions/has_permission hooks
    actually gate their access — mirroring a real SaaS tenant account."""
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


class TestTaskAccessControl(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = _make_user("owner_a@example.com")
        cls.other = _make_user("intruder_b@example.com")

        frappe.set_user(cls.owner)
        project = frappe.get_doc({
            "doctype": "WebODM Project",
            "title": "Owner A Project",
        }).insert()
        cls.project_name = project.name
        task = frappe.get_doc({
            "doctype": "WebODM Task",
            "project": project.name,
            "title": "Owner A Task",
            "status": "Pending",
        }).insert()
        cls.task_name = task.name
        frappe.set_user("Administrator")

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        frappe.delete_doc("WebODM Task", cls.task_name, force=True, ignore_permissions=True)
        frappe.delete_doc("WebODM Project", cls.project_name, force=True, ignore_permissions=True)
        super().tearDownClass()

    def tearDown(self):
        frappe.set_user("Administrator")

    def _post(self, **data):
        frappe.local.form_dict = frappe._dict(data)
        frappe.local.request = frappe._dict(data=b"")

    def test_non_owner_cannot_read_task_progress(self):
        frappe.set_user(self.other)
        self._post(task_name=self.task_name)
        with self.assertRaises(frappe.PermissionError):
            task_api.get_task_progress()

    def test_non_owner_cannot_read_task_console(self):
        frappe.set_user(self.other)
        self._post(task_name=self.task_name)
        with self.assertRaises(frappe.PermissionError):
            task_api.get_task_console()

    def test_non_owner_cannot_cancel_task(self):
        frappe.set_user(self.other)
        self._post(task_name=self.task_name)
        with self.assertRaises(frappe.PermissionError):
            task_api.cancel_task()

    def test_non_owner_cannot_process_task(self):
        frappe.set_user(self.other)
        self._post(task_name=self.task_name)
        with self.assertRaises(frappe.PermissionError):
            task_api.process_task()

    def test_non_owner_cannot_access_tiles(self):
        frappe.set_user(self.other)
        with self.assertRaises(frappe.PermissionError):
            tiles_api.info(self.task_name, "orthophoto")

    def test_owner_can_read_task_progress(self):
        frappe.set_user(self.owner)
        self._post(task_name=self.task_name)
        # Should not raise PermissionError (may return with no node data attached).
        result = task_api.get_task_progress()
        self.assertEqual(result["name"], self.task_name)
