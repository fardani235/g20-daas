"""Synthetic fixtures: a small georeferenced orthophoto and model cards that
point at the tiny deterministic ONNX detectors in ``tests/fixtures``."""

import json
import os
import shutil
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

FIXTURES = os.path.join(PLUGIN_DIR, "tests", "fixtures")
SHIPPED_MODELS = os.path.join(PLUGIN_DIR, "models")

ORIGIN = (500000.0, 4500000.0)
CRS = "EPSG:32633"


def write_orthophoto(path, width=256, height=256, res=0.1, crs=CRS, alpha=False,
                     transparent_right_of=None, dtype="uint8"):
    """RGB (optionally RGBA) GeoTIFF; ``transparent_right_of`` (px) blanks the alpha east of it."""
    rgb = np.zeros((3, height, width), dtype=dtype)
    rgb[0], rgb[1], rgb[2] = 100, 120, 140
    bands = [rgb[0], rgb[1], rgb[2]]
    if alpha:
        a = np.full((height, width), 255, dtype=dtype)
        if transparent_right_of is not None:
            a[:, transparent_right_of:] = 0
        bands.append(a)
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=len(bands), dtype=dtype,
        crs=crs, transform=from_origin(ORIGIN[0], ORIGIN[1], res, res),
    ) as ds:
        for i, b in enumerate(bands, 1):
            ds.write(b, i)
    return str(path)


def write_ungeoreferenced(path, width=64, height=64):
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width, count=3, dtype="uint8") as ds:
        ds.write(np.zeros((3, height, width), dtype="uint8"))
    return str(path)


CARDS = {
    "yolo80": {
        "id": "yolo80", "label": "Tiny YOLO (80 classes)", "file": "tiny_yolo.onnx", "family": "auto",
        "classes": [f"c{i}" for i in range(80)], "default": True,
        "recommended": {"confidence": 0.25, "iou": 0.45, "tile_size": 640, "overlap": 64},
        "colors": {"c0": "#ff0000"},
        "source": {"name": "test fixture", "license": "MIT"},
    },
    "silent": {
        "id": "silent", "label": "Detects nothing", "file": "tiny_yolo_silent.onnx", "family": "yolo",
        "classes": [f"c{i}" for i in range(80)],
    },
    "tv-tree": {
        "id": "tv-tree", "label": "Tiny torchvision", "file": "tiny_torchvision.onnx", "family": "torchvision",
        "classes": ["tree"], "label_offset": 0,
        "recommended": {"confidence": 0.5, "tile_size": 256, "overlap": 0},
    },
    "tv-1based": {
        "id": "tv-1based", "label": "Tiny torchvision, 1-based labels", "file": "tiny_torchvision_1based.onnx",
        "family": "auto", "classes": ["tree"], "label_offset": 1,
        "recommended": {"confidence": 0.3, "tile_size": 256, "overlap": 0},
    },
    "sixcls": {
        "id": "sixcls", "label": "Card lists 80 classes, model has 6", "file": "tiny_yolo_6cls.onnx",
        "family": "yolo", "classes": [f"c{i}" for i in range(80)],
    },
    "badrank": {
        "id": "badrank", "label": "Not a detector", "file": "tiny_badrank.onnx", "family": "auto",
        "classes": ["x"],
    },
    "wrongfamily": {
        "id": "wrongfamily", "label": "Declared torchvision, is YOLO", "file": "tiny_yolo.onnx",
        "family": "torchvision", "classes": [f"c{i}" for i in range(80)],
    },
    "missing": {
        "id": "missing", "label": "Weights not packaged", "file": "not-fetched.onnx", "family": "yolo",
        "classes": ["a"],
    },
    "badsum": {
        "id": "badsum", "label": "Wrong checksum", "file": "tiny_yolo.onnx", "family": "yolo",
        "classes": [f"c{i}" for i in range(80)], "sha256": "0" * 64,
    },
}


def install_models(models_dir, ids=None):
    """Write the requested test cards (default: all) and copy their fixture files."""
    os.makedirs(models_dir, exist_ok=True)
    for cid in ids or CARDS:
        card = CARDS[cid]
        with open(os.path.join(models_dir, f"{cid}.json"), "w", encoding="utf-8") as f:
            json.dump(card, f)
        src = os.path.join(FIXTURES, card["file"])
        if os.path.isfile(src):
            shutil.copy(src, os.path.join(models_dir, card["file"]))
    return str(models_dir)


@pytest.fixture
def models_dir(tmp_path):
    return install_models(tmp_path / "models")


@pytest.fixture
def ortho(tmp_path):
    return write_orthophoto(tmp_path / "ortho.tif")


@pytest.fixture
def make_request(tmp_path):
    def _make(ortho_path, params=None):
        req = {
            "inputs": {"orthophoto": ortho_path},
            "params": params or {},
            "output_path": str(tmp_path / "out.geojson"),
            "result_path": str(tmp_path / "result.json"),
            "progress_path": str(tmp_path / "progress.json"),
            "work_dir": str(tmp_path),
        }
        path = tmp_path / "request.json"
        path.write_text(json.dumps(req))
        return req, str(path)
    return _make
