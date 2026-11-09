"""Catch-up recording: closed machines and finished runs become usage records.

Against real rows on a real database. Pinned here:

* each source is recorded once, however often the sweep runs and whichever
  route (sweep or deletion hook) gets to it first;
* what counts and what is waived, end to end from the row the pipeline
  leaves to the record;
* work that has not finished is never recorded;
* the platform's cost estimates never reach a record;
* one source that cannot be recorded does not stop the rest;
* the pipeline's lifecycle stamps are written once and survive retries.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.api import plugins as plugins_api
from webodm_core.metering import ledger, rules, sweep
from webodm_core.metering.testing import (
    INSTANCE,
    RUN,
    as_user,
    at,
    local,
    local_to_utc,
    make_instance,
    make_org,
    make_run,
    make_user,
    usage_rows,
    wipe,
)
from webodm_core.metering.timeutil import iso_z
from webodm_core.testing import make_dataset, make_task
from webodm_core.webodm_core.processing import compute

PLUGIN_ID = "test.metering-plugin"


def _record_for(source_name):
    name = frappe.db.get_value(ledger.DOCTYPE, {"source_name": source_name, "entry_type": "usage"}, "name")
    return ledger.get(name) if name else None


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = make_org("Meter Sweep Org")
        cls.other_org = make_org("Meter Sweep Other")
        cls.user = make_user("meter_sweep@example.com", cls.org)
        cls.other_user = make_user("meter_sweep_other@example.com", cls.other_org)
        if not frappe.db.exists("WebODM Plugin", PLUGIN_ID):
            frappe.get_doc({"doctype": "WebODM Plugin", "plugin_id": PLUGIN_ID, "label": "Metering Test",
                            "version": "2.3.1", "plugin_type": "System", "output_kind": "vector",
                            "platform_enabled": 1, "available": 1}).insert(ignore_permissions=True)

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org, self.other_org)

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org, self.other_org)

    def real_task(self, title="Sweep survey", **fields):
        as_user(self.user)
        if not frappe.db.exists("WebODM Project", "Meter Sweep Project"):
            frappe.get_doc({"doctype": "WebODM Project", "title": "Meter Sweep Project"}).insert()
        task = make_task("Meter Sweep Project", dataset=make_dataset("Sweep images").name, title=title)
        frappe.set_user("Administrator")
        if fields:
            frappe.db.set_value("WebODM Task", task.name, fields, update_modified=False)
        return task


class TestMachineTime(_Base):
    def test_a_useful_machine_becomes_one_record(self):
        task = self.real_task()
        inst = make_instance(self.org, task=task.name)
        with at("2026-10-19T12:00:00Z"):
            stats = sweep.run()
        self.assertEqual((stats["instances"], stats["errors"]), (1, 0))
        rec = _record_for(inst)
        self.assertEqual(rec["organization"], self.org)
        self.assertEqual((rec["meter"], rec["unit"], rec["dimension"]), ("compute", "second", "aws/cpu"))
        self.assertEqual((rec["quantity"], rec["waived_quantity"], rec["waived_reason"]), (3600, 0, None))
        self.assertEqual(rec["period_start_utc"], iso_z(local_to_utc("2026-10-19 10:00:00")))
        self.assertEqual(rec["period_end_utc"], iso_z(local_to_utc("2026-10-19 11:00:00", "end")))
        self.assertEqual(rec["recorded_at_utc"], "2026-10-19T12:00:00Z")
        self.assertEqual((rec["source_doctype"], rec["source_name"]), (INSTANCE, inst))
        self.assertEqual((rec["task"], rec["project"]), (task.name, "Meter Sweep Project"))
        self.assertEqual(rec["idempotency_key"], f"compute:{inst}")
        self.assertEqual(rec["evidence"]["inputs"]["release_reason"], "task:Completed")
        self.assertEqual(rec["evidence"]["inputs"]["work_evidence"], "dispatched_at")

    def test_running_the_sweep_again_changes_nothing(self):
        inst = make_instance(self.org)
        sweep.run()
        before = usage_rows(self.org)
        for _ in range(3):
            stats = sweep.run()
            self.assertEqual((stats["instances"], stats["already_recorded"], stats["errors"]), (0, 0, 0))
        after = usage_rows(self.org)
        self.assertEqual(len(after), 1)
        self.assertEqual(before, after)
        self.assertEqual(sweep.record_instance(inst), (after[0]["name"], False))

    def test_what_the_platform_absorbs(self):
        cases = {
            "never_ready": make_instance(
                self.org, status="Failed", ready_at=None, dispatched_at=None,
                released_at=local("2026-10-19 10:10:00"), release_reason="provisioning-timeout"),
            "no_work_dispatched": make_instance(self.org, dispatched_at=None, release_reason="task:Failed"),
            "platform_failure": make_instance(self.org, release_reason="lifetime-exceeded"),
            "end_time_not_recorded": make_instance(
                self.org, released_at=None, release_reason=None, destroy_attempts=3,
                terminated_at=local("2026-10-19 14:00:00")),
        }
        sweep.run()
        for reason, inst in cases.items():
            rec = _record_for(inst)
            self.assertEqual(rec["quantity"], 0, reason)
            self.assertEqual(rec["waived_reason"], reason)
            self.assertGreater(rec["waived_quantity"], 0, reason)
        # Summing the column a rating layer will sum picks up none of it.
        self.assertEqual(sum(r["quantity"] for r in usage_rows(self.org)), 0)

    def test_time_spent_failing_to_destroy_is_not_the_customers(self):
        inst = make_instance(self.org, status="Terminating", destroy_attempts=9, terminated_at=None)
        sweep.run()
        rec = _record_for(inst)
        self.assertEqual(rec["quantity"], 3600)
        self.assertEqual(rec["period_end_utc"], iso_z(local_to_utc("2026-10-19 11:00:00", "end")))
        # ...and when the destroy is finally confirmed hours later, nothing is re-recorded.
        frappe.db.set_value(INSTANCE, inst, {"status": "Terminated", "terminated_at": local("2026-10-19 18:00:00")})
        sweep.run()
        self.assertEqual(len(usage_rows(self.org)), 1)
        self.assertEqual(_record_for(inst)["quantity"], 3600)

    def test_a_machine_that_is_still_up_is_not_recorded(self):
        for status in ("Requested", "Provisioning", "Ready"):
            make_instance(self.org, status=status, released_at=None, terminated_at=None, release_reason=None)
        self.assertEqual(sweep.outstanding_instances(self.org), [])
        sweep.run()
        self.assertFalse(usage_rows(self.org))
        ready = frappe.get_all(INSTANCE, filters={"organization": self.org, "status": "Ready"}, pluck="name")[0]
        with self.assertRaises(ValueError):
            sweep.record_instance(ready)

    def test_the_cost_estimates_never_reach_a_record(self):
        inst = make_instance(self.org, estimated_hourly_cost=987.654, estimated_cost=4321.5)
        sweep.run()
        row = frappe.db.get_value(ledger.DOCTYPE, {"source_name": inst}, "*", as_dict=True)
        text = " ".join(str(value) for value in row.values())
        for needle in ("987", "4321", "estimated", "cost"):
            self.assertNotIn(needle, text)
        self.assertNotIn("estimated_hourly_cost", sweep.INSTANCE_FIELDS)
        self.assertNotIn("estimated_cost", sweep.INSTANCE_FIELDS)
        columns = {f.fieldname for f in frappe.get_meta(ledger.DOCTYPE).fields}
        self.assertFalse({c for c in columns if any(w in c for w in ("cost", "price", "amount", "currency", "rate"))})
        self.assertFalse([f.fieldname for f in frappe.get_meta(ledger.DOCTYPE).fields
                          if f.fieldtype in ("Float", "Currency", "Percent")])

    def test_history_is_recorded_by_the_same_sweep(self):
        # Closed weeks before metering existed: no dispatched_at / released_at.
        task = self.real_task(node_task_id="node-uuid-1")
        old = make_instance(self.org, task=task.name, dispatched_at=None, released_at=None, release_reason=None,
                            requested_at=local("2026-09-26 08:00:00"), ready_at=local("2026-09-26 08:05:00"),
                            terminated_at=local("2026-09-26 09:05:00"))
        frappe.db.set_value("WebODM Task", task.name, "compute_instance", old, update_modified=False)
        with at("2026-10-19T12:00:00Z"):
            sweep.run()
        rec = _record_for(old)
        self.assertEqual(rec["quantity"], 3600)
        self.assertEqual(rec["period_end_utc"], iso_z(local_to_utc("2026-09-26 09:05:00", "end")))
        self.assertEqual(rec["recorded_at_utc"], "2026-10-19T12:00:00Z")  # late, and visibly so
        self.assertEqual(rec["evidence"]["inputs"]["work_evidence"], "task_link")
        self.assertEqual(len(rec["caveats"]), 2)  # both fallbacks are stated on the record

    def test_history_without_proof_of_work_is_waived_not_assumed(self):
        # The task has moved on (restarted): nothing says this machine worked.
        task = self.real_task(node_task_id=None)
        old = make_instance(self.org, task=task.name, dispatched_at=None, released_at=None, release_reason=None)
        sweep.run()
        rec = _record_for(old)
        self.assertEqual((rec["quantity"], rec["waived_reason"]), (0, "no_work_dispatched"))

    def test_one_source_that_fails_does_not_stop_the_others(self):
        bad = make_instance(self.org)
        good = make_instance(self.org)
        real = rules.compute_usage

        def compute_usage(inputs):
            if inputs["task"] == "poison":
                raise RuntimeError("cannot make sense of this row")
            return real(inputs)

        frappe.db.set_value(INSTANCE, bad, "task", "poison", update_modified=False)
        with patch.object(rules, "compute_usage", side_effect=compute_usage), \
                patch.object(frappe.cache, "get_value", return_value=None), patch("frappe.log_error") as log:
            stats = sweep.run()
        self.assertEqual((stats["instances"], stats["errors"]), (1, 1))
        self.assertIsNone(_record_for(bad))
        self.assertIsNotNone(_record_for(good))
        self.assertIn(bad, log.call_args.args[0])
        # Still outstanding: the next run picks it up once it can be recorded.
        self.assertEqual(sweep.outstanding_instances(self.org), [bad])
        self.assertEqual(sweep.run()["instances"], 1)
        self.assertIsNotNone(_record_for(bad))

    def test_a_database_error_on_one_source_leaves_the_sweep_usable(self):
        bad = make_instance(self.org)
        good = make_instance(self.org)
        frappe.db.set_value(INSTANCE, bad, "task", "poison", update_modified=False)
        real = sweep._project_of

        def project_of(task, organization):
            if task == "poison":
                frappe.db.sql("select * from tab_no_such_table_for_metering")
            return real(task, organization)

        with patch.object(sweep, "_project_of", side_effect=project_of), patch("frappe.log_error"):
            stats = sweep.run()
        self.assertEqual((stats["instances"], stats["errors"]), (1, 1))
        self.assertIsNotNone(_record_for(good))

    def test_a_backlog_is_worked_off_in_batches(self):
        for _ in range(5):
            make_instance(self.org)
        first = sweep.run(max_records=2)
        self.assertEqual((first["instances"], first["remaining"]), (2, 3))
        second = sweep.run(max_records=2)
        self.assertEqual((second["instances"], second["remaining"]), (2, 1))
        third = sweep.run(max_records=2)
        self.assertEqual((third["instances"], third["remaining"]), (1, 0))
        self.assertEqual(len(usage_rows(self.org)), 5)

    def test_a_run_stops_when_its_time_is_up(self):
        # Scheduled jobs are killed after 300 s; the sweep stops itself well
        # before that and leaves the rest for the next run.
        for _ in range(4):
            make_instance(self.org)
        first = sweep.run(max_seconds=0)
        self.assertEqual((first["instances"], first["remaining"]), (0, 4))
        self.assertEqual(sweep.run()["instances"], 4)
        self.assertLess(sweep.MAX_SECONDS_PER_RUN, 300)

    def test_the_sweep_does_not_touch_its_sources(self):
        inst = make_instance(self.org)
        run = make_run(self.user)
        before = (frappe.db.get_value(INSTANCE, inst, "*", as_dict=True),
                  frappe.db.get_value(RUN, run.name, "*", as_dict=True))
        sweep.run()
        after = (frappe.db.get_value(INSTANCE, inst, "*", as_dict=True),
                 frappe.db.get_value(RUN, run.name, "*", as_dict=True))
        self.assertEqual(before, after)

    def test_records_carry_the_organization_of_their_source(self):
        mine = make_instance(self.org)
        theirs = make_instance(self.other_org)
        sweep.run()
        self.assertEqual(_record_for(mine)["organization"], self.org)
        self.assertEqual(_record_for(theirs)["organization"], self.other_org)
        self.assertEqual({r["source_name"] for r in usage_rows(self.org)}, {mine})
        self.assertEqual({r["source_name"] for r in usage_rows(self.other_org)}, {theirs})

    def test_the_scheduler_entry_point_never_raises(self):
        with patch.object(sweep, "run", side_effect=RuntimeError("no database")), patch("frappe.log_error") as log:
            sweep.run_job()
        self.assertIn("no database", log.call_args.args[0])


class TestAnalysisRuns(_Base):
    def test_a_completed_run_counts_one(self):
        task = self.real_task()
        run = make_run(self.user, plugin=PLUGIN_ID, task=task.name)
        with at("2026-10-19T12:00:00Z"):
            sweep.run()
        rec = _record_for(run.name)
        self.assertEqual((rec["meter"], rec["unit"], rec["dimension"]), ("analysis_run", "run", PLUGIN_ID))
        self.assertEqual((rec["quantity"], rec["waived_quantity"]), (1, 0))
        self.assertEqual(rec["organization"], self.org)
        self.assertEqual((rec["task"], rec["project"]), (task.name, "Meter Sweep Project"))
        self.assertEqual(rec["period_end_utc"], iso_z(local_to_utc("2026-10-19 10:05:02", "end")))
        inputs = rec["evidence"]["inputs"]
        self.assertEqual((inputs["plugin_type"], inputs["plugin_version"]), ("System", "2.3.1"))
        self.assertEqual(inputs["captured"], "sweep")

    def test_a_run_that_produced_nothing_is_waived(self):
        failed = make_run(self.user, status="Failed")
        cancelled = make_run(self.user, status="Cancelled", started_at=None)
        sweep.run()
        self.assertEqual((_record_for(failed.name)["quantity"], _record_for(failed.name)["waived_reason"]), (0, "failed"))
        rec = _record_for(cancelled.name)
        self.assertEqual((rec["quantity"], rec["waived_quantity"], rec["waived_reason"]), (0, 1, "cancelled"))

    def test_a_run_that_is_not_finished_is_not_recorded(self):
        queued = make_run(self.user, status="Queued", started_at=None, completed_at=None)
        running = make_run(self.user, status="Running", completed_at=None)
        self.assertEqual(sweep.outstanding_runs(self.org), [])
        sweep.run()
        self.assertFalse(usage_rows(self.org))
        for run in (queued, running):
            with self.assertRaises(ValueError):
                sweep.record_run(run.name)

    def test_a_run_deleted_before_the_sweep_saw_it_is_still_recorded(self):
        run = make_run(self.user, plugin=PLUGIN_ID)
        frappe.delete_doc(RUN, run.name, force=True, ignore_permissions=True)
        self.assertFalse(frappe.db.exists(RUN, run.name))
        rec = _record_for(run.name)
        self.assertEqual(rec["quantity"], 1)
        self.assertEqual(rec["evidence"]["inputs"]["captured"], "before_delete")
        # The record explains itself without its source.
        self.assertEqual(rules.rederive(rec["evidence"])["quantity"], 1)

    def test_sweep_then_delete_records_once(self):
        run = make_run(self.user)
        sweep.run()
        frappe.delete_doc(RUN, run.name, force=True, ignore_permissions=True)
        sweep.run()
        self.assertEqual(len(usage_rows(self.org)), 1)

    def test_rerunning_a_plugin_does_not_lose_the_run_it_replaces(self):
        # api.plugins.run_plugin deletes the previous run of the same plugin
        # on the same task before queueing the new one.
        task = self.real_task()
        first = make_run(self.user, plugin=PLUGIN_ID, task=task.name)
        as_user(self.user)
        plugins_api._delete_previous_runs(PLUGIN_ID, task.name)
        frappe.set_user("Administrator")
        second = make_run(self.user, plugin=PLUGIN_ID, task=task.name)
        sweep.run()
        self.assertEqual(_record_for(first.name)["quantity"], 1)
        self.assertEqual(_record_for(second.name)["quantity"], 1)
        self.assertEqual(sum(r["quantity"] for r in usage_rows(self.org, meter="analysis_run")), 2)

    def test_deleting_a_task_records_its_runs(self):
        task = self.real_task()
        done = make_run(self.user, plugin=PLUGIN_ID, task=task.name)
        failed = make_run(self.user, plugin=PLUGIN_ID, task=task.name, status="Failed")
        frappe.delete_doc("WebODM Task", task.name, force=True, ignore_permissions=True)
        self.assertFalse(frappe.db.exists(RUN, done.name))
        self.assertEqual(_record_for(done.name)["quantity"], 1)
        self.assertEqual(_record_for(failed.name)["waived_reason"], "failed")

    def test_a_run_deleted_while_running_is_recorded_as_producing_nothing(self):
        run = make_run(self.user, status="Running", completed_at=None)
        with at("2026-10-19T12:00:00Z"):
            frappe.delete_doc(RUN, run.name, force=True, ignore_permissions=True)
        rec = _record_for(run.name)
        self.assertEqual((rec["quantity"], rec["waived_quantity"], rec["waived_reason"]),
                         (0, 1, "deleted_before_finishing"))
        self.assertEqual(rec["period_end_utc"], "2026-10-19T12:00:00Z")

    def test_every_run_ends_in_exactly_one_record(self):
        runs = [make_run(self.user), make_run(self.user, status="Failed"), make_run(self.user, status="Cancelled"),
                make_run(self.user, status="Running", completed_at=None)]
        sweep.run()
        for run in runs:
            frappe.delete_doc(RUN, run.name, force=True, ignore_permissions=True)
        sweep.run()
        rows = usage_rows(self.org, meter="analysis_run")
        self.assertEqual(sorted(r["source_name"] for r in rows), sorted(r.name for r in runs))
        self.assertEqual(len({r["idempotency_key"] for r in rows}), 4)
        self.assertEqual(sum(r["quantity"] for r in rows), 1)
        self.assertEqual(sum(r["waived_quantity"] for r in rows), 3)

    def test_a_run_pointed_at_another_organizations_task_copies_nothing_of_theirs(self):
        # `task` and `plugin` on a run are member-writable links.
        as_user(self.other_user)
        if not frappe.db.exists("WebODM Project", "Other Org Secret Project"):
            frappe.get_doc({"doctype": "WebODM Project", "title": "Other Org Secret Project"}).insert()
        their_task = make_task("Other Org Secret Project", dataset=make_dataset("Their images").name, title="Theirs")
        frappe.set_user("Administrator")
        if not frappe.db.exists("WebODM Plugin", "meter-other.private-plugin"):
            frappe.get_doc({"doctype": "WebODM Plugin", "plugin_id": "meter-other.private-plugin",
                            "label": "Private", "version": "9.9.9-secret", "plugin_type": "User",
                            "organization": self.other_org, "output_kind": "vector",
                            "platform_enabled": 1, "available": 1}).insert(ignore_permissions=True)
        run = make_run(self.user, plugin="meter-other.private-plugin", task=their_task.name)
        try:
            sweep.run()
            rec = _record_for(run.name)
            self.assertEqual(rec["organization"], self.org)
            self.assertIsNone(rec["project"])
            text = frappe.db.get_value(ledger.DOCTYPE, rec["name"], "evidence") + frappe.as_json(rec)
            for theirs in ("Other Org Secret Project", "9.9.9-secret", self.other_org):
                self.assertNotIn(theirs, text)
            self.assertIsNone(rec["evidence"]["inputs"]["plugin_version"])
        finally:
            frappe.delete_doc("WebODM Plugin", "meter-other.private-plugin", force=True, ignore_permissions=True)

    def test_a_removed_plugin_does_not_stop_its_runs_being_recorded(self):
        run = make_run(self.user, plugin="plugin.that.was.removed")
        sweep.run()
        rec = _record_for(run.name)
        self.assertEqual((rec["quantity"], rec["dimension"]), (1, "plugin.that.was.removed"))
        self.assertIsNone(rec["evidence"]["inputs"]["plugin_type"])


class TestInstanceDeletion(_Base):
    def test_a_closed_instance_deleted_before_the_sweep_is_recorded(self):
        inst = make_instance(self.org)
        frappe.delete_doc(INSTANCE, inst, force=True, ignore_permissions=True)
        rec = _record_for(inst)
        self.assertEqual(rec["quantity"], 3600)

    def test_a_live_instance_deleted_by_hand_is_waived(self):
        inst = make_instance(self.org, status="Ready", released_at=None, terminated_at=None, release_reason=None)
        with at(iso_z(local_to_utc("2026-10-19 10:30:00"))):
            frappe.delete_doc(INSTANCE, inst, force=True, ignore_permissions=True)
        rec = _record_for(inst)
        self.assertEqual((rec["quantity"], rec["waived_reason"]), (0, "deleted_before_release"))
        self.assertEqual(rec["waived_quantity"], 1800)


class TestLifecycleStamps(_Base):
    """The three facts the pipeline writes on the instance for metering."""

    def live(self, **fields):
        base = dict(status="Ready", dispatched_at=None, released_at=None, terminated_at=None, release_reason=None)
        base.update(fields)
        return make_instance(self.org, **base)

    def facts(self, name):
        return frappe.db.get_value(INSTANCE, name, ["status", "dispatched_at", "released_at", "release_reason",
                                                    "terminated_at", "destroy_attempts"], as_dict=True)

    def test_stamp_is_written_once(self):
        inst = self.live()
        compute.stamp_once(inst, "dispatched_at", local("2026-10-19 10:00:40"))
        compute.stamp_once(inst, "dispatched_at", local("2026-10-19 11:11:11"))
        self.assertEqual(self.facts(inst).dispatched_at, local("2026-10-19 10:00:40"))
        compute.stamp_once(inst, "release_reason", "task:Completed")
        compute.stamp_once(inst, "release_reason", "lifetime-exceeded")
        self.assertEqual(self.facts(inst).release_reason, "task:Completed")

    def test_only_the_three_facts_can_be_stamped(self):
        inst = self.live()
        compute.stamp_once(inst, "status", "Terminated")
        compute.stamp_once(inst, "estimated_cost", 0)
        self.assertEqual(self.facts(inst).status, "Ready")

    def test_mark_dispatched(self):
        inst = self.live()
        compute.mark_dispatched({"name": inst, "hostname": "h", "port": 3000, "compute_instance": inst})
        first = self.facts(inst).dispatched_at
        self.assertIsNotNone(first)
        compute.mark_dispatched({"compute_instance": inst})
        self.assertEqual(self.facts(inst).dispatched_at, first)
        # A static node has no instance: nothing to stamp, nothing raised.
        compute.mark_dispatched({"name": "static", "hostname": "h", "port": 3000})
        compute.mark_dispatched(None)

    def test_release_time_is_the_first_request_and_survives_retries(self):
        inst = self.live(dispatched_at=local("2026-10-19 10:00:40"))
        client = MagicMock()
        client.destroy.side_effect = compute.ProvisionerUnavailable("down")
        with patch.object(compute, "ProvisionerClient", return_value=client), patch("frappe.log_error"):
            self.assertFalse(compute.destroy_instance(frappe.get_doc(INSTANCE, inst), "task:Completed"))
        first = self.facts(inst)
        self.assertEqual((first.status, first.release_reason, first.destroy_attempts),
                         ("Terminating", "task:Completed", 1))
        self.assertIsNotNone(first.released_at)
        self.assertIsNone(first.terminated_at)

        # The reaper retries later, without a reason, and this time it works.
        frappe.db.set_value(INSTANCE, inst, "released_at", local("2026-10-01 11:00:00"), update_modified=False)
        client.destroy.side_effect = None
        client.destroy.return_value = {}
        with patch.object(compute, "ProvisionerClient", return_value=client):
            self.assertTrue(compute.destroy_instance(frappe.get_doc(INSTANCE, inst)))
        final = self.facts(inst)
        self.assertEqual(final.status, "Terminated")
        self.assertEqual(final.released_at, local("2026-10-01 11:00:00"))  # not moved by the retry
        self.assertEqual(final.release_reason, "task:Completed")
        self.assertGreater(final.terminated_at, final.released_at)

    def test_a_retry_never_writes_a_release_time(self):
        # A record that was already waiting for a destroy retry when these
        # fields were introduced (or whose first stamp was lost): the retry is
        # later than the moment we stopped needing the machine, so it must not
        # become that moment.
        stuck = self.live(status="Terminating", destroy_attempts=2, dispatched_at=local("2026-10-01 10:01:00"),
                          ready_at=local("2026-10-01 10:00:00"))
        self.assertEqual(sweep.outstanding_instances(self.org), [])      # no end yet: not recordable
        client = MagicMock()
        client.destroy.return_value = {}
        with patch.object(compute, "ProvisionerClient", return_value=client):
            self.assertTrue(compute.destroy_instance(frappe.get_doc(INSTANCE, stuck), "task:Completed"))
        facts = self.facts(stuck)
        self.assertEqual(facts.status, "Terminated")
        self.assertIsNone(facts.released_at)
        self.assertIsNone(facts.release_reason)
        self.assertIsNotNone(facts.terminated_at)

        sweep.run()
        rec = _record_for(stuck)
        # Weeks of "ready -> confirmed destroy" are measured and waived, not counted.
        self.assertEqual((rec["quantity"], rec["waived_reason"]), (0, "end_time_not_recorded"))
        self.assertGreater(rec["waived_quantity"], 0)

    def test_a_lost_first_stamp_is_not_made_up_for_by_the_retry(self):
        inst = self.live(dispatched_at=local("2026-10-01 10:01:00"), ready_at=local("2026-10-01 10:00:00"))
        client = MagicMock()
        client.destroy.side_effect = compute.ProvisionerUnavailable("down")
        with patch.object(compute, "ProvisionerClient", return_value=client), patch("frappe.log_error"), \
                patch.object(compute, "stamp_once"):                    # the first attempt's stamps are lost
            compute.destroy_instance(frappe.get_doc(INSTANCE, inst), "task:Completed")
        self.assertEqual((self.facts(inst).status, self.facts(inst).released_at), ("Terminating", None))
        client.destroy.side_effect = None
        client.destroy.return_value = {}
        with patch.object(compute, "ProvisionerClient", return_value=client):
            compute.destroy_instance(frappe.get_doc(INSTANCE, inst))
        self.assertIsNone(self.facts(inst).released_at)
        sweep.run()
        self.assertEqual(_record_for(inst)["waived_reason"], "end_time_not_recorded")

    def test_release_for_task_names_the_tasks_outcome(self):
        for status in ("Completed", "Failed", "Cancelled"):
            inst = self.live(handle=None)  # no handle: closes without a provisioner
            task = frappe._dict(name="T", status=status, compute_instance=inst)
            compute.release_for_task(task)
            self.assertEqual(self.facts(inst).release_reason, f"task:{status}")
            self.assertIsNotNone(self.facts(inst).released_at)
        inst = self.live(handle=None)
        compute.release_for_task(frappe._dict(name="T", status="Running", compute_instance=inst), reason="task-deleted")
        self.assertEqual(self.facts(inst).release_reason, "task-deleted")

    def test_pipeline_to_record(self):
        # Ready -> dispatched -> released by the pipeline's own functions, then swept.
        inst = self.live(handle=None, ready_at=local("2026-10-19 10:00:00"))
        compute.mark_dispatched({"compute_instance": inst})
        compute.release_for_task(frappe._dict(name="T", status="Completed", compute_instance=inst))
        frappe.db.set_value(INSTANCE, inst, "released_at", local("2026-10-19 10:20:00"), update_modified=False)
        sweep.run()
        rec = _record_for(inst)
        self.assertEqual((rec["quantity"], rec["waived_reason"]), (1200, None))
        self.assertEqual(rec["evidence"]["inputs"]["release_reason"], "task:Completed")
