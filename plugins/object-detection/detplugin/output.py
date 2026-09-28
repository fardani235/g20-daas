"""GeoJSON output: one bounding-box polygon per detection, in EPSG:4326."""

import json

import rasterio
from rasterio.warp import transform_geom

OUTPUT_CRS = "EPSG:4326"


def box_polygon(transform, src_crs, x1, y1, x2, y2) -> dict:
    """Raster-pixel bounding box -> GeoJSON polygon in EPSG:4326."""
    xs, ys = rasterio.transform.xy(transform, [y1, y1, y2, y2, y1], [x1, x2, x2, x1, x1], offset="ul")
    ring = [[float(x), float(y)] for x, y in zip(xs, ys)]
    geom = {"type": "Polygon", "coordinates": [ring]}
    if src_crs.to_string() == OUTPUT_CRS:
        return geom
    return transform_geom(src_crs, OUTPUT_CRS, geom)


def build_collection(detections: list[dict], card, ds) -> tuple[dict, dict]:
    """``(FeatureCollection, per-class counts)`` for merged raster-pixel detections."""
    features = []
    counts: dict[str, int] = {}
    for det in detections:
        label = card.class_name(det["class_id"])
        features.append({
            "type": "Feature",
            "geometry": box_polygon(ds.transform, ds.crs, det["x1"], det["y1"], det["x2"], det["y2"]),
            "properties": {
                "class": label,
                "class_id": det["class_id"],
                "confidence": round(det["confidence"], 4),
                "area_m2": det.get("area_m2"),
            },
        })
        counts[label] = counts.get(label, 0) + 1
    return {"type": "FeatureCollection", "features": features}, counts


def write_collection(path: str, collection: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(collection, f)
