import unittest
from unittest.mock import patch, MagicMock

import frappe

from webodm_core.api import tiles


class TestVolumeProxy(unittest.TestCase):
    def test_missing_dsm_raises(self):
        with patch.object(tiles, "_resolve_raster_path", side_effect=frappe.DoesNotExistError):
            with self.assertRaises(frappe.DoesNotExistError):
                tiles.volume("TASK-1", '{"type":"Polygon","coordinates":[]}')

    def test_forwards_path_and_polygon(self):
        resp = MagicMock()
        resp.json.return_value = {"volume": 1.0, "fill": 1.0, "cut": 0.0, "area": 2.0, "base_plane": "best_fit"}
        resp.raise_for_status.return_value = None
        with patch.object(tiles, "_resolve_raster_path", return_value="/abs/dsm.tif"), \
             patch.object(tiles, "_geospatial_url", return_value="http://geo:5000"), \
             patch.object(tiles.requests, "post", return_value=resp) as post:
            out = tiles.volume(
                "TASK-1",
                '{"type":"Polygon","coordinates":[[[0,0],[0,1],[1,1],[0,0]]]}',
            )
        self.assertEqual(out["volume"], 1.0)
        _args, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["path"], "/abs/dsm.tif")
        self.assertEqual(kwargs["json"]["polygon"]["type"], "Polygon")

    def _mock_post(self):
        resp = MagicMock()
        resp.json.return_value = {
            "volume": 1.0, "fill": 1.0, "cut": 0.0, "area": 2.0,
            "base_plane": "triangulate",
        }
        resp.raise_for_status.return_value = None
        return resp

    def _call_volume(self, **kwargs):
        """Invoke the proxy with mocks, returning the forwarded JSON body."""
        resp = self._mock_post()
        with patch.object(tiles, "_resolve_raster_path", return_value="/abs/dsm.tif"), \
             patch.object(tiles, "_geospatial_url", return_value="http://geo:5000"), \
             patch.object(tiles.requests, "post", return_value=resp) as post:
            tiles.volume(
                "TASK-1",
                '{"type":"Polygon","coordinates":[[[0,0],[0,1],[1,1],[0,0]]]}',
                **kwargs,
            )
        return post.call_args[1]["json"]

    def test_forwards_requested_method(self):
        self.assertEqual(self._call_volume(method="plane")["method"], "plane")

    def test_defaults_method_to_triangulate(self):
        self.assertEqual(self._call_volume()["method"], "triangulate")

    def test_rejects_unknown_method_without_calling_service(self):
        with patch.object(tiles, "_resolve_raster_path", return_value="/abs/dsm.tif"), \
             patch.object(tiles.requests, "post") as post:
            with self.assertRaises(frappe.ValidationError):
                tiles.volume(
                    "TASK-1",
                    '{"type":"Polygon","coordinates":[[[0,0],[0,1],[1,1],[0,0]]]}',
                    method="bogus",
                )
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
