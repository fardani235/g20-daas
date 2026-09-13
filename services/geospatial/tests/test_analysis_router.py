import asyncio
import json

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from rasterio.transform import from_origin

from app.analysis import all_ops
from app.routers.analysis import AnalysisRunRequest, list_analysis, run_analysis


def _dem(path):
    arr = np.full((40, 40), 100.0, dtype="float32")
    arr[10:30, 10:30] = 130.0
    tr = from_origin(500000, 4500000, 5.0, 5.0)
    with rasterio.open(
        path, "w", driver="GTiff", height=40, width=40, count=1,
        dtype="float32", crs="EPSG:32615", transform=tr, nodata=-9999,
    ) as ds:
        ds.write(arr, 1)
    return str(path)


def test_catalog_matches_registered_ops():
    res = asyncio.run(list_analysis())
    assert res["schema_version"] == 1
    ids = {o["op_id"] for o in res["operations"]}
    assert ids == {op.op_id for op in all_ops()}
    for entry in res["operations"]:
        assert set(entry) >= {
            "op_id", "label", "description", "version",
            "params_schema", "output_kind", "render_kind", "inputs",
        }


def test_catalog_declares_operation_inputs():
    res = asyncio.run(list_analysis())
    by_id = {o["op_id"]: o for o in res["operations"]}
    contours = by_id["contours"]
    assert contours["inputs"] == [{"name": "raster", "datasets": ["dsm", "dtm"]}]
    assert by_id["hillshade"]["inputs"][0]["name"] == "raster"


def test_unknown_op_is_404():
    req = AnalysisRunRequest(inputs={}, params={}, output_path="/tmp/out.tif")
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(run_analysis("does-not-exist", req))
    assert excinfo.value.status_code == 404


def test_invalid_params_is_422(tmp_path):
    src = _dem(tmp_path / "d.tif")
    req = AnalysisRunRequest(
        inputs={"raster": src},
        params={"interval_m": -1},
        output_path=str(tmp_path / "out.geojson"),
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(run_analysis("contours", req))
    assert excinfo.value.status_code == 422


def test_missing_input_is_404(tmp_path):
    req = AnalysisRunRequest(
        inputs={"raster": str(tmp_path / "nope.tif")},
        params={},
        output_path=str(tmp_path / "out.tif"),
    )
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(run_analysis("hillshade", req))
    assert excinfo.value.status_code == 404


def test_run_hillshade_succeeds(tmp_path):
    src = _dem(tmp_path / "d.tif")
    out = tmp_path / "out.tif"
    req = AnalysisRunRequest(inputs={"raster": src}, params={}, output_path=str(out))
    res = asyncio.run(run_analysis("hillshade", req))
    assert res["op_id"] == "hillshade"
    assert res["output_kind"] == "raster"
    assert res["output_path"] == str(out)
    assert out.is_file()
