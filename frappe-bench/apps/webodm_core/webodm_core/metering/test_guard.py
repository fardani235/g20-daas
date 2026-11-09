"""``guard.isolated``: the wrapper every metering write on a foreign code path goes through.

Everything else relies on four properties of it, so they are pinned on
their own: an error inside is swallowed and logged; what the block wrote is
undone and what came before it is kept; the surrounding transaction is
usable afterwards even when the error was a failed SQL statement; and it
never raises itself.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from webodm_core.metering import guard

NOTE = "ToDo"


def _note(text):
    return frappe.get_doc({"doctype": NOTE, "description": text}).insert(ignore_permissions=True).name


class TestIsolated(FrappeTestCase):
    def setUp(self):
        self.log = patch("frappe.log_error").start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        patch.stopall()
        frappe.db.rollback()

    def test_an_error_is_swallowed_and_logged(self):
        with guard.isolated("recording the thing") as ok:
            self.assertTrue(ok)
            raise RuntimeError("metering bug")
        message, title = self.log.call_args.args
        self.assertIn("recording the thing", message)
        self.assertIn("RuntimeError: metering bug", message)
        self.assertEqual(title, "WebODM Metering")

    def test_what_the_block_wrote_is_undone_and_what_came_before_is_kept(self):
        before = _note("written by the caller, before metering ran")
        with guard.isolated("x"):
            inside = _note("written by metering, then it failed")
            raise RuntimeError("boom")
        self.assertTrue(frappe.db.exists(NOTE, before))
        self.assertFalse(frappe.db.exists(NOTE, inside))

    def test_a_failed_statement_does_not_poison_the_callers_transaction(self):
        before = _note("the caller's own work")
        with guard.isolated("x"):
            frappe.db.sql("select * from tab_no_such_table_for_metering")
        # Without the savepoint PostgreSQL would refuse this with
        # "current transaction is aborted".
        self.assertTrue(frappe.db.exists(NOTE, before))
        after = _note("the caller carries on")
        self.assertTrue(frappe.db.exists(NOTE, after))

    def test_success_keeps_the_work(self):
        with guard.isolated("x") as ok:
            inside = _note("recorded")
        self.assertTrue(ok)
        self.assertTrue(frappe.db.exists(NOTE, inside))
        self.log.assert_not_called()

    def test_no_savepoint_means_no_work_and_no_exception(self):
        with patch.object(frappe.db, "savepoint", side_effect=RuntimeError("connection lost")):
            with guard.isolated("x") as ok:
                self.assertFalse(ok)
        self.assertIn("could not open a savepoint", self.log.call_args.args[0])

    def test_a_shutdown_is_not_swallowed(self):
        with self.assertRaises(KeyboardInterrupt):
            with guard.isolated("x"):
                raise KeyboardInterrupt

    def test_a_failing_error_log_does_not_raise_either(self):
        self.log.side_effect = RuntimeError("error log table is gone")
        with guard.isolated("x"):
            raise RuntimeError("boom")

    def test_the_same_problem_is_logged_once_an_hour(self):
        seen = {}
        with patch.object(frappe.cache, "get_value", side_effect=lambda key: seen.get(key)), \
                patch.object(frappe.cache, "set_value", side_effect=lambda key, value, **kw: seen.update({key: value})):
            for _ in range(5):
                with guard.isolated("recording instance X", once_key="instances:X"):
                    raise RuntimeError("still broken")
            with guard.isolated("recording instance Y", once_key="instances:Y"):
                raise RuntimeError("a different one")
        self.assertEqual(self.log.call_count, 2)

    def test_a_cache_outage_means_more_log_lines_not_fewer(self):
        with patch.object(frappe.cache, "get_value", side_effect=RuntimeError("redis down")):
            for _ in range(2):
                with guard.isolated("x", once_key="k"):
                    raise RuntimeError("boom")
        self.assertEqual(self.log.call_count, 2)
