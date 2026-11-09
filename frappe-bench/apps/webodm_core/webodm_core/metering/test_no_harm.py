"""Recording must never damage the thing it describes.

Metering touches other code paths in exactly three places: one stamp when a
job is dispatched, two when a machine is released, and a hook when a plugin
run or compute instance is deleted. Each test here breaks metering at one of
those places — with a Python exception, with a failing *database statement*
(which on PostgreSQL poisons the surrounding transaction unless it is
isolated), or by making the package unimportable — and asserts that the
dispatch, the teardown or the delete ends exactly as it would have.
"""

from __future__ import annotations

import inspect
import re
import sys
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.metering import ledger, sweep
from webodm_core.metering.testing import (
    INSTANCE,
    RUN,
    as_user,
    make_instance,
    make_org,
    make_run,
    make_user,
    usage_rows,
    wipe,
)
from webodm_core.plugins import runner
from webodm_core.testing import make_dataset, make_task
from webodm_core.webodm_core.processing import compute, task_runner


def _broken_statement(*args, **kwargs):
    """Fail the way a real bug would: inside the database, aborting the statement."""
    frappe.db.sql("select * from tab_no_such_table_for_metering")


class _Base(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.org = make_org("Meter Harm Org")
        cls.user = make_user("meter_harm@example.com", cls.org)

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org)
        patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        patch.stopall()
        frappe.set_user("Administrator")
        frappe.local.webodm_org_cache = {}
        wipe(self.org)

    def assertTransactionUsable(self):
        # After an unisolated failed statement PostgreSQL answers every query
        # with "current transaction is aborted".
        self.assertEqual(frappe.db.sql("select 1")[0][0], 1)

    def real_task(self):
        as_user(self.user)
        if not frappe.db.exists("WebODM Project", "Meter Harm Project"):
            frappe.get_doc({"doctype": "WebODM Project", "title": "Meter Harm Project"}).insert()
        task = make_task("Meter Harm Project", dataset=make_dataset("Harm images").name, title="Harm survey")
        frappe.set_user("Administrator")
        return task


class TestDispatch(_Base):
    def _dispatch(self, inst):
        task = MagicMock()
        task.name, task.status, task.title = "T-harm", "Provisioning", "t"
        task.compute_instance, task.processing_options = inst, None
        task.writes = {}
        task.db_set = MagicMock(side_effect=lambda values, value=None, **_: task.writes.update(
            values if isinstance(values, dict) else {values: value}))
        client = MagicMock()
        client.info.return_value = {}
        client.create_task.return_value = {"uuid": "node-uuid-9"}
        node = {"name": inst, "hostname": "10.0.0.1", "port": 3000, "token": "t", "compute_instance": inst}
        with patch("frappe.get_doc", return_value=task), \
                patch.object(task_runner, "_acquire_node", return_value=(node, "ready")), \
                patch.object(task_runner, "_client_for", return_value=client), \
                patch.object(task_runner.storage, "configured", return_value=False), \
                patch.object(task_runner, "_get_task_images", return_value=[("a.jpg", "/x/a.jpg")]):
            task_runner.process_task("T-harm")
        return task

    def test_dispatch_stamps_the_instance(self):
        inst = make_instance(self.org, status="Ready", dispatched_at=None, released_at=None, terminated_at=None,
                             release_reason=None)
        task = self._dispatch(inst)
        self.assertEqual(task.writes["status"], "Running")
        self.assertIsNotNone(frappe.db.get_value(INSTANCE, inst, "dispatched_at"))

    def test_a_stamp_that_raises_does_not_cost_the_dispatch(self):
        inst = make_instance(self.org, status="Ready", dispatched_at=None, released_at=None, terminated_at=None,
                             release_reason=None)
        with patch.object(compute, "stamp_once", side_effect=RuntimeError("metering bug")):
            task = self._dispatch(inst)
        self.assertEqual((task.writes["status"], task.writes["node_task_id"]), ("Running", "node-uuid-9"))
        self.assertIsNone(frappe.db.get_value(INSTANCE, inst, "dispatched_at"))

    def test_a_stamp_that_fails_in_the_database_does_not_poison_the_dispatch(self):
        # e.g. the column is missing because the code was deployed before migrate.
        inst = make_instance(self.org, status="Ready", dispatched_at=None, released_at=None, terminated_at=None,
                             release_reason=None)
        with patch.dict(compute._STAMP_FIELDS, {"dispatched_at": "time"}), \
                patch.object(compute, "now_datetime", side_effect=_broken_statement):
            task = self._dispatch(inst)
        self.assertEqual(task.writes["status"], "Running")
        self.assertTransactionUsable()

    def test_a_missing_column_is_survived(self):
        inst = make_instance(self.org, status="Ready", released_at=None, terminated_at=None, release_reason=None)
        with patch.dict(compute._STAMP_FIELDS, {"no_such_column": "time"}):
            compute.stamp_once(inst, "no_such_column")
        self.assertTransactionUsable()
        self.assertEqual(frappe.db.get_value(INSTANCE, inst, "status"), "Ready")


class TestTeardown(_Base):
    def test_a_stamp_that_fails_does_not_keep_a_machine_alive(self):
        inst = make_instance(self.org, status="Ready", released_at=None, terminated_at=None, release_reason=None)
        client = MagicMock()
        client.destroy.return_value = {}
        real_sql = frappe.db.sql

        def sql(query, *args, **kwargs):
            if "UPDATE `tabWebODM Compute Instance` SET `release" in str(query):
                return real_sql("select * from tab_no_such_table_for_metering")
            return real_sql(query, *args, **kwargs)

        with patch.object(compute, "ProvisionerClient", return_value=client), patch.object(frappe.db, "sql", side_effect=sql):
            destroyed = compute.destroy_instance(frappe.get_doc(INSTANCE, inst), "task:Completed")
        self.assertTrue(destroyed)
        client.destroy.assert_called_once()
        row = frappe.db.get_value(INSTANCE, inst, ["status", "terminated_at", "released_at"], as_dict=True)
        self.assertEqual(row.status, "Terminated")
        self.assertIsNotNone(row.terminated_at)
        self.assertIsNone(row.released_at)  # the fact is lost; the machine is not
        self.assertTransactionUsable()

        # ...and metering then waives or falls back; it never guesses upward.
        sweep.run()
        (rec,) = usage_rows(self.org)
        self.assertLessEqual(rec["quantity"], 3600 + 5 * 3600)

    def test_a_stamp_that_raises_does_not_keep_a_machine_alive(self):
        # stamp_once is written never to raise; if it ever did, the machine
        # must still be given back — an unrecorded release is a waived hour,
        # an undestroyed machine is a bill.
        inst = make_instance(self.org, status="Ready", released_at=None, terminated_at=None, release_reason=None)
        client = MagicMock()
        client.destroy.return_value = {}
        with patch.object(compute, "ProvisionerClient", return_value=client), \
                patch.object(compute, "stamp_once", side_effect=RuntimeError("metering bug")):
            compute.release_for_task(frappe._dict(name="T", status="Completed", compute_instance=inst))
        client.destroy.assert_called_once()
        self.assertEqual(frappe.db.get_value(INSTANCE, inst, "status"), "Terminated")
        self.assertTransactionUsable()


class TestDelete(_Base):
    def test_a_recorder_that_raises_does_not_stop_a_run_being_deleted(self):
        run = make_run(self.user)
        with patch.object(sweep, "record_run", side_effect=RuntimeError("metering bug")):
            frappe.delete_doc(RUN, run.name, force=True, ignore_permissions=True)
        self.assertFalse(frappe.db.exists(RUN, run.name))
        self.assertFalse(usage_rows(self.org))

    def test_a_recorder_that_fails_in_the_database_does_not_poison_the_delete(self):
        run = make_run(self.user)
        with patch.object(ledger, "record", side_effect=_broken_statement):
            frappe.delete_doc(RUN, run.name, force=True, ignore_permissions=True)
        self.assertFalse(frappe.db.exists(RUN, run.name))
        self.assertTransactionUsable()

    def test_an_unimportable_metering_package_does_not_stop_a_delete(self):
        run = make_run(self.user)
        with patch.dict(sys.modules, {"webodm_core.metering.sweep": None}):
            frappe.delete_doc(RUN, run.name, force=True, ignore_permissions=True)
        self.assertFalse(frappe.db.exists(RUN, run.name))

    def test_deleting_a_task_with_runs_survives_a_broken_recorder(self):
        task = self.real_task()
        runs = [make_run(self.user, task=task.name), make_run(self.user, task=task.name, status="Failed")]
        with patch.object(ledger, "record", side_effect=_broken_statement):
            frappe.delete_doc("WebODM Task", task.name, force=True, ignore_permissions=True)
        self.assertFalse(frappe.db.exists("WebODM Task", task.name))
        for run in runs:
            self.assertFalse(frappe.db.exists(RUN, run.name))
        self.assertTransactionUsable()

    def test_replacing_a_run_survives_a_broken_recorder(self):
        from webodm_core.api import plugins as plugins_api

        task = self.real_task()
        run = make_run(self.user, plugin="test.metering-plugin", task=task.name)
        with patch.object(sweep, "run_inputs", side_effect=_broken_statement):
            plugins_api._delete_previous_runs("test.metering-plugin", task.name)
        self.assertFalse(frappe.db.exists(RUN, run.name))
        self.assertTransactionUsable()

    def test_a_broken_recorder_does_not_stop_an_instance_being_deleted(self):
        inst = make_instance(self.org)
        with patch.object(sweep, "record_instance", side_effect=RuntimeError("metering bug")):
            frappe.delete_doc(INSTANCE, inst, force=True, ignore_permissions=True)
        self.assertFalse(frappe.db.exists(INSTANCE, inst))

    def test_a_run_missed_at_deletion_is_simply_not_recorded(self):
        # The cost of a failed capture is a missing record — never a wrong one,
        # never a failed delete. It is in the error log.
        run = make_run(self.user)
        with patch.object(sweep, "record_run", side_effect=RuntimeError("metering bug")):
            frappe.delete_doc(RUN, run.name, force=True, ignore_permissions=True)
        self.assertIn("metering bug", frappe.log_error.call_args.args[0])
        sweep.run()
        self.assertFalse(usage_rows(self.org))


class TestStructure(FrappeTestCase):
    """The pipeline does not depend on metering; metering reads the pipeline's rows."""

    _IMPORT = re.compile(r"^\s*(from|import)\s+webodm_core\.metering", re.MULTILINE)

    def test_the_pipeline_never_imports_metering(self):
        for module in (compute, task_runner, runner):
            self.assertIsNone(self._IMPORT.search(inspect.getsource(module)), module.__name__)

    def test_the_hook_module_imports_nothing_at_load(self):
        from webodm_core.metering import hooks

        source = inspect.getsource(hooks)
        top_level = [line for line in source.splitlines() if re.match(r"^(from|import)\s", line)]
        self.assertEqual(top_level, ["import frappe"])
        import webodm_core.metering as package

        self.assertIsNone(re.search(r"^(from|import)\s", inspect.getsource(package), re.MULTILINE))

    def test_hooks_are_registered(self):
        events = frappe.get_hooks("doc_events")
        self.assertIn("webodm_core.metering.hooks.before_run_delete", events["WebODM Plugin Run"]["on_trash"])
        self.assertIn("webodm_core.metering.hooks.before_instance_delete", events["WebODM Compute Instance"]["on_trash"])
        # The organization stamp on runs is still there (the dict was extended, not replaced).
        self.assertIn("webodm_core.tenancy_hooks.stamp_organization", events["WebODM Plugin Run"]["before_insert"])

    def test_scheduler_entries_are_separate_and_resolve(self):
        cron = frappe.get_hooks("scheduler_events")["cron"]
        self.assertIn("webodm_core.metering.storage.sample_job", cron["*/10 * * * *"])
        self.assertIn("webodm_core.metering.sweep.run_job", cron["*/5 * * * *"])
        for path in ("webodm_core.metering.storage.sample_job", "webodm_core.metering.sweep.run_job"):
            self.assertTrue(callable(frappe.get_attr(path)))
        # Storage measurement must not share an entry with anything that could delay it.
        self.assertEqual(cron["*/10 * * * *"], ["webodm_core.metering.storage.sample_job"])
