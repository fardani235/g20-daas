"""Numbers and outlines of the change regions.

Per region: area, mean and extreme height change, volume and the volume's
uncertainty. Site totals: cut, fill, net, changed area and its share of the
area both surveys cover.

**Volume** is the sum of the offset-corrected height difference over the
region's cells times the cell area — nothing is interpolated or modelled.

**Volume uncertainty** (one standard deviation) has three parts, added in
quadrature:

* *random*: cell noise does not average out as fast as ``1/sqrt(n)`` because
  neighbouring cells share their error; ``cells_per_sample`` (measured in
  ``align.block_noise``) is how many cells make one independent sample;
* *systematic*: whatever is left of the height offset after alignment shifts
  every cell of a region the same way, so it scales with the *area* and does
  not average out at all. Across regions it adds linearly, which is why the
  site totals are less certain than the regions suggest individually;
* *edge*: a region ends where the change drops below the level of detection,
  not where the change ends. The height difference summed over a ring just
  outside the region (``EDGE_RING_M`` wide) measures what was left out — a
  stockpile's shallow toe, the feathered edge of a fill. It is not added to
  the volume (the ring also holds whatever else lies next to the region) but
  it is counted as uncertainty when it points the same way as the region.
"""

import math

import numpy as np
from rasterio import features
from rasterio.warp import transform_geom
from scipy import ndimage
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from .detect import APPEARANCE, CLASS_NAMES, CUT, FILL, VEGETATION

# The map draws at most 5000 features; keep well under it and say so.
MAX_FEATURES = 2000

# Width of the ring around a region in which sub-threshold change is measured.
EDGE_RING_M = 5.0
EDGE_RING_MAX_CELLS = 30

CLASS_COLOURS = {"fill": "#2563eb", "cut": "#dc2626", "vegetation": "#16a34a", "appearance": "#d97706"}
CLASS_LABELS = {
    "fill": "Fill (ground raised)",
    "cut": "Cut (ground lowered)",
    "vegetation": "Vegetation height change (not counted)",
    "appearance": "Looks different, same height",
}


def random_volume_sigma(cells: int, cell_area: float, sigma: float, cells_per_sample: float) -> float:
    """Standard deviation of a sum over ``cells`` correlated cells, as a volume."""
    return cell_area * sigma * math.sqrt(cells * min(float(cells), max(1.0, cells_per_sample)))


def edge_volumes(labels: np.ndarray, dz: np.ndarray, n: int, ring_cells: int) -> np.ndarray:
    """Per region id: the height difference summed over the ring of cells within
    ``ring_cells`` outside it (cells of other regions and cells without data excluded)."""
    if ring_cells <= 0:
        return np.zeros(n)
    # Each ring cell is credited to one neighbouring region (the highest id in reach).
    nearest = ndimage.maximum_filter(labels, size=2 * ring_cells + 1, mode="constant", cval=0)
    ring = (labels == 0) & (nearest > 0) & np.isfinite(dz)
    return np.bincount(nearest[ring], weights=dz[ring].astype("float64"), minlength=n)


def measure_regions(labels: np.ndarray, regions: list[dict], dz: np.ndarray | None, cell_area: float,
                    sigma_noise: float, sigma_sys: float, cells_per_sample: float,
                    ring_cells: int = 0) -> list[dict]:
    """Add area, height statistics, volume and volume uncertainty to each region record."""
    if not regions:
        return regions
    n = max(r["id"] for r in regions) + 1
    if dz is not None:
        flat = labels.ravel()
        values = np.where(flat > 0, np.nan_to_num(dz.ravel(), nan=0.0), 0.0)
        sums = np.bincount(flat, weights=values, minlength=n)
        ids = np.array([r["id"] for r in regions])
        highs = ndimage.maximum(dz, labels, index=ids)
        lows = ndimage.minimum(dz, labels, index=ids)
        rings = edge_volumes(labels, dz, n, ring_cells)
    for i, r in enumerate(regions):
        cells = r["cells"]
        r["class_name"] = CLASS_NAMES[r["class"]]
        r["area_m2"] = cells * cell_area
        if dz is None or r["class"] == APPEARANCE:
            continue
        total = float(sums[r["id"]])
        r["mean_change_m"] = total / cells
        r["max_change_m"] = float(highs[i]) if total >= 0 else float(lows[i])
        r["volume_m3"] = abs(total) * cell_area
        r["net_volume_m3"] = total * cell_area
        # Sub-threshold change around the region, when it continues the region's own direction.
        ring = float(rings[r["id"]]) * cell_area
        r["edge_volume_m3"] = max(0.0, ring if total >= 0 else -ring)
        r["volume_sigma_random_m3"] = random_volume_sigma(cells, cell_area, sigma_noise, cells_per_sample)
        r["volume_sigma_m3"] = math.sqrt(r["volume_sigma_random_m3"] ** 2 + (r["area_m2"] * sigma_sys) ** 2
                                         + r["edge_volume_m3"] ** 2)
    return regions


def totals(regions: list[dict], valid_cells: int, cell_area: float, sigma_sys: float) -> dict:
    """Site totals with uncertainties. Cut and fill are earthwork classes only."""
    def group(code):
        rs = [r for r in regions if r["class"] == code]
        area = sum(r["area_m2"] for r in rs)
        volume = sum(r.get("volume_m3", 0.0) for r in rs)
        net = sum(r.get("net_volume_m3", 0.0) for r in rs)
        random = math.sqrt(sum(r.get("volume_sigma_random_m3", 0.0) ** 2 for r in rs))
        # Like the systematic term, what the rims leave out errs the same way
        # for every region of a class, so it adds up rather than averaging out.
        edge = sum(r.get("edge_volume_m3", 0.0) for r in rs)
        return {"regions": len(rs), "area_m2": area, "volume_m3": volume, "net_m3": net,
                "random_m3": random, "edge_m3": edge,
                "sigma_m3": math.sqrt(random ** 2 + (area * sigma_sys) ** 2 + edge ** 2)}

    fill, cut, veg, app = group(FILL), group(CUT), group(VEGETATION), group(APPEARANCE)
    compared = valid_cells * cell_area
    changed = fill["area_m2"] + cut["area_m2"]
    # Net = sum of the height difference over every changed cell, so a leftover
    # offset pushes cut and fill the same way: it acts on the combined area.
    net_sigma = math.sqrt(fill["random_m3"] ** 2 + cut["random_m3"] ** 2 + (changed * sigma_sys) ** 2
                          + fill["edge_m3"] ** 2 + cut["edge_m3"] ** 2)
    share = (changed / compared) if compared > 0 else 0.0
    return {
        "compared_area_m2": compared,
        "changed_area_m2": changed,
        "changed_share": share,
        "changed_percent": 100.0 * share,
        "fill_volume_m3": fill["net_m3"],
        "fill_volume_sigma_m3": fill["sigma_m3"],
        "fill_below_detection_m3": fill["edge_m3"],
        "fill_area_m2": fill["area_m2"],
        "fill_regions": fill["regions"],
        "cut_volume_m3": -cut["net_m3"],
        "cut_volume_sigma_m3": cut["sigma_m3"],
        "cut_below_detection_m3": cut["edge_m3"],
        "cut_area_m2": cut["area_m2"],
        "cut_regions": cut["regions"],
        "net_volume_m3": fill["net_m3"] + cut["net_m3"],
        "net_volume_sigma_m3": net_sigma,
        "vegetation_area_m2": veg["area_m2"],
        "vegetation_regions": veg["regions"],
        "appearance_area_m2": app["area_m2"],
        "appearance_regions": app["regions"],
        "appearance_share": (app["area_m2"] / compared) if compared > 0 else 0.0,
    }


def _rank(r: dict) -> tuple:
    # Earthwork first (largest volume first), then the informational classes by area.
    return (0 if r["class"] in (FILL, CUT) else 1, -r.get("volume_m3", 0.0), -r["area_m2"])


def feature_collection(labels: np.ndarray, regions: list[dict], grid, simplify_cells: float = 0.75,
                       max_features: int = MAX_FEATURES) -> tuple[dict, dict]:
    """Region outlines as a GeoJSON FeatureCollection in EPSG:4326, plus what was left out."""
    ordered = sorted(regions, key=_rank)
    kept = ordered[:max_features]
    info = {"feature_count": len(kept), "regions": len(regions), "omitted_small_regions": len(ordered) - len(kept)}
    if not kept:
        return {"type": "FeatureCollection", "features": []}, info

    lookup = np.zeros(max(r["id"] for r in regions) + 1, dtype=bool)
    lookup[[r["id"] for r in kept]] = True
    mask = lookup[labels]
    parts: dict[int, list] = {}
    for geom, value in features.shapes(labels, mask=mask, connectivity=8, transform=grid.transform):
        parts.setdefault(int(value), []).append(shape(geom))

    tolerance = simplify_cells * grid.resolution
    out = []
    for r in kept:
        polys = parts.get(r["id"])
        if not polys:
            continue
        geom = unary_union([p if p.is_valid else p.buffer(0) for p in polys])
        if tolerance > 0:
            simple = geom.simplify(tolerance, preserve_topology=True)
            geom = simple if (simple.is_valid and not simple.is_empty) else geom
        if geom.is_empty:
            continue
        gj = transform_geom(grid.crs, "EPSG:4326", mapping(geom), precision=7)
        out.append({"type": "Feature", "geometry": gj, "properties": _properties(r)})
    info["feature_count"] = len(out)
    return {"type": "FeatureCollection", "features": out}, info


def _properties(r: dict) -> dict:
    name = r["class_name"]
    props = {
        "id": r["id"],
        "class": name,
        "label": CLASS_LABELS[name],
        "color": CLASS_COLOURS[name],
        "area": round(r["area_m2"], 2),
    }
    if "volume_m3" in r:
        props.update({
            "volume_m3": round(r["volume_m3"], 2),
            "volume_uncertainty_m3": round(r["volume_sigma_m3"], 2),
            "below_detection_m3": round(r["edge_volume_m3"], 2),
            "mean_change_m": round(r["mean_change_m"], 3),
            "max_change_m": round(r["max_change_m"], 3),
        })
    return props


def legend() -> list[dict]:
    return [{"name": k, "label": CLASS_LABELS[k], "color": CLASS_COLOURS[k]} for k in CLASS_COLOURS]
