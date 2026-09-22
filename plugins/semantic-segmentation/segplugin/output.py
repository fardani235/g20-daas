"""Writing results: the class mask GeoTIFF, statistics and polygons.

The mask is a single-band uint8 GeoTIFF of class ids with 255 as nodata, an
embedded colour table (so any GIS — and the platform's tile renderer — shows
class colours) and the class table stored in the file's tags, making the file
self-describing. Polygons are a GeoJSON FeatureCollection in EPSG:4326 with
``class``, ``class_id`` and ``area`` (m²) per feature.
"""

import json

import numpy as np
import rasterio
from rasterio import features
from rasterio.warp import transform_geom
from shapely.geometry import mapping, shape

from .grid import Grid

NODATA = 255


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def write_mask(path: str, labels: np.ndarray, grid: Grid, card, extra_tags: dict | None = None):
    profile = grid.profile(dtype="uint8", nodata=NODATA)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(labels, 1)
        dst.write_colormap(1, {c["id"]: _rgb(c["color"]) + (255,) for c in card.classes})
        dst.set_band_description(1, f"{card.label} class ids")
        tags = {
            "SEGMENTATION_MODEL": card.id,
            "SEGMENTATION_CLASSES": json.dumps(
                [{"id": c["id"], "name": c["name"], "color": c["color"]} for c in card.classes]
            ),
        }
        tags.update(extra_tags or {})
        dst.update_tags(**tags)


def class_stats(labels: np.ndarray, grid: Grid, card) -> dict:
    counts = np.bincount(labels.ravel(), minlength=256)
    valid = int(counts[:NODATA].sum())
    area = grid.pixel_area_m2
    classes = []
    for c in card.classes:
        n = int(counts[c["id"]])
        classes.append({
            "id": c["id"], "name": c["name"], "color": c["color"],
            "pixels": n,
            "area_m2": round(n * area, 2),
            "fraction": round(n / valid, 4) if valid else 0.0,
        })
    return {
        "classes": classes,
        "valid_pixels": valid,
        "nodata_pixels": int(counts[NODATA]),
        "valid_area_m2": round(valid * area, 2),
    }


def vectorize(labels: np.ndarray, grid: Grid, card, *, min_area_m2: float = 0.0,
              simplify_m: float = 0.0, include_background: bool = False) -> dict:
    """Polygons per connected class region as a GeoJSON FeatureCollection (EPSG:4326)."""
    names = {c["id"]: c["name"] for c in card.classes}
    keep = labels != NODATA
    if not include_background:
        for bid in card.background_ids:
            keep &= labels != bid
    mpu = grid.meters_per_unit
    tolerance = simplify_m / mpu if simplify_m > 0 else 0
    feats = []
    if keep.any():
        for geom, value in features.shapes(labels, mask=keep, transform=grid.transform, connectivity=8):
            poly = shape(geom)
            if tolerance > 0:
                poly = poly.simplify(tolerance, preserve_topology=True)
            if poly.is_empty:
                continue
            area = poly.area * mpu * mpu
            if area < min_area_m2:
                continue
            cid = int(value)
            feats.append({
                "type": "Feature",
                "geometry": transform_geom(grid.crs, "EPSG:4326", mapping(poly)),
                "properties": {"class": names.get(cid, str(cid)), "class_id": cid, "area": round(area, 2)},
            })
    return {"type": "FeatureCollection", "features": feats}


def vector_stats(collection: dict) -> dict:
    per_class = {}
    for f in collection["features"]:
        name = f["properties"]["class"]
        entry = per_class.setdefault(name, {"count": 0, "area_m2": 0.0})
        entry["count"] += 1
        entry["area_m2"] += f["properties"]["area"]
    for entry in per_class.values():
        entry["area_m2"] = round(entry["area_m2"], 2)
    return {"feature_count": len(collection["features"]), "per_class": per_class}
