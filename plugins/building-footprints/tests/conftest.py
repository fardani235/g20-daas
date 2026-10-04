"""Synthetic fixtures: a small georeferenced scene whose pixels encode the
answer the fixture model reads back, plus model cards pointing at the tiny
deterministic ONNX graphs in ``tests/fixtures``.

Scene (UTM 33N, 0.2 m, 80 x 60 m): five "buildings" drawn into the red channel
(mask), each with its own normalised inner distance in the green channel (so
touching buildings have two distance maxima and a dip at the shared wall), a
constant blue channel and an alpha band with a 2 m nodata strip on the left.

- A  12 x 10 m rectangle            } A and B share a wall: one blob,
- B   8 x  8 m rectangle, touching A } two buildings after the watershed
- C  L-shaped (16 x 16 minus 8 x 8)   not near-rectangular → never regularised
- D  1 x 1 m speck                    below min_area_m2 → dropped
- E  10 x 6 m rectangle rotated 30°   regularised to its rotated rectangle
"""

import json
import os
import shutil
import sys

import numpy as np
import pytest
import rasterio
from rasterio import features
from rasterio.transform import from_origin
from shapely import affinity
from shapely.geometry import box

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

FIXTURES = os.path.join(PLUGIN_DIR, "tests", "fixtures")
SHIPPED_MODELS = os.path.join(PLUGIN_DIR, "models")

ORIGIN = (500000.0, 4500000.0)
CRS = "EPSG:32633"
RES = 0.2
SIZE_M = (80.0, 60.0)  # width, height

# Buildings in scene metres (x right, y down from the top-left corner).
BUILDINGS = {
    "A": box(10, 10, 22, 20),
    "B": box(22, 10, 30, 18),
    "C": box(40, 30, 56, 46).difference(box(48, 38, 56, 46)),
    "D": box(70, 50, 71, 51),
    "E": affinity.rotate(box(60, 10, 70, 16), 30),
}
AREAS = {k: v.area for k, v in BUILDINGS.items()}


def _to_crs(poly):
    return affinity.affine_transform(poly, [1, 0, 0, -1, ORIGIN[0], ORIGIN[1]])


def write_scene(path, res=RES, alpha=True, nodata_strip_m=2.0):
    from scipy import ndimage
    w, h = int(SIZE_M[0] / res), int(SIZE_M[1] / res)
    transform = from_origin(ORIGIN[0], ORIGIN[1], res, res)
    mask = np.zeros((h, w), dtype=bool)
    dist = np.zeros((h, w), dtype="float32")
    for poly in BUILDINGS.values():
        m = features.rasterize([(_to_crs(poly), 1)], out_shape=(h, w), transform=transform, dtype="uint8").astype(bool)
        d = ndimage.distance_transform_edt(m)
        if d.max() > 0:
            dist[m] = (d[m] / d.max())
        mask |= m
    outside = ndimage.distance_transform_edt(~mask)
    dist[~mask] = -np.minimum(outside[~mask] / (5.0 / res), 1.0)

    red = (mask * 255).astype("uint8")
    green = np.rint(255 * (0.5 + 0.5 * dist)).clip(0, 255).astype("uint8")
    blue = np.full((h, w), 128, dtype="uint8")
    bands = [red, green, blue]
    if alpha:
        a = np.full((h, w), 255, dtype="uint8")
        a[:, :int(nodata_strip_m / res)] = 0
        bands.append(a)
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=len(bands), dtype="uint8",
                       crs=CRS, transform=transform) as ds:
        for i, b in enumerate(bands, 1):
            ds.write(b, i)
    return str(path)


def write_ungeoreferenced(path, width=64, height=64):
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width, count=3, dtype="uint8") as ds:
        ds.write(np.zeros((3, height, width), dtype="uint8"))
    return str(path)


def _card(cid, file, **extra):
    card = {
        "id": cid, "label": f"Fixture {cid}", "file": file, "default": False,
        "class": {"name": "building", "color": "#d7301f"},
        "onnx": {
            "input": "image", "output": "logits", "input_size": 256,
            "channels": ["red", "green", "blue"], "scale": "unit", "mean": None, "std": None,
            "outputs": {
                "mask": {"channel": 0, "activation": "sigmoid"},
                "boundary": {"channel": 1, "activation": "sigmoid"},
                "distance": {"channel": 2, "activation": "tanh"},
            },
        },
        "recommended": {
            "resolution_m": 0.2, "tile_size": 256, "overlap": 64, "confidence": 0.5, "seed_min_distance": 5,
            "large_blob_area_px": 2000, "h_maxima_depth": 0.1, "min_area_m2": 4.0, "simplify_m": 0.3,
            "regularize_area_threshold": 0.85, "regularize_overlap_tol_m2": 1.0,
        },
        "source": {"name": "test fixture", "license": "MIT"},
    }
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(card.get(key), dict):
            card[key] = {**card[key], **value}
        else:
            card[key] = value
    return card


CARDS = {
    "tiny": _card("tiny", "tiny_footprints.onnx", default=True),
    "badshape": _card("badshape", "tiny_footprints_badshape.onnx"),
    "twoch": _card("twoch", "tiny_footprints_2ch.onnx"),
    "missing": _card("missing", "not-fetched.onnx"),
    "badsum": _card("badsum", "tiny_footprints.onnx", sha256="0" * 64),
    "wrongsize": _card("wrongsize", "tiny_footprints.onnx", onnx={"input_size": 512}),
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
def use_fixture_models(models_dir, monkeypatch):
    """Point ``main.py`` runs at the fixture cards instead of the shipped ones."""
    import bfplugin.pipeline as pipeline
    monkeypatch.setattr(pipeline, "MODELS_DIR", models_dir)
    return models_dir


@pytest.fixture
def scene(tmp_path):
    return write_scene(tmp_path / "ortho.tif")


@pytest.fixture
def make_request(tmp_path):
    def _make(ortho_path, params=None):
        req = {
            "inputs": {"orthophoto": ortho_path} if ortho_path else {},
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
