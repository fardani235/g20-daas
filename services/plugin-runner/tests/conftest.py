import json
import os
import shutil
import zipfile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EXAMPLE_PLUGIN_DIR = os.path.join(REPO_ROOT, "docs", "plugins", "examples", "elevation-mask")


def make_package(path, files: dict[str, str], manifest: dict | None = None) -> str:
    """Write a plugin zip with ``files`` (name -> source text) plus a manifest."""
    manifest = manifest if manifest is not None else {
        "id": "test-plugin", "label": "Test", "version": "1.0.0",
        "entrypoint": "main.py", "output_kind": "raster",
        "inputs": [{"name": "raster", "datasets": ["dsm"]}],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        for name, text in files.items():
            zf.writestr(name, text)
    return str(path)


@pytest.fixture
def dem(tmp_path):
    arr = np.full((16, 16), 100.0, dtype="float32")
    arr[4:12, 4:12] = 130.0
    path = tmp_path / "dsm.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=16, width=16, count=1, dtype="float32",
        crs="EPSG:32633", transform=from_origin(500000, 4500000, 2.0, 2.0), nodata=-9999,
    ) as ds:
        ds.write(arr, 1)
    return str(path)


@pytest.fixture
def run_dir(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    return str(d)


@pytest.fixture
def example_package(tmp_path):
    if not os.path.isdir(EXAMPLE_PLUGIN_DIR):
        pytest.skip("example plugin not present")
    return shutil.make_archive(str(tmp_path / "elevation-mask"), "zip", root_dir=EXAMPLE_PLUGIN_DIR)
