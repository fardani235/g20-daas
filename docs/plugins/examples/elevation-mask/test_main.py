"""Unit tests for the Elevation Mask example plugin.

Run with ``python -m pytest`` from this directory. Only numpy and rasterio are
needed — the same libraries the sandbox provides at run time.
"""

import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

import main as plugin


@pytest.fixture
def dem(tmp_path):
    """A 20x20 DEM at 100 m with a 130 m block in the middle and one nodata cell."""
    arr = np.full((20, 20), 100.0, dtype="float32")
    arr[5:15, 5:15] = 130.0
    arr[0, 0] = -9999
    path = tmp_path / "dsm.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=20, width=20, count=1, dtype="float32",
        crs="EPSG:32633", transform=from_origin(500000, 4500000, 1.0, 1.0), nodata=-9999,
    ) as ds:
        ds.write(arr, 1)
    return str(path)


def _request(tmp_path, dem, params):
    req = {
        "inputs": {"raster": dem},
        "params": params,
        "output_path": str(tmp_path / "out.tif"),
        "result_path": str(tmp_path / "result.json"),
        "work_dir": str(tmp_path),
    }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(req))
    return req, str(path)


def test_masks_cells_above_threshold(tmp_path, dem):
    req, request_path = _request(tmp_path, dem, {"threshold": 120})
    assert plugin.main(["main.py", request_path]) == 0

    with rasterio.open(req["output_path"]) as ds:
        out = ds.read(1)
        assert ds.nodata == plugin.NODATA
        assert ds.crs.to_epsg() == 32633
    assert out[10, 10] == 1          # inside the 130 m block
    assert out[2, 2] == 0            # 100 m plain
    assert out[0, 0] == plugin.NODATA

    result = json.loads((tmp_path / "result.json").read_text())
    assert result["metadata"]["masked_pixels"] == 100
    assert 0 < result["metadata"]["masked_fraction"] < 1


def test_below_mode_inverts_the_mask(tmp_path, dem):
    req, request_path = _request(tmp_path, dem, {"threshold": 120, "mode": "below"})
    plugin.run(req)
    with rasterio.open(req["output_path"]) as ds:
        out = ds.read(1)
    assert out[10, 10] == 0
    assert out[2, 2] == 1


def test_rejects_unknown_mode(tmp_path, dem):
    req, _ = _request(tmp_path, dem, {"threshold": 120, "mode": "sideways"})
    with pytest.raises(ValueError):
        plugin.run(req)
