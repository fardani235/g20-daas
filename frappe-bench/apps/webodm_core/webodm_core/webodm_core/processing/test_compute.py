"""On-demand compute, app side: node selection and fallback in the dispatcher,
the Provisioning state transitions, best-effort release, the sweep's rules and
the concurrency caps. Everything is mocked at the frappe / provisioner-client
boundary so no site, no provisioner and no cloud are needed — which is also
the point: nothing here knows what a provider is."""

import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import add_to_date, now_datetime

from webodm_core.webodm_core.processing import compute, task_runner
from webodm_core.webodm_core.processing.node_client import NodeODMTransportError


def _task(**kw):
    t = MagicMock()
    t.name = kw.pop("name", "T-1")
    t.status = kw.pop("status", "Queued")
    t.organization = kw.pop("organization", "Acme")
    t.compute_instance = kw.pop("compute_instance", None)
    t.node_task_id = kw.pop("node_task_id", None)
    t.dispatch_attempts = kw.pop("dispatch_attempts", 0)
    t.poll_failures = kw.pop("poll_failures", 0)
    t.next_attempt_at = None
    t.title = "t"
    t.processing_options = kw.pop("processing_options", None)
    t.images = kw.pop("images", [])
    for k, v in kw.items():
        setattr(t, k, v)
    t.get = lambda key, default=None: getattr(t, key, default)

    def db_set(field, value=None, **_):
        values = field if isinstance(field, dict) else {field: value}
        for k, v in values.items():
            setattr(t, k, v)
        t.writes.update(values)
    t.writes = {}
    t.db_set = MagicMock(side_effect=db_set)
    return t


class TestInstanceClass(unittest.TestCase):
    def test_default_is_cpu(self):
        with patch("frappe.conf", {}):
            self.assertEqual(compute.instance_class_for({}), "cpu")
            self.assertEqual(compute.instance_class_for(None), "cpu")
            self.assertEqual(compute.instance_class_for([{"name": "dsm", "value": True}]), "cpu")

    def test_heavy_options_pick_large(self):
        with patch("frappe.conf", {}):
            self.assertEqual(compute.instance_class_for([{"name": "feature-quality", "value": "ultra"}]), "cpu-large")
            self.assertEqual(compute.instance_class_for({"pc-quality": "Ultra"}), "cpu-large")
            # double-encoded JSON string, as processing_options is stored
            self.assertEqual(compute.instance_class_for('"[{\\"name\\": \\"pc-quality\\", \\"value\\": \\"ultra\\"}]"'), "cpu-large")

    def test_many_images_pick_large(self):
        with patch("frappe.conf", {}):
            self.assertEqual(compute.instance_class_for({}, image_count=499), "cpu")
            self.assertEqual(compute.instance_class_for({}, image_count=501), "cpu-large")
        with patch("frappe.conf", {"compute_large_task_images": 100}):
            self.assertEqual(compute.instance_class_for({}, image_count=150), "cpu-large")

    def test_unavailable_class_degrades_to_provider_default(self):
        with patch("frappe.conf", {}):
            avail = {"cpu": {}, "gpu": {}}
            self.assertEqual(compute.instance_class_for({"pc-quality": "ultra"}, available=avail, default="cpu"), "cpu")
            self.assertEqual(compute.instance_class_for({}, available={"small": {}}, default="small"), "small")


class TestCapacity(unittest.TestCase):
    def test_caps(self):
        with patch("frappe.conf", {"compute_max_instances": 3, "compute_max_instances_per_org": 1}), \
                patch("frappe.db.count", side_effect=[3, 0]):
            with self.assertRaises(compute.CapacityExceeded) as cm:
                compute.check_capacity("Acme")
            self.assertIn("global", str(cm.exception))
        with patch("frappe.conf", {"compute_max_instances": 3, "compute_max_instances_per_org": 1}), \
                patch("frappe.db.count", side_effect=[1, 1]):
            with self.assertRaises(compute.CapacityExceeded) as cm:
                compute.check_capacity("Acme")
            self.assertIn("organization", str(cm.exception))
        with patch("frappe.conf", {}), patch("frappe.db.count", side_effect=[1, 1]):
            compute.check_capacity("Acme")  # defaults 5 / 2


class TestProvisionerClient(unittest.TestCase):
    def _resp(self, status, body=None, text=""):
        r = MagicMock()
        r.status_code = status
        r.content = b"x" if body is not None or text else b""
        r.text = text
        r.json = MagicMock(return_value=body) if body is not None else MagicMock(side_effect=ValueError)
        return r

    def test_errors_and_token_header(self):
        with patch("frappe.conf", {"provisioner_url": "http://prov:5002/"}), \
                patch.dict("os.environ", {"PROVISIONER_API_TOKEN": "s3cret"}), \
                patch.object(compute.requests, "request") as req:
            c = compute.ProvisionerClient()
            req.return_value = self._resp(200, {"enabled": True})
            self.assertEqual(c.provider(), {"enabled": True})
            self.assertEqual(req.call_args.kwargs["headers"]["Authorization"], "Bearer s3cret")
            self.assertEqual(req.call_args.args, ("GET", "http://prov:5002/provider"))

            req.return_value = self._resp(400, {"detail": "unknown class"})
            with self.assertRaises(compute.ProvisionerError) as cm:
                c.create("gpu", "tok", {}, 10)
            self.assertIn("unknown class", str(cm.exception))
            self.assertNotIsInstance(cm.exception, compute.ProvisionerUnavailable)

            req.return_value = self._resp(503, {"detail": "aws down"})
            with self.assertRaises(compute.ProvisionerUnavailable):
                c.describe("aws:i-1")

            import requests as _requests
            req.side_effect = _requests.ConnectionError("refused")
            with self.assertRaises(compute.ProvisionerUnavailable) as cm:
                c.destroy("aws:i-1")
            self.assertNotIn("refused", str(cm.exception))  # no raw transport detail leaks

    def test_unconfigured(self):
        with patch("frappe.conf", {}):
            self.assertFalse(compute.enabled())
            with self.assertRaises(compute.ProvisionerUnavailable):
                compute.ProvisionerClient()


class TestAcquireNode(unittest.TestCase):
    """process_task's node choice: ready cloud node > provision > static; fallbacks."""

    def setUp(self):
        patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)

    def test_no_provisioner_uses_static_node(self):
        task = _task()
        with patch.object(compute, "ready_instance_for", return_value=None), \
                patch.object(compute, "enabled", return_value=False), \
                patch.object(task_runner, "_first_node", return_value={"name": "static", "hostname": "h", "port": 1}):
            node, outcome = task_runner._acquire_node(task)
        self.assertEqual((node["name"], outcome), ("static", "static"))
        self.assertEqual(task.writes, {})

    def test_no_provisioner_no_node(self):
        task = _task()
        with patch.object(compute, "ready_instance_for", return_value=None), \
                patch.object(compute, "enabled", return_value=False), \
                patch.object(task_runner, "_first_node", return_value=None):
            self.assertEqual(task_runner._acquire_node(task), (None, "none"))

    def test_provisioner_moves_task_to_provisioning(self):
        task = _task()
        with patch.object(compute, "ready_instance_for", return_value=None), \
                patch.object(compute, "enabled", return_value=True), \
                patch.object(compute, "request_for_task", return_value="CI-1") as req, \
                patch.object(task_runner, "_first_node") as first:
            node, outcome = task_runner._acquire_node(task)
        self.assertIsNone(node)
        self.assertEqual(outcome, "provisioning")
        self.assertEqual(task.writes["status"], "Provisioning")
        self.assertEqual(task.writes["compute_instance"], "CI-1")
        req.assert_called_once_with(task)
        first.assert_not_called()

    def test_ready_instance_wins(self):
        task = _task(compute_instance="CI-1", status="Provisioning")
        inst = frappe._dict(name="CI-1")
        with patch.object(compute, "ready_instance_for", return_value=inst), \
                patch.object(compute, "node_for_instance", return_value={"name": "CI-1", "hostname": "1.2.3.4", "port": 3000, "token": "t", "compute_instance": "CI-1"}), \
                patch.object(compute, "request_for_task") as req:
            node, outcome = task_runner._acquire_node(task)
        self.assertEqual(outcome, "ready")
        self.assertEqual(node["hostname"], "1.2.3.4")
        req.assert_not_called()

    def test_provisioning_task_without_ready_node_waits(self):
        task = _task(compute_instance="CI-1", status="Provisioning")
        with patch.object(compute, "ready_instance_for", return_value=None), \
                patch.object(compute, "request_for_task") as req:
            self.assertEqual(task_runner._acquire_node(task), (None, "provisioning"))
        req.assert_not_called()

    def test_capacity_waits_without_spending_an_attempt(self):
        task = _task(dispatch_attempts=2)
        with patch.object(compute, "ready_instance_for", return_value=None), \
                patch.object(compute, "enabled", return_value=True), \
                patch.object(compute, "request_for_task", side_effect=compute.CapacityExceeded("global cap")):
            self.assertEqual(task_runner._acquire_node(task), (None, "capacity"))
        self.assertGreater(task.writes["next_attempt_at"], now_datetime())
        self.assertIn("cap", task.writes["last_error"])
        self.assertNotIn("dispatch_attempts", task.writes)
        self.assertNotIn("status", task.writes)

    def test_provisioner_down_falls_back_to_static(self):
        task = _task()
        with patch.object(compute, "ready_instance_for", return_value=None), \
                patch.object(compute, "enabled", return_value=True), \
                patch.object(compute, "request_for_task", side_effect=compute.ProvisionerUnavailable("down")), \
                patch.object(task_runner, "_first_node", return_value={"name": "static", "hostname": "h", "port": 1}):
            node, outcome = task_runner._acquire_node(task)
        self.assertEqual((node["name"], outcome), ("static", "static"))
        self.assertNotIn("status", task.writes)

    def test_no_provider_behind_provisioner_falls_back_to_static(self):
        task = _task()
        with patch.object(compute, "ready_instance_for", return_value=None), \
                patch.object(compute, "enabled", return_value=True), \
                patch.object(compute, "request_for_task", side_effect=compute.ProvisionerError("no compute provider configured")), \
                patch.object(task_runner, "_first_node", return_value={"name": "static", "hostname": "h", "port": 1}):
            node, outcome = task_runner._acquire_node(task)
        self.assertEqual(outcome, "static")


class TestProcessTaskWithCompute(unittest.TestCase):
    def setUp(self):
        patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)

    def test_provisioning_outcome_does_not_defer(self):
        task = _task()
        with patch("frappe.get_doc", return_value=task), \
                patch.object(task_runner, "_acquire_node", return_value=(None, "provisioning")):
            task_runner.process_task("T-1")
        self.assertNotIn("dispatch_attempts", task.writes)

    def test_none_outcome_defers(self):
        task = _task()
        with patch("frappe.get_doc", return_value=task), \
                patch.object(task_runner, "_acquire_node", return_value=(None, "none")):
            task_runner.process_task("T-1")
        self.assertEqual(task.writes["dispatch_attempts"], 1)

    def test_dispatch_to_cloud_node_keeps_instance_link(self):
        task = _task(status="Provisioning", compute_instance="CI-1")
        client = MagicMock(); client.info.return_value = {}
        client.create_task.return_value = {"uuid": "U-9"}
        node = {"name": "CI-1", "hostname": "1.2.3.4", "port": 3000, "token": "t", "compute_instance": "CI-1"}
        with patch("frappe.get_doc", return_value=task), \
                patch.object(task_runner, "_acquire_node", return_value=(node, "ready")), \
                patch.object(task_runner, "_client_for", return_value=client), \
                patch.object(task_runner.storage, "configured", return_value=False), \
                patch.object(task_runner, "_get_task_images", return_value=[("a.jpg", "/x/a.jpg")]):
            task_runner.process_task("T-1")
        self.assertEqual(task.writes["status"], "Running")
        self.assertEqual(task.writes["node_task_id"], "U-9")
        self.assertEqual(task.compute_instance, "CI-1")

    def test_terminal_states_release_compute(self):
        for finisher in (lambda t: task_runner._fail(t, "boom"),
                         lambda t: task_runner._finish(t, "Completed"),
                         lambda t: task_runner._finish(t, "Cancelled", progress=0)):
            task = _task(status="Running", compute_instance="CI-1")
            with patch.object(compute, "release_for_task") as rel:
                finisher(task)
            rel.assert_called_once_with(task)

    def test_poll_uses_the_tasks_own_node(self):
        task = _task(status="Running", node_task_id="U", compute_instance="CI-1")
        client = MagicMock(); client.task_info.return_value = {"status": {"code": 20}, "progress": 50}
        node = {"name": "CI-1", "hostname": "1.2.3.4", "port": 3000, "token": "t"}
        with patch("frappe.get_doc", return_value=task), \
                patch.object(compute, "node_for_instance", return_value=node) as nfi, \
                patch.object(task_runner, "_first_node") as first, \
                patch.object(task_runner, "_client_for", return_value=client) as cf:
            task_runner.poll_task("T-1")
        nfi.assert_called_once_with("CI-1")
        first.assert_not_called()
        self.assertEqual(cf.call_args.args[0]["hostname"], "1.2.3.4")
        self.assertEqual(task.writes["progress"], 50)

    def test_poll_on_a_gone_cloud_node_is_a_tolerated_failure(self):
        task = _task(status="Running", node_task_id="U", compute_instance="CI-1")
        with patch("frappe.get_doc", return_value=task), \
                patch.object(compute, "node_for_instance", return_value=None):
            task_runner.poll_task("T-1")
        self.assertEqual(task.writes["poll_failures"], 1)
        self.assertNotIn("status", task.writes)

    def test_relay_retry_is_a_transport_error_for_the_poller(self):
        task = _task(status="Running", node_task_id="U", poll_failures=0)
        client = MagicMock(); client.task_info.return_value = {"status": {"code": 40}, "progress": 100}
        with patch("frappe.get_doc", return_value=task), \
                patch.object(task_runner, "_node_for_task", return_value={"name": "n", "hostname": "h", "port": 1}), \
                patch.object(task_runner, "_client_for", return_value=client), \
                patch.object(task_runner, "_download_assets", side_effect=task_runner.AssetRelayRetry("cogify down")):
            task_runner.poll_task("T-1")
        self.assertEqual(task.writes["poll_failures"], 1)
        self.assertIn("cogify down", task.writes["last_error"])
        self.assertNotIn("status", task.writes)
        self.assertTrue(issubclass(task_runner.AssetRelayRetry, NodeODMTransportError))


class TestCheckProvisioning(unittest.TestCase):
    def setUp(self):
        patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)
        self.task = _task(status="Provisioning", compute_instance="CI-1")

    def _inst(self, **kw):
        inst = _task(name="CI-1", status=kw.pop("status", "Provisioning"), handle=kw.pop("handle", "aws:i-1"),
                     requested_at=kw.pop("requested_at", now_datetime()), creation=now_datetime(),
                     token="tok", last_error=None, ready_at=None, terminated_at=None, estimated_hourly_cost=0.5,
                     destroy_attempts=0, expires_at=None, max_lifetime_seconds=3600, task="T-1")
        inst.get_password = MagicMock(return_value="tok")
        for k, v in kw.items():
            setattr(inst, k, v)
        return inst

    def _run(self, inst, describe=None, describe_exc=None):
        client = MagicMock()
        if describe_exc:
            client.describe.side_effect = describe_exc
        else:
            client.describe.return_value = describe or {}
        client.destroy.return_value = {}
        docs = {"WebODM Task": self.task, "WebODM Compute Instance": inst}
        with patch("frappe.get_doc", side_effect=lambda dt, name=None: docs[dt]), \
                patch("frappe.db.exists", return_value=True), \
                patch("frappe.conf", {"provisioner_url": "http://p", "compute_provision_timeout_seconds": 600}), \
                patch.object(compute, "ProvisionerClient", return_value=client), \
                patch.object(task_runner, "enqueue_process") as enq:
            compute.check_provisioning("T-1")
        return client, enq

    def test_ready_answer_records_endpoint_and_dispatches(self):
        inst = self._inst()
        client, enq = self._run(inst, describe={"status": "ready", "hostname": "203.0.113.9", "port": 3000})
        self.assertEqual(inst.writes["status"], "Ready")
        self.assertEqual(inst.writes["hostname"], "203.0.113.9")
        self.assertEqual(inst.writes["port"], 3000)
        client.describe.assert_called_once_with("aws:i-1", token="tok")
        enq.assert_called_once_with("T-1")
        self.assertEqual(self.task.writes, {})  # process_task flips it to Running

    def test_pending_answer_waits(self):
        inst = self._inst()
        _, enq = self._run(inst, describe={"status": "pending", "detail": "nodeodm not answering"})
        enq.assert_not_called()
        self.assertNotIn("status", inst.writes)
        self.assertEqual(self.task.status, "Provisioning")

    def test_timeout_fails_instance_and_requeues_with_backoff(self):
        inst = self._inst(requested_at=add_to_date(now_datetime(), seconds=-601))
        client, enq = self._run(inst)
        self.assertEqual(inst.writes["status"], "Failed")  # destroyed, reason kept
        self.assertIsNotNone(inst.writes["terminated_at"])
        self.assertIn("timed out", inst.last_error)
        client.destroy.assert_called_once_with("aws:i-1")
        client.describe.assert_not_called()
        self.assertEqual(self.task.writes["status"], "Queued")
        self.assertIsNone(self.task.writes["compute_instance"])
        self.assertEqual(self.task.writes["dispatch_attempts"], 1)
        self.assertGreater(self.task.writes["next_attempt_at"], now_datetime())
        enq.assert_not_called()

    def test_terminated_at_provider_requeues(self):
        inst = self._inst()
        client, _ = self._run(inst, describe={"status": "terminated", "detail": "instance not found"})
        self.assertEqual(self.task.writes["status"], "Queued")
        client.destroy.assert_called_once()

    def test_provisioner_unreachable_is_tolerated(self):
        inst = self._inst()
        self._run(inst, describe_exc=compute.ProvisionerUnavailable("down"))
        self.assertNotIn("status", inst.writes)
        self.assertEqual(self.task.status, "Provisioning")
        self.assertIn("down", inst.writes["last_error"])

    def test_already_ready_instance_just_dispatches(self):
        inst = self._inst(status="Ready", hostname="1.1.1.1")
        client, enq = self._run(inst)
        enq.assert_called_once_with("T-1")
        client.describe.assert_not_called()


class TestDestroyAndRelease(unittest.TestCase):
    def setUp(self):
        self.log = patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)

    def _inst(self, **kw):
        inst = _task(name="CI-1", status=kw.pop("status", "Ready"), handle=kw.pop("handle", "aws:i-1"),
                     requested_at=add_to_date(now_datetime(), seconds=-3600), ready_at=add_to_date(now_datetime(), seconds=-1800),
                     creation=now_datetime(), estimated_hourly_cost=2.0, destroy_attempts=0, last_error=None,
                     terminated_at=None)
        for k, v in kw.items():
            setattr(inst, k, v)
        return inst

    def test_destroy_success_closes_with_cost_estimate(self):
        inst = self._inst()
        client = MagicMock(); client.destroy.return_value = {}
        with patch("frappe.conf", {"provisioner_url": "http://p"}), patch.object(compute, "ProvisionerClient", return_value=client):
            self.assertTrue(compute.destroy_instance(inst))
        self.assertEqual(inst.writes["status"], "Terminated")
        self.assertAlmostEqual(inst.writes["estimated_cost"], 1.0, places=2)  # 0.5 h x 2.0/h from ready_at
        self.assertIsNotNone(inst.writes["terminated_at"])

    def test_destroy_failure_leaves_terminating_for_the_sweep(self):
        inst = self._inst()
        client = MagicMock(); client.destroy.side_effect = compute.ProvisionerUnavailable("down")
        with patch("frappe.conf", {"provisioner_url": "http://p"}), patch.object(compute, "ProvisionerClient", return_value=client):
            self.assertFalse(compute.destroy_instance(inst))
        self.assertEqual(inst.writes["status"], "Terminating")
        self.assertEqual(inst.writes["destroy_attempts"], 1)
        self.assertIn("destroy failed", inst.writes["last_error"])

    def test_destroy_without_handle_closes_directly(self):
        inst = self._inst(handle=None, status="Requested")
        with patch.object(compute, "ProvisionerClient") as pc:
            self.assertTrue(compute.destroy_instance(inst))
        pc.assert_not_called()
        self.assertEqual(inst.writes["status"], "Terminated")

    def test_release_never_raises(self):
        task = _task(compute_instance="CI-1")
        with patch("frappe.db.exists", return_value=True), patch("frappe.get_doc", side_effect=RuntimeError("db gone")):
            compute.release_for_task(task)  # no exception
        self.log.assert_called()
        task2 = _task(compute_instance=None)
        with patch("frappe.db.exists") as ex:
            compute.release_for_task(task2)
        ex.assert_not_called()


class TestReaper(unittest.TestCase):
    def setUp(self):
        patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)

    def _inst(self, **kw):
        inst = _task(name=kw.pop("name", "CI-1"), status=kw.pop("status", "Ready"), handle=kw.pop("handle", "aws:i-1"),
                     requested_at=kw.pop("requested_at", now_datetime()), ready_at=None, creation=now_datetime(),
                     estimated_hourly_cost=0, destroy_attempts=0, last_error=None, terminated_at=None,
                     expires_at=kw.pop("expires_at", None), max_lifetime_seconds=3600, task=kw.pop("task", "T-1"))
        for k, v in kw.items():
            setattr(inst, k, v)
        return inst

    def _reap(self, inst, task, *, task_exists=True, managed=None):
        client = MagicMock(); client.destroy.return_value = {}
        client.list_managed.return_value = managed or []
        docs = {("WebODM Compute Instance", inst.name): inst, ("WebODM Task", inst.task): task}
        def get_all(dt, **kw):
            if kw.get("pluck") == "handle":
                return [inst.handle]
            if kw.get("pluck") == "name" and kw.get("filters", {}).get("status") != "Failed":
                return [inst.name]
            return []

        with patch("frappe.get_all", side_effect=get_all), \
                patch("frappe.get_doc", side_effect=lambda dt, name: docs[(dt, name)]), \
                patch("frappe.db.exists", return_value=task_exists), \
                patch("frappe.conf", {"provisioner_url": "http://p", "compute_provision_timeout_seconds": 600, "compute_orphan_grace_seconds": 300}), \
                patch.object(compute, "ProvisionerClient", return_value=client):
            stats = compute.reap()
        return stats, client

    def test_terminal_task_releases_instance(self):
        inst = self._inst()
        task = _task(status="Completed", compute_instance="CI-1")
        stats, client = self._reap(inst, task)
        client.destroy.assert_called_once_with("aws:i-1")
        self.assertEqual(inst.writes["status"], "Terminated")
        self.assertEqual(stats["destroyed"], 1)

    def test_missing_task_releases_instance(self):
        inst = self._inst()
        _, client = self._reap(inst, None, task_exists=False)
        client.destroy.assert_called_once()

    def test_running_task_on_live_instance_is_left_alone(self):
        inst = self._inst()
        task = _task(status="Running", compute_instance="CI-1")
        _, client = self._reap(inst, task)
        client.destroy.assert_not_called()
        self.assertEqual(inst.writes, {})
        self.assertEqual(task.writes, {})

    def test_task_moved_to_another_instance_releases_old_one(self):
        inst = self._inst()
        task = _task(status="Running", compute_instance="CI-2")
        _, client = self._reap(inst, task)
        client.destroy.assert_called_once()

    def test_lifetime_budget_kills_instance_and_fails_task(self):
        inst = self._inst(expires_at=add_to_date(now_datetime(), seconds=-1))
        task = _task(status="Running", compute_instance="CI-1")
        _, client = self._reap(inst, task)
        client.destroy.assert_called_once()
        self.assertEqual(task.writes["status"], "Failed")
        self.assertIn("lifetime budget", task.writes["last_error"])
        self.assertIn("max lifetime", inst.last_error)

    def test_never_came_up_is_requeued(self):
        inst = self._inst(status="Provisioning", requested_at=add_to_date(now_datetime(), seconds=-700))
        task = _task(status="Provisioning", compute_instance="CI-1")
        with patch.object(task_runner, "_defer_dispatch") as defer:
            _, client = self._reap(inst, task)
        client.destroy.assert_called_once()
        self.assertEqual(task.writes["status"], "Queued")
        defer.assert_called_once()

    def test_terminating_is_retried(self):
        inst = self._inst(status="Terminating")
        task = _task(status="Completed", compute_instance="CI-1")
        _, client = self._reap(inst, task)
        client.destroy.assert_called_once()
        self.assertEqual(inst.writes["status"], "Terminated")

    def test_orphans_older_than_grace_are_destroyed(self):
        inst = self._inst()
        task = _task(status="Running", compute_instance="CI-1")
        old = add_to_date(now_datetime(), seconds=-1000).isoformat()
        fresh = now_datetime().isoformat()
        managed = [
            {"handle": "aws:i-1", "status": "running", "launched_at": old},        # ours, live
            {"handle": "aws:i-orphan", "status": "running", "launched_at": old},   # nobody's
            {"handle": "aws:i-new", "status": "running", "launched_at": fresh},    # may be in flight
            {"handle": "aws:i-dead", "status": "terminated", "launched_at": old},
        ]
        stats, client = self._reap(inst, task, managed=managed)
        self.assertEqual([c.args[0] for c in client.destroy.call_args_list], ["aws:i-orphan"])
        self.assertEqual(stats["orphans"], 1)


if __name__ == "__main__":
    unittest.main()
