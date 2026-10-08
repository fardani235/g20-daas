"""Writers for the two packages built from this plugin.

The extension of ``output_path`` decides: ``.geojson`` (the default package)
gets the change regions as polygons with their numbers; ``.tif`` (the raster
package) gets the elevation difference itself, for use in GIS.
"""

import json
import os

import numpy as np
import rasterio

NODATA = -9999.0


def kind_for(output_path: str) -> str:
    return "raster" if os.path.splitext(output_path)[1].lower() in (".tif", ".tiff") else "vector"


def write_geojson(path: str, collection: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(collection, f, separators=(",", ":"))


def write_difference(path: str, dz: np.ndarray, keep: np.ndarray, grid, tags: dict):
    """Height difference (after − before, metres) as a float32 GeoTIFF; nodata outside ``keep``."""
    data = np.where(keep & np.isfinite(dz), dz, np.float32(NODATA)).astype("float32")
    profile = {
        "driver": "GTiff", "dtype": "float32", "count": 1, "nodata": NODATA,
        "width": grid.width, "height": grid.height, "crs": grid.crs, "transform": grid.transform,
        "tiled": True, "blockxsize": 256, "blockysize": 256, "compress": "deflate", "predictor": 3,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
        dst.set_band_description(1, "elevation change, after minus before (m)")
        dst.update_tags(**{k: str(v) for k, v in tags.items()})
