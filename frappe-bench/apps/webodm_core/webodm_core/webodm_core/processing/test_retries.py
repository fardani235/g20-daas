"""Retry / backoff / single-state-machine behaviour of the processing pipeline.

Before: a dispatch failure re-ran every 60s forever with no counter; one
transient task_info error marked a multi-hour job Failed; and
api.task.get_task_progress re-implemented polling inline, racing poll_task.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from webodm_core.webodm_core.processing import task_runner
from webodm_core.webodm_core.processing.node_client import NodeODMError


def _user(email):
    if not frappe.db.exists("User", email):
        frappe.get_doc({"doctype": "User", "email": email, "first_name": "rt",
                        "send_welcome_email": 0}).insert(ignore_permissions=True)
    u = frappe.get_doc("User", email); u.roles = []
    u.append("roles", {"role": "WebODM User"}); u.save(ignore_permissions=True)
    return email


class TestBackoffSchedule(FrappeTestCase):
    def test_exponential_and_capped(self):
        self.assertEqual(task_runner._backoff_seconds(0), 0)
        self.assertEqual(task_runner._backoff_seconds(1), 60)
        self.assertEqual(task_runner._backoff_seconds(2), 120)
        self.assertEqual(task_runner._backoff_seconds(3), 240)
        self.assertEqual(task_runner._backoff_seconds(6), 1800)
        self.assertEqual(task_runner._backoff_seconds(50), 1800)


class _TaskFixture(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = _user("retry_owner@example.com")
        cls.org = frappe.get_doc({"doctype": "WebODM Organization",
                                  "organization_name": "Retry Org"}).insert(ignore_permissions=True).name
        frappe.get_doc({"doctype": "WebODM Org Membership", "user": cls.user,
                        "organization": cls.org, "role": "Owner"}).insert(ignore_permissions=True)
        frappe.local.webodm_org_cache = {}
        frappe.set_user(cls.user)
        cls.project = frappe.get_doc({"doctype": "WebODM Project", "title": "Retry Project"}).insert().name
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}

    def _task(self, **kw):
        frappe.local.webodm_org_cache = {}
        frappe.set_user(self.user)
        doc = {"doctype": "WebODM Task", "project": self.project, "title": "Retry Task", "status": "Queued"}
        doc.update(kw)
        t = frappe.get_doc(doc).insert()
        frappe.set_user("Administrator")
        self._tasks.append(t.name)
        return t

    def setUp(self):
        self._tasks = []
        frappe.local.webodm_org_cache = {}

    def tearDown(self):
        frappe.set_user("Administrator")
        for name in self._tasks:
            frappe.cache.delete(task_runner._sync_lock_key(name))
            frappe.delete_doc("WebODM Task", name, ignore_permissions=True, force=True)


class TestDispatchBackoff(_TaskFixture):
    def test_no_node_defers_with_backoff_instead_of_spinning(self):
        t = self._task()
        with patch.object(task_runner, "_node_client", return_value=None), patch("frappe.log_error"):
            task_runner.process_task(t.name)
        t.reload()
        self.assertEqual(t.status, "Queued")
        self.assertEqual(t.dispatch_attempts, 1)
        self.assertIn("No processing nodes", t.last_error)
        self.assertIsNotNone(t.next_attempt_at)
        delay = (t.next_attempt_at - now_datetime()).total_seconds()
        self.assertTrue(50 <= delay <= 70, delay)

    def test_backoff_grows_and_eventually_fails(self):
        t = self._task()
        client = MagicMock()
        client.info.side_effect = NodeODMError("connection refused")
        with patch.object(task_runner, "_node_client", return_value=client), patch("frappe.log_error"):
            for i in range(1, task_runner.MAX_DISPATCH_ATTEMPTS + 1):
                # Clear the window so each call is a real attempt.
                frappe.db.set_value("WebODM Task", t.name, "next_attempt_at", None)
                task_runner.process_task(t.name)
                t.reload()
                if i < task_runner.MAX_DISPATCH_ATTEMPTS:
                    self.assertEqual(t.status, "Queued", i)
                    self.assertEqual(t.dispatch_attempts, i)
                    delay = (t.next_attempt_at - now_datetime()).total_seconds()
                    self.assertAlmostEqual(delay, task_runner._backoff_seconds(i), delta=10)
        self.assertEqual(t.status, "Failed")
        self.assertIn("Giving up", t.last_error)
        self.assertIsNone(t.next_attempt_at)

    def test_process_task_respects_backoff_window(self):
        t = self._task()
        frappe.db.set_value("WebODM Task", t.name, "next_attempt_at", add_to_date(now_datetime(), minutes=5))
        with patch.object(task_runner, "_node_client") as nc:
            task_runner.process_task(t.name)
        nc.assert_not_called()

    def test_sweep_skips_tasks_still_in_backoff(self):
        due = self._task()
        waiting = self._task()
        never_failed = self._task()
        frappe.db.set_value("WebODM Task", due.name, "next_attempt_at", add_to_date(now_datetime(), minutes=-1))
        frappe.db.set_value("WebODM Task", waiting.name, "next_attempt_at", add_to_date(now_datetime(), minutes=5))
        with patch("frappe.enqueue") as enq:
            task_runner.process_pending_tasks()
        queued = {c.kwargs["task_name"] for c in enq.call_args_list}
        self.assertIn(due.name, queued)
        self.assertIn(never_failed.name, queued)
        self.assertNotIn(waiting.name, queued)

    def test_no_images_is_a_permanent_failure(self):
        t = self._task()
        client = MagicMock(); client.info.return_value = {"version": "x"}
        with patch.object(task_runner, "_node_client", return_value=client), patch("frappe.log_error"):
            task_runner.process_task(t.name)
        t.reload()
        self.assertEqual(t.status, "Failed")
        self.assertEqual(t.dispatch_attempts, 0)
        client.create_task.assert_not_called()

    def test_success_resets_counters(self):
        t = self._task()
        frappe.db.set_value("WebODM Task", t.name, {"dispatch_attempts": 3, "last_error": "old"})
        client = MagicMock(); client.info.return_value = {}
        client.create_task.return_value = {"uuid": "NODE-1"}
        with patch.object(task_runner, "_node_client", return_value=client), \
             patch.object(task_runner, "_get_task_images", return_value=[("a.jpg", "/tmp/a.jpg")]):
            task_runner.process_task(t.name)
        t.reload()
        self.assertEqual(t.status, "Running")
        self.assertEqual(t.node_task_id, "NODE-1")
        self.assertEqual(t.dispatch_attempts, 0)
        self.assertFalse(t.last_error)
        self.assertIsNone(t.next_attempt_at)


class TestPollTolerance(_TaskFixture):
    def _running(self):
        return self._task(status="Running", node_task_id="NODE-R")

    def test_single_transient_error_does_not_fail_the_task(self):
        t = self._running()
        client = MagicMock(); client.task_info.side_effect = NodeODMError("timeout")
        with patch("frappe.log_error"):
            task_runner.sync_task_with_node(t, client)
        t.reload()
        self.assertEqual(t.status, "Running")
        self.assertEqual(t.poll_failures, 1)
        self.assertIn("timeout", t.last_error)

    def test_consecutive_failures_eventually_fail(self):
        t = self._running()
        client = MagicMock(); client.task_info.side_effect = NodeODMError("down")
        with patch("frappe.log_error"):
            for _ in range(task_runner.MAX_POLL_FAILURES):
                task_runner.sync_task_with_node(t, client)
        t.reload()
        self.assertEqual(t.status, "Failed")
        self.assertIn("Lost contact", t.last_error)

    def test_success_resets_failure_counter(self):
        t = self._running()
        frappe.db.set_value("WebODM Task", t.name, "poll_failures", 4); t.reload()
        client = MagicMock(); client.task_info.return_value = {"status": {"code": 20}, "progress": 42.0}
        out = task_runner.sync_task_with_node(t, client)
        t.reload()
        self.assertEqual(t.poll_failures, 0)
        self.assertEqual(t.progress, 42)
        self.assertEqual(out["node_progress"], 42.0)
        self.assertEqual(out["node_status_code"], 20)

    def test_node_failure_records_error_message(self):
        t = self._running()
        client = MagicMock()
        client.task_info.return_value = {"status": {"code": 30, "errorMessage": "Not enough overlap"}, "progress": 10}
        with patch("frappe.log_error"):
            task_runner.sync_task_with_node(t, client)
        t.reload()
        self.assertEqual(t.status, "Failed")
        self.assertIn("Not enough overlap", t.last_error)

    def test_completed_triggers_download_once(self):
        t = self._running()
        client = MagicMock(); client.task_info.return_value = {"status": {"code": 40}, "progress": 100}
        with patch.object(task_runner, "_download_assets") as dl:
            task_runner.sync_task_with_node(t, client)
        dl.assert_called_once()

    def test_lock_makes_concurrent_sync_a_noop(self):
        t = self._running()
        client = MagicMock(); client.task_info.return_value = {"status": {"code": 20}, "progress": 50}
        key = task_runner._sync_lock_key(t.name)
        frappe.cache.set(key, "1", ex=30)  # simulate poll_task mid-flight
        try:
            out = task_runner.sync_task_with_node(t, client)
        finally:
            frappe.cache.delete(key)
        client.task_info.assert_not_called()
        self.assertTrue(out.get("locked"))
        self.assertEqual(out["status"], "Running")

    def test_lock_is_released_after_sync(self):
        t = self._running()
        client = MagicMock(); client.task_info.return_value = {"status": {"code": 20}, "progress": 5}
        task_runner.sync_task_with_node(t, client)
        self.assertIsNone(frappe.cache.get(task_runner._sync_lock_key(t.name)))

    def test_lock_is_released_when_node_raises(self):
        t = self._running()
        client = MagicMock(); client.task_info.side_effect = NodeODMError("x")
        with patch("frappe.log_error"):
            task_runner.sync_task_with_node(t, client)
        self.assertIsNone(frappe.cache.get(task_runner._sync_lock_key(t.name)))


class TestGetTaskProgressDelegates(_TaskFixture):
    """The API must not contain its own polling logic."""

    def _call(self, task_name):
        from webodm_core.api import task as task_api
        frappe.local.form_dict = frappe._dict(task_name=task_name)
        frappe.local.request = frappe._dict(data=b"")
        return task_api.get_task_progress()

    def test_running_task_goes_through_sync_task_with_node(self):
        t = self._task(status="Running", node_task_id="NODE-API")
        frappe.set_user(self.user)
        with patch.object(task_runner, "sync_task_with_node",
                          return_value={"status": "Running", "progress": 33, "node_progress": 33.3,
                                        "node_status_code": 20}) as sync:
            out = self._call(t.name)
        sync.assert_called_once()
        self.assertEqual(sync.call_args.args[0].name, t.name)
        self.assertEqual(out["node_progress"], 33.3)
        self.assertEqual(out["node_status_code"], 20)

    def test_non_running_task_does_not_touch_the_node(self):
        t = self._task(status="Pending")
        frappe.set_user(self.user)
        with patch.object(task_runner, "sync_task_with_node") as sync:
            out = self._call(t.name)
        sync.assert_not_called()
        self.assertEqual(out["status"], "Pending")

    def test_api_source_has_no_inline_task_info_call(self):
        import inspect
        from webodm_core.api import task as task_api
        src = inspect.getsource(task_api.get_task_progress)
        self.assertNotIn("task_info", src)
        self.assertNotIn("_status_action", src)
