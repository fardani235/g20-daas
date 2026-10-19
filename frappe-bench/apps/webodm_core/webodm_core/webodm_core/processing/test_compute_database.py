"""The compute sweep against a real database.

``test_compute.py`` mocks ``frappe.get_all``, which is how a filter that
PostgreSQL rejects went unnoticed: ``{"terminated_at": ["is", "not set"]}``
on a Datetime column compares a timestamp with ``''``, the query raised and
``reap()`` — the safety net against machines left running — failed on every
run. These tests run the real queries against real rows.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from webodm_core.webodm_core.processing import compute

INSTANCE = "WebODM Compute Instance"
ORG = "Compute Database Org"


def _instance(**fields):
    values = {"status": "Ready", "provider": "aws", "instance_class": "cpu",
              "handle": f"aws:i-{frappe.generate_hash(length=8)}", "requested_at": now_datetime(),
              "ready_at": now_datetime(), "max_lifetime_seconds": 3600,
              "expires_at": add_to_date(now_datetime(), seconds=3600)}
    values.update(fields)
    doc = frappe.get_doc({"doctype": INSTANCE, "task": "no-such-task", "organization": ORG, **values})
    doc.flags.ignore_links = True
    return doc.insert(ignore_permissions=True).name


class TestReaperOnDatabase(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("WebODM Organization", ORG):
            frappe.get_doc({"doctype": "WebODM Organization", "organization_name": ORG}).insert(ignore_permissions=True)

    def setUp(self):
        frappe.db.delete(INSTANCE, {"organization": ORG})
        frappe.db.delete("WebODM Usage Record", {"organization": ORG})
        patch("frappe.log_error").start()
        self.client = MagicMock()
        self.client.destroy.return_value = {}
        self.client.list_managed.return_value = []
        patch.object(compute, "ProvisionerClient", return_value=self.client).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        patch.stopall()
        frappe.db.rollback()

    def status(self, name):
        return frappe.db.get_value(INSTANCE, name, ["status", "terminated_at", "released_at", "release_reason"],
                                   as_dict=True)

    def test_reap_runs(self):
        with patch.dict(frappe.conf, {"provisioner_url": "http://provisioner"}):
            stats = compute.reap()
        self.assertEqual(stats["errors"], 0)

    def test_instance_of_a_deleted_task_is_destroyed_and_says_why(self):
        name = _instance()
        with patch.dict(frappe.conf, {"provisioner_url": "http://provisioner"}):
            stats = compute.reap()
        self.assertGreaterEqual(stats["destroyed"], 1)
        row = self.status(name)
        self.assertEqual((row.status, row.release_reason), ("Terminated", "task-deleted"))
        self.assertIsNotNone(row.released_at)
        self.assertIsNotNone(row.terminated_at)

    def test_failed_instance_whose_destroy_was_never_confirmed_is_picked_up(self):
        # Marked Failed, then the process died before the destroy call.
        leaked = _instance(status="Failed", terminated_at=None)
        closed = _instance(status="Failed", terminated_at=now_datetime())
        never_existed = _instance(status="Failed", handle=None, terminated_at=None)
        with patch.dict(frappe.conf, {"provisioner_url": "http://provisioner"}):
            compute.reap()
        destroyed = [call.args[0] for call in self.client.destroy.call_args_list]
        self.assertIn(frappe.db.get_value(INSTANCE, leaked, "handle"), destroyed)
        self.assertNotIn(frappe.db.get_value(INSTANCE, closed, "handle"), destroyed)
        self.assertIsNotNone(self.status(leaked).terminated_at)
        self.assertIsNone(self.status(never_existed).terminated_at)

    def test_terminating_is_retried_without_moving_the_release_time(self):
        released = add_to_date(now_datetime(), seconds=-1800)
        name = _instance(status="Terminating", released_at=released, release_reason="task:Completed",
                         destroy_attempts=2)
        with patch.dict(frappe.conf, {"provisioner_url": "http://provisioner"}):
            compute.reap()
        row = self.status(name)
        self.assertEqual(row.status, "Terminated")
        self.assertEqual(row.released_at, released)
        self.assertEqual(row.release_reason, "task:Completed")

    def test_scheduler_entry_point(self):
        _instance()
        with patch.dict(frappe.conf, {"provisioner_url": "http://provisioner"}):
            compute.reap_job()
        frappe.log_error.assert_not_called()
