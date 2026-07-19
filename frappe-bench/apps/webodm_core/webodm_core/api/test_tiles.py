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


if __name__ == "__main__":
    unittest.main()
