"""Dispatch backoff, poll-failure tolerance, and the transient/permanent split.

Regressions guarded here:
- one transient task_info error used to flip a multi-hour job to Failed;
- dispatch failures (no node, node down) used to loop silently every minute
  forever with no counter, no backoff and no terminal state;
- enqueues used job_name (not deduplicated) so sweeps could stack jobs.
"""
import unittest

import frappe
from unittest.mock import MagicMock, patch

from frappe.utils import add_to_date, now_datetime

from webodm_core.webodm_core.processing import task_runner
from webodm_core.webodm_core.processing.node_client import NodeODMError, NodeODMTransportError


def _task(**kw):
    t = MagicMock()
    t.name = kw.pop("name", "T-1")
    t.status = kw.pop("status", "Queued")
    t.node_task_id = kw.pop("node_task_id", None)
    # No on-demand node unless a test says so: the runner prefers a task's
    # compute instance over the static node when one is linked.
    t.compute_instance = kw.pop("compute_instance", None)
    t.dispatch_attempts = kw.pop("dispatch_attempts", 0)
    t.poll_failures = kw.pop("poll_failures", 0)
    t.title = "t"
    t.processing_options = None
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _db_set_dict(task):
    """Merge all db_set calls (dict or (field, value) form) into one dict."""
    out = {}
    for call in task.db_set.call_args_list:
        a = call.args
        if len(a) == 1 and isinstance(a[0], dict):
            out.update(a[0])
        else:
            out[a[0]] = a[1]
    return out


class TestBackoff(unittest.TestCase):
    def test_exponential_with_cap(self):
        b = task_runner._backoff_seconds
        self.assertEqual(b(1), 60)
        self.assertEqual(b(2), 120)
        self.assertEqual(b(3), 240)
        self.assertEqual(b(30), task_runner.DISPATCH_BACKOFF_CAP_SECONDS)


class TestEnqueueIsDeduplicated(unittest.TestCase):
    def test_process_uses_job_id_and_deduplicate(self):
        with patch("frappe.enqueue") as enq:
            task_runner.enqueue_process("T-9")
        kw = enq.call_args.kwargs
        self.assertEqual(kw["job_id"], "webodm:process:T-9")
        self.assertTrue(kw["deduplicate"])
        self.assertEqual(kw["queue"], "long")

    def test_poll_uses_job_id_and_deduplicate(self):
        with patch("frappe.enqueue") as enq:
            task_runner.enqueue_poll("T-9")
        kw = enq.call_args.kwargs
        self.assertEqual(kw["job_id"], "webodm:poll:T-9")
        self.assertTrue(kw["deduplicate"])


class TestSweepHonoursBackoff(unittest.TestCase):
    def test_skips_tasks_whose_next_attempt_is_in_the_future(self):
        rows = [
            {"name": "READY", "next_attempt_at": None},
            {"name": "PAST", "next_attempt_at": add_to_date(now_datetime(), seconds=-5)},
            {"name": "LATER", "next_attempt_at": add_to_date(now_datetime(), seconds=600)},
        ]
        with patch("frappe.get_all", return_value=[frappe._dict(r) for r in rows]), \
                patch.object(task_runner, "enqueue_process") as enq:
            task_runner.process_pending_tasks()
        self.assertEqual([c.args[0] for c in enq.call_args_list], ["READY", "PAST"])


class TestDispatchRetries(unittest.TestCase):
    def setUp(self):
        # now_datetime() lazily caches System Settings via frappe.get_doc, which
        # the tests below mock; warm it first so the module passes on its own.
        now_datetime()
        self.log = patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)

    def test_no_node_defers_with_backoff_not_fail(self):
        task = _task(dispatch_attempts=0)
        with patch("frappe.get_doc", return_value=task), patch.object(task_runner, "_first_node", return_value=None):
            task_runner.process_task("T-1")
        w = _db_set_dict(task)
        self.assertEqual(w["dispatch_attempts"], 1)
        self.assertGreater(w["next_attempt_at"], now_datetime())
        self.assertIn("no processing nodes", w["last_error"])
        self.assertNotIn("status", w)

    def test_unreachable_node_defers(self):
        task = _task(dispatch_attempts=2)
        client = MagicMock(); client.info.side_effect = NodeODMTransportError("refused")
        with patch("frappe.get_doc", return_value=task), \
                patch.object(task_runner, "_first_node", return_value={"name": "n", "hostname": "h", "port": 1}), \
                patch.object(task_runner, "_client_for", return_value=client):
            task_runner.process_task("T-1")
        w = _db_set_dict(task)
        self.assertEqual(w["dispatch_attempts"], 3)
        self.assertNotIn("status", w)

    def test_gives_up_after_max_attempts(self):
        task = _task(dispatch_attempts=task_runner.MAX_DISPATCH_ATTEMPTS - 1)
        with patch("frappe.get_doc", return_value=task), patch.object(task_runner, "_first_node", return_value=None):
            task_runner.process_task("T-1")
        w = _db_set_dict(task)
        self.assertEqual(w["status"], "Failed")
        self.assertIn("giving up", w["last_error"])

    def test_upload_transport_error_defers_but_rejection_fails(self):
        for exc, expect_status in ((NodeODMTransportError("timeout"), None), (NodeODMError("too many images"), "Failed")):
            task = _task(dispatch_attempts=0)
            client = MagicMock(); client.info.return_value = {}
            client.create_task.side_effect = exc
            with patch("frappe.get_doc", return_value=task), \
                    patch.object(task_runner, "_first_node", return_value={"name": "n", "hostname": "h", "port": 1}), \
                    patch.object(task_runner, "_client_for", return_value=client), \
                    patch.object(task_runner, "_get_task_images", return_value=[("a.jpg", "/x/a.jpg")]):
                task_runner.process_task("T-1")
            w = _db_set_dict(task)
            self.assertEqual(w.get("status"), expect_status, exc)

    def test_no_images_is_permanent(self):
        task = _task()
        client = MagicMock(); client.info.return_value = {}
        with patch("frappe.get_doc", return_value=task), \
                patch.object(task_runner, "_first_node", return_value={"name": "n", "hostname": "h", "port": 1}), \
                patch.object(task_runner, "_client_for", return_value=client), \
                patch.object(task_runner, "_get_task_images", return_value=[]):
            task_runner.process_task("T-1")
        self.assertEqual(_db_set_dict(task)["status"], "Failed")
        client.create_task.assert_not_called()

    def test_success_resets_counters(self):
        task = _task(dispatch_attempts=3, poll_failures=2)
        client = MagicMock(); client.info.return_value = {}
        client.create_task.return_value = {"uuid": "U-1"}
        with patch("frappe.get_doc", return_value=task), \
                patch.object(task_runner, "_first_node", return_value={"name": "n", "hostname": "h", "port": 1}), \
                patch.object(task_runner, "_client_for", return_value=client), \
                patch.object(task_runner, "_get_task_images", return_value=[("a.jpg", "/x/a.jpg")]):
            task_runner.process_task("T-1")
        w = _db_set_dict(task)
        self.assertEqual(w["status"], "Running")
        self.assertEqual(w["node_task_id"], "U-1")
        self.assertEqual(w["dispatch_attempts"], 0)
        self.assertIsNone(w["next_attempt_at"])
        self.assertIsNone(w["last_error"])


class TestPollTolerance(unittest.TestCase):
    def setUp(self):
        now_datetime()  # see TestDispatchRetries.setUp
        self.log = patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)
        self.node = {"name": "n", "hostname": "h", "port": 1}

    def _poll(self, task, client):
        with patch("frappe.get_doc", return_value=task), \
                patch.object(task_runner, "_first_node", return_value=self.node), \
                patch.object(task_runner, "_client_for", return_value=client):
            task_runner.poll_task(task.name)

    def test_one_transport_error_does_not_fail_the_task(self):
        task = _task(status="Running", node_task_id="U", poll_failures=0)
        client = MagicMock(); client.task_info.side_effect = NodeODMTransportError("timeout")
        self._poll(task, client)
        w = _db_set_dict(task)
        self.assertEqual(w["poll_failures"], 1)
        self.assertNotIn("status", w)

    def test_fails_after_max_consecutive_transport_errors(self):
        task = _task(status="Running", node_task_id="U", poll_failures=task_runner.MAX_POLL_FAILURES - 1)
        client = MagicMock(); client.task_info.side_effect = NodeODMTransportError("timeout")
        self._poll(task, client)
        w = _db_set_dict(task)
        self.assertEqual(w["status"], "Failed")
        self.assertIn("consecutive polls", w["last_error"])

    def test_unknown_task_on_node_is_permanent(self):
        task = _task(status="Running", node_task_id="U", poll_failures=0)
        client = MagicMock(); client.task_info.side_effect = NodeODMError("U not found")
        self._poll(task, client)
        self.assertEqual(_db_set_dict(task)["status"], "Failed")

    def test_successful_poll_resets_failure_counter(self):
        task = _task(status="Running", node_task_id="U", poll_failures=4)
        client = MagicMock(); client.task_info.return_value = {"status": {"code": 20}, "progress": 42}
        self._poll(task, client)
        w = _db_set_dict(task)
        self.assertEqual(w["poll_failures"], 0)
        self.assertEqual(w["progress"], 42)

    def test_node_failure_message_is_recorded(self):
        task = _task(status="Running", node_task_id="U")
        client = MagicMock(); client.task_info.return_value = {"status": {"code": 30, "errorMessage": "Not enough features"}, "progress": 10}
        self._poll(task, client)
        w = _db_set_dict(task)
        self.assertEqual(w["status"], "Failed")
        self.assertIn("Not enough features", w["last_error"])

    def test_download_transport_error_keeps_running_for_retry(self):
        task = _task(status="Running", node_task_id="U", poll_failures=0)
        client = MagicMock(); client.task_info.return_value = {"status": {"code": 40}, "progress": 100}
        with patch.object(task_runner, "_download_assets", side_effect=NodeODMTransportError("reset")):
            self._poll(task, client)
        w = _db_set_dict(task)
        self.assertEqual(w["poll_failures"], 1)
        self.assertNotIn("status", w)


if __name__ == "__main__":
    unittest.main()
