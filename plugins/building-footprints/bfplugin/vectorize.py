"""Instances to polygons: outline, simplify, regularise, score, GeoJSON.

Each labelled instance becomes one polygon (its exterior ring only — courtyards
and holes left by the mask are not footprints), simplified with
Douglas-Peucker. Buildings are mostly rectangular while raster outlines are
staircases, so an instance whose area fills at least
``regularize_area_threshold`` of its minimum rotated rectangle is replaced by
that rectangle (a threshold of 1 switches regularisation off) — but only when doing so does not create more than
``regularize_overlap_tol_m2`` of *new* overlap with neighbouring instances
(a rectangle snapped over a terraced neighbour would otherwise swallow it).
The score of a feature is the mean building probability over its pixels.
"""

from dataclasses import dataclass

import numpy as np
from rasterio import features
from rasterio.warp import transform_geom
from shapely import STRtree
from shapely.geometry import Polygon, mapping, shape

from .grid import Grid


@dataclass
class Footprint:
    instance_id: int
    polygon: Polygon        # in the grid CRS
    score: float
    regularized: bool = False

    def area_m2(self, grid: Grid) -> float:
        return self.polygon.area * grid.meters_per_unit ** 2


def instance_scores(labels: np.ndarray, prob_u8: np.ndarray, ids: np.ndarray) -> np.ndarray:
    from scipy import ndimage
    means = ndimage.mean(prob_u8, labels=labels, index=ids)
    return np.asarray(means, dtype="float64") / 255.0


def outlines(labels: np.ndarray, grid: Grid) -> dict[int, Polygon]:
    """Largest exterior polygon per instance id, in the grid CRS."""
    best: dict[int, Polygon] = {}
    mask = labels > 0
    if not mask.any():
        return best
    for geom, value in features.shapes(labels, mask=mask, transform=grid.transform, connectivity=8):
        poly = shape(geom)
        if poly.is_empty:
            continue
        ring = Polygon(poly.exterior.coords)
        iid = int(value)
        if iid not in best or ring.area > best[iid].area:
            best[iid] = ring
    return best


def simplify(poly: Polygon, tolerance: float) -> Polygon:
    if tolerance <= 0:
        return poly
    out = poly.simplify(tolerance, preserve_topology=True)
    if out.is_empty or not out.is_valid or out.geom_type != "Polygon":
        return poly
    return out


def regularize(footprints: list[Footprint], grid: Grid, *, area_threshold: float, overlap_tol_m2: float) -> int:
    """Replace near-rectangular footprints by their minimum rotated rectangle in place.

    Neighbour overlap is measured against the *original* outlines so the
    result does not depend on processing order. Returns the number regularised.
    """
    if not footprints or area_threshold >= 1:
        return 0  # 1 = off: a polygon can only *equal* its rectangle, so nothing would change
    polys = [f.polygon for f in footprints]
    tree = STRtree(polys)
    mpu2 = grid.meters_per_unit ** 2
    count = 0
    for i, f in enumerate(footprints):
        poly = polys[i]
        rect = poly.minimum_rotated_rectangle
        if rect.is_empty or rect.area <= 0 or rect.geom_type != "Polygon":
            continue
        if poly.area / rect.area < area_threshold:
            continue
        added = 0.0
        for j in tree.query(rect):
            j = int(j)
            if j == i:
                continue
            other = polys[j]
            added += max(0.0, rect.intersection(other).area - poly.intersection(other).area)
            if added * mpu2 > overlap_tol_m2:
                break
        if added * mpu2 > overlap_tol_m2:
            continue
        f.polygon = rect
        f.regularized = True
        count += 1
    return count


def build_footprints(labels: np.ndarray, prob_u8: np.ndarray, grid: Grid, *, simplify_m: float,
                     min_area_m2: float, regularize_area_threshold: float,
                     regularize_overlap_tol_m2: float) -> tuple[list[Footprint], dict]:
    """The full vector stage. Returns (footprints, stats)."""
    polys = outlines(labels, grid)
    ids = np.fromiter(sorted(polys), dtype="int64", count=len(polys))
    scores = instance_scores(labels, prob_u8, ids) if len(ids) else np.zeros(0)
    tolerance = simplify_m / grid.meters_per_unit if simplify_m > 0 else 0.0
    mpu2 = grid.meters_per_unit ** 2

    footprints = []
    dropped_small = 0
    for iid, score in zip(ids.tolist(), scores.tolist()):
        poly = simplify(polys[iid], tolerance)
        if poly.is_empty or poly.area * mpu2 < min_area_m2:
            dropped_small += 1
            continue
        footprints.append(Footprint(instance_id=iid, polygon=poly, score=float(score)))

    regularized = regularize(footprints, grid, area_threshold=regularize_area_threshold,
                             overlap_tol_m2=regularize_overlap_tol_m2)
    stats = {
        "polygons": len(polys),
        "dropped_small": dropped_small,
        "regularized": regularized,
        "feature_count": len(footprints),
    }
    return footprints, stats


def feature_collection(footprints: list[Footprint], grid: Grid, class_name: str) -> dict:
    """GeoJSON FeatureCollection in EPSG:4326 with class, score and area (m²)."""
    feats = []
    for f in footprints:
        area = f.area_m2(grid)
        feats.append({
            "type": "Feature",
            "geometry": transform_geom(grid.crs, "EPSG:4326", mapping(f.polygon), precision=7),
            "properties": {
                "class": class_name,
                "score": round(f.score, 3),
                "area": round(area, 2),
                "regularized": f.regularized,
                "instance_id": f.instance_id,
            },
        })
    return {"type": "FeatureCollection", "features": feats}


def collection_stats(collection: dict) -> dict:
    feats = collection["features"]
    areas = [f["properties"]["area"] for f in feats]
    scores = [f["properties"]["score"] for f in feats]
    return {
        "feature_count": len(feats),
        "regularized": sum(1 for f in feats if f["properties"]["regularized"]),
        "total_area_m2": round(sum(areas), 2),
        "median_area_m2": round(float(np.median(areas)), 2) if areas else 0.0,
        "mean_score": round(float(np.mean(scores)), 3) if scores else 0.0,
    }
