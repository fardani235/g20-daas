"""From a task being started to a usage record, through the real pipeline.

The other tests build the rows the pipeline leaves behind. This one lets
the pipeline build them: the real ``task_runner`` and ``compute`` functions
run against real tasks and real ``WebODM Compute Instance`` rows, with only
the two things outside the database replaced — the provisioner (an in-memory
provider) and the processing node (a scripted NodeODM). The clock the
pipeline stamps with is controlled, so every expected quantity is exact.

What it proves is that the three facts metering depends on
(``dispatched_at``, ``released_at``, ``release_reason``) are written by the
code paths that actually run, in each way a job can end, and that the sweep
then counts or waives the machine's time as the specification says.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.metering import ledger, report, sweep, timeutil
from webodm_core.metering.testing import INSTANCE, as_user, make_org, make_user, usage_rows, wipe
from webodm_core.testing import make_dataset, make_task
from webodm_core.webodm_core.processing import compute, task_runner
from webodm_core.webodm_core.processing.node_client import NodeODMTransportError

PROJECT = "Meter Pipeline Project"
T0 = dt.datetime(2026, 10, 19, 10, 0, 0)  # site-local, like every pipeline timestamp


class FakeProvisioner:
    """The provisioner service, in memory. Same surface as ``compute.ProvisionerClient``."""

    def __init__(self):
        self.state = "pending"
        self.destroy_error = None
        self.created, self.destroyed = [], []

    def provider(self):
        return {"enabled": True, "provider": "fake", "classes": {"cpu": {}, "cpu-large": {}},
                "default_class": "cpu", "max_lifetime_seconds": 4 * 3600}

    def create(self, instance_class, token, labels, max_lifetime_seconds):
        handle = f"fake:{len(self.created) + 1}"
        self.created.append(handle)
        return {"handle": handle, "provider": "fake", "instance_class": instance_class, "hourly_cost": 0.5}

    def describe(self, handle, token=None):
        return {"status": self.state, "hostname": "10.0.0.9", "port": 3000}

    def destroy(self, handle):
        if self.destroy_error:
            raise self.destroy_error
        self.destroyed.append(handle)
        return {}

    def list_managed(self):
        return []


class FakeNode:
    """A NodeODM that does what the test tells it to."""

    def __init__(self):
        self.code = task_runner.NODE_STATUS_RUNNING
        self.create_error = None
        self.created = 0

    def info(self):
        return {}

    def create_task(self, images, options, name=None):
        if self.create_error:
            raise self.create_error
        self.created += 1
        return {"uuid": f"node-task-{self.created}"}

    def task_info(self, uuid):
        return {"status": {"code": self.code, "errorMessage": "not enough overlap"}, "progress": 50}

    def task_cancel(self, uuid):
        return {}


class _Pipeline(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = make_org("Meter Pipeline Org")
        cls.user = make_user("meter_pipeline@example.com", cls.org)

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org)
        self.now = T0
        self.provisioner, self.node = FakeProvisioner(), FakeNode()
        for patcher in (
            patch.object(compute, "now_datetime", side_effect=lambda: self.now),
            patch.object(compute, "ProvisionerClient", side_effect=lambda *a, **k: self.provisioner),
            patch.object(task_runner, "_client_for", side_effect=lambda node: self.node),
            # Collecting outputs is not what is under test: a COMPLETED node means the task completes.
            patch.object(task_runner, "_download_assets",
                         side_effect=lambda client, uuid, task: task_runner._finish(task, "Completed", last_error=None)),
            patch.dict(frappe.conf, {"provisioner_url": "http://provisioner.invalid",
                                     "compute_provision_timeout_seconds": 600,
                                     "compute_max_lifetime_seconds": 4 * 3600}),
            patch.dict("os.environ", {compute.NODE_TOKEN_SECRET_ENV: "s" * 40}),
            patch("frappe.enqueue"),
            patch("frappe.log_error"),
        ):
            patcher.start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        patch.stopall()
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        frappe.db.rollback()
        wipe(self.org)

    # -- driving the pipeline ---------------------------------------------

    def advance(self, **delta):
        self.now = self.now + dt.timedelta(**delta)

    def queued_task(self, title="Pipeline survey"):
        as_user(self.user)
        if not frappe.db.exists("WebODM Project", PROJECT):
            frappe.get_doc({"doctype": "WebODM Project", "title": PROJECT}).insert()
        task = make_task(PROJECT, dataset=make_dataset("Pipeline images", n=2).name, title=title, status="Queued")
        frappe.set_user("Administrator")
        return task.name

    def status(self, task):
        return frappe.db.get_value("WebODM Task", task, "status")

    def instance_of(self, task):
        return frappe.db.get_value("WebODM Task", task, "compute_instance")

    def facts(self, instance):
        return frappe.db.get_value(INSTANCE, instance, ["status", "ready_at", "dispatched_at", "released_at",
                                                        "release_reason", "terminated_at", "destroy_attempts"],
                                   as_dict=True)

    def provision(self, task):
        """Queued -> Provisioning -> machine Ready. Returns the instance name."""
        task_runner.process_task(task)
        self.assertEqual(self.status(task), "Provisioning")
        instance = self.instance_of(task)
        self.advance(minutes=4)                       # booting: not the customer's time
        self.provisioner.state = "ready"
        compute.check_provisioning(task)
        self.assertEqual(self.facts(instance).status, "Ready")
        return instance

    def dispatch(self, task):
        self.advance(seconds=30)
        task_runner.process_task(task)

    def record_of(self, instance):
        sweep.run()
        name = ledger.exists(ledger.compute_key(instance))
        self.assertIsNotNone(name, "the sweep recorded nothing for the instance")
        return ledger.get(name)

    def utc(self, local, edge):
        return timeutil.iso_z(timeutil.local_to_utc(local, sweep.site_time_zone(), edge=edge)[0])


class TestJobEndings(_Pipeline):
    def test_a_job_that_completes(self):
        task = self.queued_task()
        instance = self.provision(task)
        ready = self.now
        self.dispatch(task)
        self.assertEqual(self.status(task), "Running")
        self.assertEqual(self.facts(instance).dispatched_at, ready + dt.timedelta(seconds=30))

        self.advance(hours=1)                         # processing
        task_runner.poll_task(task)                   # still running
        self.assertIsNone(self.facts(instance).released_at)
        self.node.code = task_runner.NODE_STATUS_COMPLETED
        task_runner.poll_task(task)

        self.assertEqual(self.status(task), "Completed")
        facts = self.facts(instance)
        self.assertEqual((facts.status, facts.release_reason), ("Terminated", "task:Completed"))
        self.assertEqual(facts.released_at, ready + dt.timedelta(seconds=3630))
        self.assertEqual(self.provisioner.destroyed, ["fake:1"])

        rec = self.record_of(instance)
        self.assertEqual((rec["quantity"], rec["waived_quantity"], rec["unit"]), (3630, 0, "second"))
        self.assertEqual(rec["dimension"], "fake/cpu")
        self.assertEqual(rec["organization"], self.org)
        self.assertEqual((rec["task"], rec["project"]), (task, PROJECT))
        self.assertEqual(rec["period_start_utc"], self.utc(ready, "start"))
        self.assertEqual(rec["period_end_utc"], self.utc(ready + dt.timedelta(seconds=3630), "end"))
        self.assertEqual(rec["caveats"], [])
        # The four minutes of boot are nowhere in what counts.
        self.assertEqual(sum(r["quantity"] for r in usage_rows(self.org)), 3630)

    def test_a_job_that_never_reaches_the_node_costs_the_customer_nothing(self):
        task = self.queued_task()
        instance = self.provision(task)
        self.node.create_error = NodeODMTransportError("upload to node failed")
        for _ in range(task_runner.MAX_DISPATCH_ATTEMPTS):
            self.advance(minutes=2)
            task_runner.process_task(task)
        self.assertEqual(self.status(task), "Failed")
        facts = self.facts(instance)
        self.assertEqual((facts.status, facts.release_reason, facts.dispatched_at), ("Terminated", "task:Failed", None))

        rec = self.record_of(instance)
        self.assertEqual((rec["quantity"], rec["waived_reason"]), (0, "no_work_dispatched"))
        self.assertEqual(rec["waived_quantity"], task_runner.MAX_DISPATCH_ATTEMPTS * 120)

    def test_a_machine_that_never_comes_up(self):
        task = self.queued_task()
        task_runner.process_task(task)
        instance = self.instance_of(task)
        self.advance(minutes=11)                      # past the 10-minute provisioning timeout
        compute.check_provisioning(task)
        self.assertEqual(self.status(task), "Queued")  # requeued for another try
        facts = self.facts(instance)
        self.assertEqual((facts.status, facts.release_reason, facts.ready_at), ("Failed", "provisioning-timeout", None))

        rec = self.record_of(instance)
        self.assertEqual((rec["quantity"], rec["waived_reason"]), (0, "never_ready"))
        self.assertEqual(rec["waived_quantity"], 11 * 60)

    def test_a_job_cancelled_part_way(self):
        task = self.queued_task()
        instance = self.provision(task)
        self.dispatch(task)
        self.advance(minutes=20)
        self.node.code = task_runner.NODE_STATUS_CANCELED
        task_runner.poll_task(task)
        self.assertEqual(self.status(task), "Cancelled")
        self.assertEqual(self.facts(instance).release_reason, "task:Cancelled")
        rec = self.record_of(instance)
        self.assertEqual((rec["quantity"], rec["waived_quantity"]), (20 * 60 + 30, 0))

    def test_a_job_the_engine_fails_after_work_started(self):
        # Counts, by the letter of the brief; the reason is kept so that a
        # different policy can be applied later without touching the record.
        task = self.queued_task()
        instance = self.provision(task)
        self.dispatch(task)
        self.advance(minutes=45)
        self.node.code = task_runner.NODE_STATUS_FAILED
        task_runner.poll_task(task)
        self.assertEqual(self.status(task), "Failed")
        rec = self.record_of(instance)
        self.assertEqual(rec["quantity"], 45 * 60 + 30)
        self.assertEqual(rec["evidence"]["inputs"]["release_reason"], "task:Failed")

    def test_a_machine_killed_by_the_lifetime_budget(self):
        task = self.queued_task()
        instance = self.provision(task)
        self.dispatch(task)
        self.advance(hours=5)                         # past the 4-hour budget
        compute.reap()
        self.assertEqual(self.status(task), "Failed")
        self.assertEqual(self.facts(instance).release_reason, "lifetime-exceeded")
        rec = self.record_of(instance)
        self.assertEqual((rec["quantity"], rec["waived_reason"]), (0, "platform_failure"))
        self.assertGreater(rec["waived_quantity"], 5 * 3600)

    def test_a_destroy_that_fails_does_not_extend_what_counts(self):
        task = self.queued_task()
        instance = self.provision(task)
        ready = self.now
        self.dispatch(task)
        self.advance(hours=1)
        self.provisioner.destroy_error = compute.ProvisionerUnavailable("provisioner down")
        self.node.code = task_runner.NODE_STATUS_COMPLETED
        task_runner.poll_task(task)
        self.assertEqual(self.status(task), "Completed")           # the job is done regardless
        self.assertEqual(self.facts(instance).status, "Terminating")

        early = self.record_of(instance)                            # recordable at once: the interval is closed
        self.assertEqual(early["quantity"], 3630)

        self.advance(hours=3)                                       # three hours of failed retries
        compute.reap()
        self.assertEqual(self.facts(instance).status, "Terminating")
        self.provisioner.destroy_error = None
        compute.reap()
        facts = self.facts(instance)
        self.assertEqual(facts.status, "Terminated")
        self.assertEqual(facts.released_at, ready + dt.timedelta(seconds=3630))
        self.assertGreaterEqual(facts.destroy_attempts, 2)

        sweep.run()
        (rec,) = usage_rows(self.org)
        self.assertEqual(rec["quantity"], 3630)                     # not 3630 + three hours

    def test_a_task_deleted_while_running(self):
        task = self.queued_task()
        instance = self.provision(task)
        self.dispatch(task)
        self.advance(minutes=10)
        frappe.delete_doc("WebODM Task", task, force=True, ignore_permissions=True)
        facts = self.facts(instance)                                 # the instance outlives its task
        self.assertEqual((facts.status, facts.release_reason), ("Terminated", "task-deleted"))
        rec = self.record_of(instance)
        self.assertEqual(rec["quantity"], 10 * 60 + 30)
        self.assertEqual(rec["task"], task)

    def test_processing_again_is_a_second_machine_and_a_second_record(self):
        from webodm_core.api import task as task_api

        task = self.queued_task()
        first = self.provision(task)
        self.dispatch(task)
        self.advance(minutes=30)
        self.node.code = task_runner.NODE_STATUS_COMPLETED
        task_runner.poll_task(task)
        sweep.run()

        # Restart, as api.task.process_task does.
        self.advance(days=1)
        self.provisioner.state, self.node.code = "pending", task_runner.NODE_STATUS_RUNNING
        frappe.db.set_value("WebODM Task", task, {"status": "Queued", "node_task_id": None, "compute_instance": None,
                                                  "dispatch_attempts": 0})
        second = self.provision(task)
        self.assertNotEqual(first, second)
        self.dispatch(task)
        self.advance(minutes=50)
        self.node.code = task_runner.NODE_STATUS_COMPLETED
        task_runner.poll_task(task)
        for _ in range(3):
            sweep.run()

        rows = {r["source_name"]: r["quantity"] for r in usage_rows(self.org)}
        self.assertEqual(rows, {first: 30 * 60 + 30, second: 50 * 60 + 30})

    def test_in_progress_follows_the_machine(self):
        task = self.queued_task()
        instance = self.provision(task)
        self.dispatch(task)
        self.advance(minutes=30)
        now_utc = timeutil.local_to_utc(self.now, sweep.site_time_zone(), edge="start")[0]
        with patch.object(timeutil, "utc_now", return_value=now_utc):
            (item,) = report.in_progress(self.org)
        self.assertEqual((item["source_name"], item["state"], item["work_dispatched"], item["provisional"]),
                         (instance, "Ready", True, True))
        self.assertEqual(item["elapsed_so_far"], 30 * 60 + 30)
        self.assertEqual(sweep.outstanding_instances(self.org), [])
        self.assertFalse(usage_rows(self.org))


class TestStaticNode(_Pipeline):
    def test_processing_on_a_static_node_leaves_nothing_to_meter(self):
        # design.md F2: no rented machine, no record of one. Stated, not hidden.
        task = self.queued_task()
        with patch.object(compute, "enabled", return_value=False), \
                patch.object(task_runner, "_first_node", return_value={"name": "static", "hostname": "h", "port": 3000}):
            task_runner.process_task(task)
            self.assertEqual(self.status(task), "Running")
            self.node.code = task_runner.NODE_STATUS_COMPLETED
            task_runner.poll_task(task)
        self.assertEqual(self.status(task), "Completed")
        self.assertIsNone(self.instance_of(task))
        self.assertEqual(frappe.db.count(INSTANCE, {"organization": self.org}), 0)
        self.assertEqual(sweep.run()["instances"], 0)
        self.assertFalse(usage_rows(self.org, meter="compute"))
