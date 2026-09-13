import asyncio
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from rasterio.transform import from_origin

from app.analysis.detection import detector, models
from app.analysis.detection.detector import DetectionParams
from app.routers.analysis import AnalysisValidateRequest, list_analysis, validate_analysis

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    d = tmp_path / "models"
    d.mkdir()
    monkeypatch.setenv(models.MODELS_DIR_ENV, str(d))
    shutil.copy(FIXTURES / "tiny_detector_detects.onnx", d / "detector.onnx")
    (d / "labels.txt").write_text("\n".join(f"c{i}" for i in range(80)) + "\n")
    return d


def _ortho(path, width=256, height=256, crs="EPSG:32615"):
    arr = np.zeros((3, height, width), dtype="uint8")
    arr[0] = 100
    arr[1] = 120
    arr[2] = 140
    transform = from_origin(500000, 4500000, 1.0, 1.0)
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=3,
        dtype="uint8", crs=crs, transform=transform,
    ) as ds:
        ds.write(arr)
    return str(path)


def test_params_defaults_and_validation():
    params = DetectionParams()
    assert params.confidence == 0.25
    assert params.tile_size == 640
    assert params.overlap == 64
    with pytest.raises(Exception):
        DetectionParams(confidence=1.5)
    with pytest.raises(Exception):
        DetectionParams(tile_size=100)  # not a multiple of 32
    with pytest.raises(Exception):
        DetectionParams(tile_size=64, overlap=64)


def test_windows_cover_full_extent_with_overlap():
    windows = list(detector._windows(width=100, height=100, tile=64, overlap=16))
    assert len(windows) >= 4
    covered = np.zeros((100, 100), dtype=bool)
    for w in windows:
        covered[int(w.row_off):int(w.row_off + w.height),
                int(w.col_off):int(w.col_off + w.width)] = True
    assert covered.all()


def test_run_detection_emits_geojson_with_properties(models_dir, tmp_path):
    src = _ortho(tmp_path / "ortho.tif")
    out = tmp_path / "out.geojson"

    result = detector.run_detection(
        {"raster": src},
        DetectionParams(model="detector.onnx", labels="labels.txt", confidence=0.5),
        str(out),
    )

    data = json.loads(out.read_text())
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) >= 1
    feature = data["features"][0]
    assert set(feature["properties"]) == {"class", "class_id", "confidence"}
    assert feature["geometry"]["type"] == "Polygon"
    lon, lat = feature["geometry"]["coordinates"][0][0]
    assert -180 <= lon <= 180 and -90 <= lat <= 90
    assert result["metadata"]["total"] == len(data["features"])


def test_class_filter_restricts_output(models_dir, tmp_path):
    src = _ortho(tmp_path / "o.tif")
    out = tmp_path / "o.geojson"
    detector.run_detection(
        {"raster": src},
        DetectionParams(model="detector.onnx", labels="labels.txt",
                        confidence=0.5, classes=["c0"]),
        str(out),
    )
    data = json.loads(out.read_text())
    assert data["features"]
    assert all(f["properties"]["class"] == "c0" for f in data["features"])


def test_no_detections_yields_empty_collection(models_dir, tmp_path):
    src = _ortho(tmp_path / "o.tif")
    out = tmp_path / "o.geojson"
    result = detector.run_detection(
        {"raster": src},
        DetectionParams(model="detector.onnx", labels="labels.txt", confidence=0.99),
        str(out),
    )
    data = json.loads(out.read_text())
    assert data["features"] == []
    assert result["metadata"]["total"] == 0
    assert result["metadata"]["counts"] == {}


def test_catalog_exposes_detection_operation():
    ops = {o["op_id"]: o for o in asyncio.run(list_analysis())["operations"]}
    op = ops["object-detection"]
    assert op["output_kind"] == "vector"
    assert op["render_kind"] == "detections"
    assert op["inputs"] == [{"name": "raster", "datasets": ["orthophoto"]}]
    assert op["timeout_seconds"] == 1800
    assert op["needs_validation"] is True
    assert "confidence" in op["params_schema"]["properties"]


def test_validate_unknown_op_is_404():
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(validate_analysis("nope", AnalysisValidateRequest(params={})))
    assert excinfo.value.status_code == 404


def test_validate_invalid_params_is_422(models_dir):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(validate_analysis(
            "object-detection", AnalysisValidateRequest(params={"confidence": 5})
        ))
    assert excinfo.value.status_code == 422


def test_validate_missing_model_is_422(models_dir):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(validate_analysis(
            "object-detection", AnalysisValidateRequest(params={"model": "nope.onnx"})
        ))
    assert excinfo.value.status_code == 422
    assert "not found" in str(excinfo.value.detail)


def test_validate_ok_with_valid_model(models_dir):
    result = asyncio.run(validate_analysis(
        "object-detection",
        AnalysisValidateRequest(params={"model": "detector.onnx", "labels": "labels.txt"}),
    ))
    assert result == {"ok": True}
