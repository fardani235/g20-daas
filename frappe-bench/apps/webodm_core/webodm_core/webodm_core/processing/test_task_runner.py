import unittest

from webodm_core.webodm_core.processing.task_runner import (
    _build_node_options,
    _status_action,
)


def _as_map(options):
    """NodeODM options are a list of {name, value}; flatten to a dict for assertions."""
    return {o["name"]: o["value"] for o in options}


class TestBuildNodeOptions(unittest.TestCase):
    def test_returns_nodeodm_array_shape(self):
        # NodeODM's POST /task/new requires options as an array of
        # {"name": ..., "value": ...}, NOT a bare {name: value} dict. A dict is
        # silently dropped by NodeODM's filterOptions and ODM runs with defaults.
        opts = _build_node_options({"dsm": True})
        self.assertIsInstance(opts, list)
        self.assertTrue(all(set(o) == {"name", "value"} for o in opts))

    def test_dsm_selected_forwards_dsm_flag(self):
        self.assertEqual(_as_map(_build_node_options({"dsm": True})).get("dsm"), True)

    def test_dtm_and_dsm_together(self):
        m = _as_map(_build_node_options({"dsm": True, "dtm": True}))
        self.assertEqual(m.get("dsm"), True)
        self.assertEqual(m.get("dtm"), True)

    def test_model_and_pointcloud_map_to_odm_flag_names(self):
        m = _as_map(_build_node_options({"model": True, "pointCloud": True}))
        self.assertEqual(m.get("glb"), True)
        self.assertEqual(m.get("pc-ept"), True)

    def test_orthophoto_resolution_passed_as_value(self):
        m = _as_map(_build_node_options({"orthophoto": True, "orthophotoResolution": 3}))
        self.assertEqual(m.get("orthophoto-resolution"), 3)

    def test_orthophoto_checked_does_not_skip(self):
        m = _as_map(_build_node_options({"orthophoto": True}))
        self.assertNotIn("skip-orthophoto", m)

    def test_orthophoto_unchecked_sends_skip_orthophoto(self):
        # ODM has no --orthophoto flag; the orthophoto is produced by default and
        # can only be suppressed with --skip-orthophoto.
        m = _as_map(_build_node_options({"orthophoto": False, "dsm": True}))
        self.assertEqual(m.get("skip-orthophoto"), True)

    def test_unselected_outputs_are_absent(self):
        m = _as_map(_build_node_options({"orthophoto": True, "dsm": False, "dtm": False}))
        self.assertNotIn("dsm", m)
        self.assertNotIn("dtm", m)
        self.assertNotIn("glb", m)
        self.assertNotIn("pc-ept", m)

    def test_falsy_resolution_not_sent(self):
        m = _as_map(_build_node_options({"orthophoto": True, "orthophotoResolution": None}))
        self.assertNotIn("orthophoto-resolution", m)

    def test_list_input_passes_through_verbatim(self):
        opts = [{"name": "dsm", "value": True}, {"name": "feature-quality", "value": "ultra"}]
        self.assertEqual(_build_node_options(opts), opts)

    def test_list_input_drops_malformed_entries(self):
        opts = [{"name": "dsm", "value": True}, {"bad": "x"}, "nope"]
        self.assertEqual(_build_node_options(opts), [{"name": "dsm", "value": True}])


class TestStatusAction(unittest.TestCase):
    """Map NodeODM task status codes to a poll action. NodeODM codes:
    QUEUED=10, RUNNING=20, FAILED=30, COMPLETED=40, CANCELED=50. The old code
    conflated these: it called _download_assets on FAILED(30) (treating a failure
    as a completion) and lumped CANCELED(50) in with COMPLETED via `>= 40`, so a
    canceled task got marked Failed and a real failure never set Failed at all."""

    def test_queued_keeps_running(self):
        self.assertEqual(_status_action(10, 0), "running")

    def test_running_keeps_running(self):
        self.assertEqual(_status_action(20, 42), "running")

    def test_failed_code_30_is_failed(self):
        # FAILED must NOT download assets.
        self.assertEqual(_status_action(30, 0), "failed")

    def test_completed_code_40_downloads(self):
        self.assertEqual(_status_action(40, 100), "download")

    def test_canceled_code_50_is_cancelled(self):
        # CANCELED is a terminal cancel, not a failure and not a completion.
        self.assertEqual(_status_action(50, 0), "cancelled")

    def test_dict_status_uses_code(self):
        self.assertEqual(_status_action({"code": 30}, 0), "failed")

    def test_unknown_high_code_is_failed(self):
        # Defensive: any unexpected terminal-ish code is a failure, never a silent
        # completion.
        self.assertEqual(_status_action(99, 0), "failed")


class TestPendingTasksAreNotAutoStarted(unittest.TestCase):
    """The scheduler sweep must never start a task the user did not start.

    Regression: the every-minute cron filtered on status="Pending", so any
    freshly uploaded task went Pending -> Running within 60s without anyone
    pressing Start, and the auto_start_processing setting was meaningless
    because processing began whether it was on or off."""

    def test_sweep_queries_queued_not_pending(self):
        from unittest.mock import patch
        from webodm_core.webodm_core.processing import task_runner

        with patch("frappe.get_all") as get_all, patch("frappe.enqueue"):
            get_all.return_value = []
            task_runner.process_pending_tasks()

        filters = get_all.call_args.kwargs["filters"]
        self.assertEqual(filters, {"status": "Queued"})
        self.assertNotEqual(
            filters.get("status"), "Pending",
            "sweeping Pending auto-starts tasks nobody asked to run",
        )

    def test_worker_refuses_a_merely_pending_task(self):
        # Defence in depth: even if something enqueues a Pending task, the
        # worker must bail before it can reach the node and set Running.
        from unittest.mock import MagicMock, patch
        from webodm_core.webodm_core.processing import task_runner

        task = MagicMock()
        task.status = "Pending"
        with patch("frappe.get_doc", return_value=task), patch("frappe.get_all") as get_all:
            task_runner.process_task("TASK-PENDING")

        get_all.assert_not_called()      # never looked for a node
        task.db_set.assert_not_called()  # never wrote a status


if __name__ == "__main__":
    unittest.main()
