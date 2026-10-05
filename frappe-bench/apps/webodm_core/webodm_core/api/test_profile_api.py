"""Elevation profile proxy: validation, permission check, octree resolution, forwarding.

Plain ``unittest`` with the Frappe and storage calls mocked (like
``test_tiles``), so it runs without a site; the octree state machine, file
ownership and cache code it relies on are covered by ``test_pointcloud``.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import frappe

from webodm_core.api import pointcloud as api
from webodm_core.plugins import geospatial
from webodm_core.storage import cache
from webodm_core.webodm_core.processing import potree

LINE = [[500000.0, 4500000.0], [500100.0, 4500000.0]]
FILES = {"metadata": "/abs/metadata.json", "hierarchy": "/abs/hierarchy.bin", "octree": "s3://b/o/octree.bin"}
RESULT = {"distance": [0.5, 1.5], "ground": [100.0, None], "top": [101.0, None], "stats": {"length": 100.0}}


def _task(point_cloud="/private/files/t1_georeferenced_model.laz"):
    task = MagicMock()
    task.name = "t1"
    task.point_cloud = point_cloud
    task.organization = "org-1"
    return task


class TestProfileApi(unittest.TestCase):
    def setUp(self):
        self.task = _task()
        patch.object(api, "_get_task_checked", return_value=self.task).start()
        self.sources = patch.object(potree, "file_sources", return_value=FILES).start()
        self.call = patch.object(geospatial, "pointcloud_profile", return_value=RESULT).start()
        self.addCleanup(patch.stopall)

    # -- forwarding ---------------------------------------------------------

    def test_forwards_resolved_files_line_and_parameters(self):
        out = api.profile("t1", line='[[500000,4500000],[500100,4500000]]', width="2.5", step="0.5", ground_window="15")
        self.assertIs(out, RESULT)
        self.sources.assert_called_once_with(self.task)
        args, kwargs = self.call.call_args
        self.assertEqual(args[0], FILES)
        self.assertEqual(args[1], LINE)
        self.assertEqual(kwargs["width"], 2.5)
        self.assertEqual(kwargs["step"], 0.5)
        self.assertEqual(kwargs["ground_window"], 15.0)
        self.assertEqual(kwargs["timeout"], api.PROFILE_TIMEOUT)

    def test_defaults_width_and_leaves_step_automatic(self):
        api.profile("t1", line=LINE)
        kwargs = self.call.call_args[1]
        self.assertEqual(kwargs["width"], 1.0)
        self.assertIsNone(kwargs["step"])
        self.assertIsNone(kwargs["ground_window"])
        api.profile("t1", line=LINE, step="0", ground_window="")
        kwargs = self.call.call_args[1]
        self.assertIsNone(kwargs["step"])
        self.assertIsNone(kwargs["ground_window"])

    def test_permission_check_runs_before_anything_else(self):
        api._get_task_checked.side_effect = frappe.PermissionError("no")
        with self.assertRaises(frappe.PermissionError):
            api.profile("t1", line=LINE)
        self.sources.assert_not_called()
        self.call.assert_not_called()

    # -- validation ---------------------------------------------------------

    def test_requires_task_name_and_point_cloud(self):
        with self.assertRaises(frappe.ValidationError):
            api.profile(None, line=LINE)
        self.task.point_cloud = None
        with self.assertRaises(frappe.DoesNotExistError):
            api.profile("t1", line=LINE)
        self.call.assert_not_called()

    def test_rejects_bad_lines(self):
        for bad in (None, "not json", "[]", [[0, 0]], [[0, 0], [1]], [[0, 0], ["x", 1]], [[0, 0], [float("nan"), 1]],
                    [[i, 0] for i in range(51)]):
            with self.subTest(bad=bad):
                with self.assertRaises(frappe.ValidationError):
                    api.profile("t1", line=bad)
        self.call.assert_not_called()

    def test_rejects_width_and_step_out_of_range(self):
        for kwargs in ({"width": 0}, {"width": 0.01}, {"width": 1000}, {"step": 0.001}, {"step": -1}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(frappe.ValidationError):
                    api.profile("t1", line=LINE, **kwargs)
        self.call.assert_not_called()

    # -- errors -------------------------------------------------------------

    def test_octree_not_ready_is_a_validation_error_with_the_reason(self):
        self.sources.side_effect = potree.OctreeNotReady("the point cloud has not been prepared for viewing yet")
        with self.assertRaises(frappe.ValidationError) as ctx:
            api.profile("t1", line=LINE)
        self.assertIn("not been prepared", str(ctx.exception))
        self.call.assert_not_called()

    def test_service_errors_are_reported(self):
        self.call.side_effect = geospatial.GeospatialError("profile failed: width must be between 0.05 and 200.0 m")
        with self.assertRaises(frappe.ValidationError) as ctx:
            api.profile("t1", line=LINE)
        self.assertIn("width must be", str(ctx.exception))
        self.call.side_effect = geospatial.GeospatialUnavailable("connection refused")
        with self.assertRaises(frappe.ValidationError) as ctx:
            api.profile("t1", line=LINE)
        self.assertIn("unavailable", str(ctx.exception).lower())


class TestFileSources(unittest.TestCase):
    """``potree.file_sources``: Ready state, ownership check, cache-first / S3-second."""

    def setUp(self):
        self.task = _task()
        self.rows = [
            MagicMock(kind="potree_metadata", file_url="/private/files/t1_potree_metadata.json", storage_key="orgs/o/t/metadata.json"),
            MagicMock(kind="potree_hierarchy", file_url="/private/files/t1_potree_hierarchy.bin", storage_key="orgs/o/t/hierarchy.bin"),
            MagicMock(kind="potree_octree", file_url="/private/files/t1_potree_octree.bin", storage_key="orgs/o/t/octree.bin"),
        ]
        patch.object(potree.assets, "potree_rows", return_value=self.rows).start()
        self.state = patch.object(potree, "state", return_value={"status": "Ready"}).start()
        self.owned = patch.object(potree, "file_doc_for_url").start()
        self.addCleanup(patch.stopall)

    def test_resolves_each_file_cache_first_then_s3(self):
        def resolve(file_url, storage_key, org):
            self.assertEqual(org, "org-1")
            if file_url.endswith("octree.bin"):
                return "s3", f"s3://bucket/{storage_key}"
            return "local", f"/sites/private/files/{file_url.rsplit('/', 1)[-1]}"

        with patch.object(cache, "resolve_source", side_effect=resolve):
            files = potree.file_sources(self.task)
        self.assertEqual(files, {
            "metadata": "/sites/private/files/t1_potree_metadata.json",
            "hierarchy": "/sites/private/files/t1_potree_hierarchy.bin",
            "octree": "s3://bucket/orgs/o/t/octree.bin",
        })
        self.state.assert_called_once_with(self.task, warm=False)
        # Every row proved to be a File attached to this task before its blob was resolved.
        self.assertEqual(self.owned.call_count, 3)
        for call in self.owned.call_args_list:
            self.assertEqual(call.kwargs, {"attached_to_doctype": "WebODM Task", "attached_to_name": "t1"})

    def test_not_ready_raises_with_the_state_error(self):
        self.state.return_value = {"status": "Failed", "error": "conversion failed: boom"}
        with self.assertRaises(potree.OctreeNotReady) as ctx:
            potree.file_sources(self.task)
        self.assertIn("boom", str(ctx.exception))
        self.state.return_value = {"status": "", "error": None}
        with self.assertRaises(potree.OctreeNotReady) as ctx:
            potree.file_sources(self.task)
        self.assertIn("not been prepared", str(ctx.exception))

    def test_foreign_file_is_refused(self):
        self.owned.side_effect = frappe.PermissionError("not attached")
        with patch.object(cache, "resolve_source") as resolve:
            with self.assertRaises(frappe.PermissionError):
                potree.file_sources(self.task)
        resolve.assert_not_called()

    def test_evicted_without_storage_is_not_ready(self):
        with patch.object(cache, "resolve_source", side_effect=cache.CacheMiss("gone")):
            with self.assertRaises(potree.OctreeNotReady) as ctx:
                potree.file_sources(self.task)
        self.assertIn("not available on this host", str(ctx.exception))

    def test_missing_row_is_not_ready(self):
        del self.rows[2]
        with patch.object(cache, "resolve_source", return_value=("local", "/x")):
            with self.assertRaises(potree.OctreeNotReady):
                potree.file_sources(self.task)


class TestGeospatialClient(unittest.TestCase):
    def test_posts_files_line_and_optional_parameters(self):
        resp = MagicMock()
        resp.json.return_value = RESULT
        resp.raise_for_status.return_value = None
        with patch.object(geospatial, "geospatial_url", return_value="http://geo:5000"), \
             patch.object(geospatial.requests, "post", return_value=resp) as post:
            out = geospatial.pointcloud_profile(FILES, LINE, width=2.0, step=None, ground_window=None, timeout=42)
        self.assertIs(out, RESULT)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://geo:5000/pointcloud/profile")
        self.assertEqual(kwargs["json"], {**FILES, "line": LINE, "width": 2.0})
        self.assertEqual(kwargs["timeout"], 42)
        with patch.object(geospatial, "geospatial_url", return_value="http://geo:5000"), \
             patch.object(geospatial.requests, "post", return_value=resp) as post:
            geospatial.pointcloud_profile(FILES, LINE, width=1.0, step=0.5, ground_window=0)
        self.assertEqual(post.call_args[1]["json"]["step"], 0.5)
        self.assertEqual(post.call_args[1]["json"]["ground_window"], 0)

    def test_maps_4xx_to_error_and_5xx_to_unavailable(self):
        def failing(status, detail):
            response = MagicMock(status_code=status)
            response.json.return_value = {"detail": detail}
            err = geospatial.requests.HTTPError(response=response)
            r = MagicMock()
            r.raise_for_status.side_effect = err
            return r

        with patch.object(geospatial, "geospatial_url", return_value="http://geo:5000"), \
             patch.object(geospatial.requests, "post", return_value=failing(422, "line needs at least two vertices")):
            with self.assertRaises(geospatial.GeospatialError) as ctx:
                geospatial.pointcloud_profile(FILES, LINE)
        self.assertIn("two vertices", str(ctx.exception))
        with patch.object(geospatial, "geospatial_url", return_value="http://geo:5000"), \
             patch.object(geospatial.requests, "post", return_value=failing(502, "object storage: range read failed")):
            with self.assertRaises(geospatial.GeospatialUnavailable):
                geospatial.pointcloud_profile(FILES, LINE)

    def test_unexpected_payload_is_an_error(self):
        resp = MagicMock()
        resp.json.return_value = {"nope": 1}
        resp.raise_for_status.return_value = None
        with patch.object(geospatial, "geospatial_url", return_value="http://geo:5000"), \
             patch.object(geospatial.requests, "post", return_value=resp):
            with self.assertRaises(geospatial.GeospatialError):
                geospatial.pointcloud_profile(FILES, LINE)


if __name__ == "__main__":
    unittest.main()
