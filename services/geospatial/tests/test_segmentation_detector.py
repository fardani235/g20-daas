import asyncio
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi import HTTPException
from rasterio.transform import from_origin
from rasterio.windows import Window
from scipy import ndimage

from app.analysis.segmentation import detector, models
from app.analysis.segmentation.detector import SegmentationParams
from app.routers.analysis import (
    AnalysisValidateRequest,
    list_analysis,
    validate_analysis,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    d = tmp_path / "models"
    d.mkdir()
    monkeypatch.setenv(models.MODELS_DIR_ENV, str(d))
    shutil.copy(FIXTURES / "tiny_segmenter.onnx", d / "segmenter.onnx")
    (d / "labels.txt").write_text("background\nbuilding\n")
    return d


def _spec(mode="multiclass", foreground_index=0, num_classes=2):
    return models.SegmentationSpec(
        input_name="images",
        output_name="masks",
        input_size=(64, 64),
        has_batch_dim=True,
        mode=mode,
        num_classes=num_classes,
        foreground_index=foreground_index,
    )


def _ortho(path, width=64, height=64, crs="EPSG:32615", res=1.0):
    arr = np.zeros((3, height, width), dtype="uint8")
    arr[0] = 100
    arr[1] = 120
    arr[2] = 140
    transform = from_origin(500000, 4500000, res, res)
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=3,
        dtype="uint8", crs=crs, transform=transform,
    ) as ds:
        ds.write(arr)
    return str(path)


def test_params_defaults_and_validation():
    params = SegmentationParams()
    assert params.threshold == 0.5
    assert params.tile_size == 512
    assert params.overlap == 64
    assert params.min_segment_area == 64
    with pytest.raises(Exception):
        SegmentationParams(threshold=1.5)
    with pytest.raises(Exception):
        SegmentationParams(tile_size=100)  # not a multiple of 32
    with pytest.raises(Exception):
        SegmentationParams(tile_size=64, overlap=64)


def test_windows_cover_full_extent_with_overlap():
    windows = list(detector._windows(width=100, height=100, tile=64, overlap=16))
    assert len(windows) >= 4
    covered = np.zeros((100, 100), dtype=bool)
    for w in windows:
        covered[int(w.row_off):int(w.row_off + w.height),
                int(w.col_off):int(w.col_off + w.width)] = True
    assert covered.all()


def test_resolve_tiling_uses_ground_size():
    class _DS:
        res = (0.05, 0.05)

    tile, overlap = detector._resolve_tiling(
        _DS(), SegmentationParams(tile_size_m=32.0, overlap_m=6.4)
    )
    assert tile == 640  # 32 m / 0.05 m
    assert overlap == 128  # 6.4 m / 0.05 m


def test_decode_multiclass_argmax_and_threshold():
    raw = np.zeros((1, 2, 4, 4), dtype=np.float32)
    raw[0, 0] = 0.9
    raw[0, 1] = 0.1
    raw[0, 0, 1:3, 1:3] = 0.1
    raw[0, 1, 1:3, 1:3] = 0.9

    classes, confidence = detector.decode_mask(raw, _spec(), ["a", "b"], 0.5)
    assert (classes[1:3, 1:3] == 1).all()
    assert (classes[0, 0] == 0)
    assert confidence[1, 1] == pytest.approx(0.9)

    # A threshold above every score leaves nothing classified.
    classes, confidence = detector.decode_mask(raw, _spec(), ["a", "b"], 0.95)
    assert (classes == detector._UNCLASSIFIED).all()
    assert (confidence == 0.0).all()


def test_decode_binary_uses_foreground_index():
    raw = np.zeros((1, 1, 4, 4), dtype=np.float32)
    raw[0, 0, 1:3, 1:3] = 0.9
    raw[0, 0, 0, 0] = 0.1

    classes, _ = detector.decode_mask(
        raw, _spec(mode="binary", foreground_index=1, num_classes=1), ["bg", "obj"], 0.5
    )
    assert (classes[1:3, 1:3] == 1).all()
    assert classes[0, 0] == detector._UNCLASSIFIED


def test_stitch_prefers_higher_confidence():
    class_map = np.full((2, 6), detector._UNCLASSIFIED, dtype=np.int16)
    confidence = np.zeros((2, 6), dtype=np.float32)

    detector._stitch(
        class_map, confidence,
        np.full((2, 4), 1, dtype=np.int16), np.full((2, 4), 0.6, dtype=np.float32),
        Window(0, 0, 4, 2),
    )
    detector._stitch(
        class_map, confidence,
        np.full((2, 4), 2, dtype=np.int16), np.full((2, 4), 0.9, dtype=np.float32),
        Window(2, 0, 4, 2),
    )

    assert (class_map[:, 0:2] == 1).all()  # only the first tile covers these
    assert (class_map[:, 2:4] == 2).all()  # overlap: higher confidence wins
    assert (class_map[:, 4:6] == 2).all()


def test_region_across_tile_seam_is_single_and_contiguous():
    width = 100
    class_map = np.full((1, width), detector._UNCLASSIFIED, dtype=np.int16)
    confidence = np.zeros((1, width), dtype=np.float32)

    windows = list(detector._windows(width, 1, tile=64, overlap=32))
    assert len(windows) >= 3

    # A class-1 region that spans the first three overlapping tiles.
    for window, (start, end), conf in (
        (windows[0], (40, 64), 0.8),
        (windows[1], (32, 72), 0.7),
        (windows[2], (64, 84), 0.8),
    ):
        col = int(window.col_off)
        tile_classes = np.full((1, int(window.width)), detector._UNCLASSIFIED, dtype=np.int16)
        tile_confidence = np.zeros((1, int(window.width)), dtype=np.float32)
        tile_classes[0, start - col:end - col] = 1
        tile_confidence[0, start - col:end - col] = conf
        detector._stitch(class_map, confidence, tile_classes, tile_confidence, window)

    _, components = ndimage.label(class_map == 1)
    assert components == 1  # emitted once, not split per tile
    assert (class_map[0, 32:84] == 1).all()


def test_clean_mask_removes_speckle_but_keeps_region():
    class_map = np.zeros((12, 12), dtype=np.int16)
    class_map[2, 2] = 1  # isolated speckle
    class_map[6:11, 6:11] = 1  # 5x5 region
    confidence = np.ones((12, 12), dtype=np.float32)

    cleaned = detector.clean_mask(
        class_map, confidence, ["class0", "class1"], SegmentationParams()
    )

    assert cleaned[2, 2] != 1  # speckle gone
    assert cleaned[8, 8] == 1  # region survives


def test_emit_classes_excludes_background_and_filters():
    labels = ["background", "building", "road"]
    assert detector.emit_classes(labels, SegmentationParams()) == [1, 2]
    assert detector.emit_classes(labels, SegmentationParams(classes=["road"])) == [2]
    assert detector.emit_classes(labels, SegmentationParams(classes=["none"])) == []


def test_run_segmentation_emits_geojson_with_properties(models_dir, tmp_path):
    src = _ortho(tmp_path / "ortho.tif")
    out = tmp_path / "out.geojson"

    result = detector.run_segmentation(
        {"raster": src},
        SegmentationParams(
            model="segmenter.onnx", labels="labels.txt",
            tile_size=64, overlap=0, threshold=0.5, min_segment_area=64,
        ),
        str(out),
    )

    data = json.loads(out.read_text())
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 1

    feature = data["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    assert set(feature["properties"]) == {"class", "class_id", "area", "confidence"}
    assert feature["properties"]["class"] == "building"
    assert feature["properties"]["class_id"] == 1
    assert feature["properties"]["confidence"] == pytest.approx(0.9)
    assert feature["properties"]["area"] == pytest.approx(1024.0)  # 32x32 px at 1 m

    lon, lat = feature["geometry"]["coordinates"][0][0]
    assert -180 <= lon <= 180 and -90 <= lat <= 90

    metadata = result["metadata"]
    assert metadata["mode"] == "multiclass"
    assert metadata["total"] == 1
    assert metadata["counts"] == {"building": 1}
    assert metadata["areas"] == {"building": pytest.approx(1024.0)}
    assert metadata["area_unit"] == "m2"


def test_run_segmentation_no_regions_yields_empty(models_dir, tmp_path):
    src = _ortho(tmp_path / "ortho.tif")
    out = tmp_path / "out.geojson"

    result = detector.run_segmentation(
        {"raster": src},
        SegmentationParams(
            model="segmenter.onnx", labels="labels.txt",
            tile_size=64, overlap=0, threshold=1.0,
        ),
        str(out),
    )

    data = json.loads(out.read_text())
    assert data["features"] == []
    assert result["metadata"]["total"] == 0
    assert result["metadata"]["counts"] == {}


def test_run_segmentation_covers_full_extent(models_dir, tmp_path):
    src = _ortho(tmp_path / "ortho.tif", width=128, height=128)
    out = tmp_path / "out.geojson"

    result = detector.run_segmentation(
        {"raster": src},
        SegmentationParams(
            model="segmenter.onnx", labels="labels.txt",
            tile_size=64, overlap=0, threshold=0.5, min_segment_area=64,
        ),
        str(out),
    )

    # Every tile contributed a region, not only the first.
    assert result["metadata"]["total"] == 4
    assert result["metadata"]["counts"] == {"building": 4}


def test_run_segmentation_drops_small_regions(models_dir, tmp_path):
    src = _ortho(tmp_path / "ortho.tif")
    out = tmp_path / "out.geojson"

    # The 32x32 region is 1024 px; require more than that to keep it.
    result = detector.run_segmentation(
        {"raster": src},
        SegmentationParams(
            model="segmenter.onnx", labels="labels.txt",
            tile_size=64, overlap=0, min_segment_area=2048,
        ),
        str(out),
    )

    assert result["metadata"]["total"] == 0


def test_run_segmentation_simplify_keeps_valid_geometry(models_dir, tmp_path):
    src = _ortho(tmp_path / "ortho.tif")
    out = tmp_path / "out.geojson"

    result = detector.run_segmentation(
        {"raster": src},
        SegmentationParams(
            model="segmenter.onnx", labels="labels.txt",
            tile_size=64, overlap=0, simplify_tolerance=3.0,
        ),
        str(out),
    )

    data = json.loads(out.read_text())
    assert result["metadata"]["total"] == 1
    assert data["features"][0]["geometry"]["type"] == "Polygon"
    lon, lat = data["features"][0]["geometry"]["coordinates"][0][0]
    assert -180 <= lon <= 180 and -90 <= lat <= 90


def test_run_segmentation_class_filter(models_dir, tmp_path):
    src = _ortho(tmp_path / "ortho.tif")

    kept = detector.run_segmentation(
        {"raster": src},
        SegmentationParams(
            model="segmenter.onnx", labels="labels.txt",
            tile_size=64, overlap=0, classes=["building"],
        ),
        str(tmp_path / "kept.geojson"),
    )
    assert kept["metadata"]["counts"] == {"building": 1}

    removed = detector.run_segmentation(
        {"raster": src},
        SegmentationParams(
            model="segmenter.onnx", labels="labels.txt",
            tile_size=64, overlap=0, classes=["none"],
        ),
        str(tmp_path / "removed.geojson"),
    )
    assert removed["metadata"]["total"] == 0


def test_catalog_exposes_segmentation_operation():
    ops = {o["op_id"]: o for o in asyncio.run(list_analysis())["operations"]}
    op = ops["semantic-segmentation"]
    assert op["output_kind"] == "vector"
    assert op["render_kind"] == "segmentation"
    assert op["inputs"] == [{"name": "raster", "datasets": ["orthophoto"]}]
    assert op["timeout_seconds"] == 1800
    assert op["needs_validation"] is True
    assert "threshold" in op["params_schema"]["properties"]
    assert op["models"]  # a curated list is offered to the UI


def test_validate_segmentation_endpoints(models_dir):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(validate_analysis("semantic-segmentation", AnalysisValidateRequest(params={"threshold": 5})))
    assert excinfo.value.status_code == 422

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(validate_analysis(
            "semantic-segmentation", AnalysisValidateRequest(params={"model": "nope.onnx"})
        ))
    assert excinfo.value.status_code == 422
    assert "not found" in str(excinfo.value.detail)

    result = asyncio.run(validate_analysis(
        "semantic-segmentation",
        AnalysisValidateRequest(params={"model": "segmenter.onnx", "labels": "labels.txt"}),
    ))
    assert result == {"ok": True}
