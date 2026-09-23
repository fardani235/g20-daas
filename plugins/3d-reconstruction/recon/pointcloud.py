"""LAS/LAZ point clouds: header facts, gridding and subsampling.

Read with ``laspy`` (+ ``lazrs`` for LAZ), chunk by chunk, so a 100M-point
cloud never has to fit in memory. Two uses:

* ``rasterize`` bins points into a ``Grid`` — per cell the max / mean / min
  Z (a DSM-like surface) and the mean RGB — which feeds the terrain
  triangulation when the task has no DSM, or supplies colour when it has no
  orthophoto.
* ``sample`` returns an evenly spaced subset of points with colours for the
  direct point-cloud GLB.

ODM writes 8-bit colour values into the 16-bit LAS fields; ``_rgb8`` detects
that from the observed maximum instead of trusting the format.
"""

import numpy as np

from .crs import crs_from_epsg, crs_from_wkt
from .errors import InputError
from .heightfield import Grid

CHUNK = 1_000_000

# LAS GeoKeyDirectory keys carrying an EPSG code (GeoTIFF spec).
_GEOKEY_PROJECTED = 3072
_GEOKEY_GEOGRAPHIC = 2048

CLASS_NAMES = {
    0: "never classified", 1: "unclassified", 2: "ground", 3: "low vegetation", 4: "medium vegetation",
    5: "high vegetation", 6: "building", 7: "low point", 9: "water", 17: "bridge deck", 64: "ODM: unclassified",
}


def open_las(path: str):
    import laspy

    try:
        return laspy.open(path)
    except Exception as e:  # laspy raises several error types
        raise InputError(f"cannot read point cloud {path}: {e}") from e


def las_crs(header):
    """CRS from the header's WKT VLR or GeoTIFF key directory, else ``None``."""
    for vlr in getattr(header, "vlrs", []):
        name = type(vlr).__name__
        if name == "WktCoordinateSystemVlr":
            crs = crs_from_wkt(getattr(vlr, "string", None))
            if crs is not None:
                return crs
    for vlr in getattr(header, "vlrs", []):
        if type(vlr).__name__ == "GeoKeyDirectoryVlr":
            keys = {k.id: k for k in vlr.geo_keys}
            for key_id in (_GEOKEY_PROJECTED, _GEOKEY_GEOGRAPHIC):
                key = keys.get(key_id)
                if key is not None and key.tiff_tag_location == 0 and 1024 <= key.value_offset <= 32767:
                    crs = crs_from_epsg(key.value_offset)
                    if crs is not None:
                        return crs
    return None


def info(path: str) -> dict:
    with open_las(path) as f:
        h = f.header
        return {
            "count": int(h.point_count),
            "bounds": (float(h.mins[0]), float(h.mins[1]), float(h.maxs[0]), float(h.maxs[1])),
            "z_range": (float(h.mins[2]), float(h.maxs[2])),
            "crs": las_crs(h),
            "has_rgb": "red" in h.point_format.dimension_names,
            "has_classification": "classification" in h.point_format.dimension_names,
            "point_format": int(h.point_format.id),
        }


def parse_classes(text: str) -> set[int] | None:
    text = (text or "").strip()
    if not text:
        return None
    out = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError as e:
            raise InputError(f"point_classes must be comma-separated integers, got {part!r}") from e
    return out or None


def _rgb8(chunk, sixteen_bit: bool | None) -> tuple[np.ndarray | None, bool | None]:
    names = chunk.point_format.dimension_names
    if "red" not in names:
        return None, sixteen_bit
    r, g, b = (np.asarray(chunk[c], dtype=np.uint16) for c in ("red", "green", "blue"))
    if sixteen_bit is None:
        sixteen_bit = bool(max(int(r.max(initial=0)), int(g.max(initial=0)), int(b.max(initial=0))) > 255)
    rgb = np.stack([r, g, b], axis=1)
    if sixteen_bit:
        rgb = (rgb >> 8).astype(np.uint8)
    else:
        rgb = rgb.astype(np.uint8)
    return rgb, sixteen_bit


def _class_mask(chunk, classes: set[int] | None):
    if classes is None or "classification" not in chunk.point_format.dimension_names:
        return None
    cls = np.asarray(chunk.classification)
    return np.isin(cls, list(classes))


def _cell_index(x, y, grid: Grid):
    cols = np.minimum(((x - grid.minx) / grid.res).astype(np.int64), grid.width - 1)
    rows = np.minimum(((grid.maxy - y) / grid.res).astype(np.int64), grid.height - 1)
    return rows * grid.width + cols


def rasterize(path: str, grid: Grid, *, statistic: str = "max", classes: set[int] | None = None,
              want_rgb: bool = True, color_grid: Grid | None = None, on_progress=None) -> dict:
    """Bin the cloud onto ``grid`` in one pass.

    Returns z (masked, on ``grid``), rgb (uint8 HxWx3 on ``color_grid`` or
    ``grid``, ``None`` without RGB) with its ``rgb_valid`` mask, counts, points
    seen/used and the classification codes present.
    """
    if statistic not in ("max", "mean", "min"):
        raise InputError(f"point_statistic must be max, mean or min, got {statistic!r}")
    cgrid = color_grid or grid
    n = grid.cells
    zsum = np.zeros(n, np.float64)
    zext = np.full(n, -np.inf if statistic == "max" else np.inf, np.float64)
    count = np.zeros(n, np.int64)
    rgbsum = np.zeros((cgrid.cells, 3), np.float32) if want_rgb else None
    rgbcount = np.zeros(cgrid.cells, np.int32) if want_rgb else None
    seen_classes = set()
    sixteen_bit = None
    used = total = 0

    with open_las(path) as f:
        total_points = int(f.header.point_count)
        for chunk in f.chunk_iterator(CHUNK):
            total += len(chunk)
            x = np.asarray(chunk.x, dtype=np.float64)
            y = np.asarray(chunk.y, dtype=np.float64)
            z = np.asarray(chunk.z, dtype=np.float64)
            keep = (x >= grid.minx) & (x < grid.maxx) & (y > grid.miny) & (y <= grid.maxy) & np.isfinite(z)
            if "classification" in chunk.point_format.dimension_names:
                seen_classes.update(int(v) for v in np.unique(np.asarray(chunk.classification)))
            cm = _class_mask(chunk, classes)
            if cm is not None:
                keep &= cm
            if not keep.any():
                continue
            xk, yk, zk = x[keep], y[keep], z[keep]
            flat = _cell_index(xk, yk, grid)
            np.add.at(count, flat, 1)
            if statistic == "mean":
                np.add.at(zsum, flat, zk)
            elif statistic == "max":
                np.maximum.at(zext, flat, zk)
            else:
                np.minimum.at(zext, flat, zk)
            if rgbsum is not None:
                rgb, sixteen_bit = _rgb8(chunk, sixteen_bit)
                if rgb is None:
                    rgbsum = rgbcount = None
                else:
                    cflat = flat if cgrid is grid else _cell_index(xk, yk, cgrid)
                    np.add.at(rgbsum, cflat, rgb[keep].astype(np.float32))
                    np.add.at(rgbcount, cflat, 1)
            used += int(keep.sum())
            if on_progress and total_points:
                on_progress(total / total_points)

    if used == 0:
        raise InputError("no points fall inside the model extent" + (" for the selected classes" if classes else ""))
    hit = count > 0
    if statistic == "mean":
        zval = np.where(hit, zsum / np.maximum(count, 1), 0.0)
    else:
        zval = np.where(hit, zext, 0.0)
    z_out = np.ma.MaskedArray(zval.astype(np.float32).reshape(grid.height, grid.width),
                              mask=~hit.reshape(grid.height, grid.width))
    rgb_out = rgb_valid = None
    if rgbsum is not None:
        rgb_valid = (rgbcount > 0).reshape(cgrid.height, cgrid.width)
        np.divide(rgbsum, np.maximum(rgbcount, 1)[:, None], out=rgbsum)
        rgb_out = np.clip(rgbsum + 0.5, 0, 255).astype(np.uint8).reshape(cgrid.height, cgrid.width, 3)
        del rgbsum
    return {
        "z": z_out, "rgb": rgb_out, "rgb_valid": rgb_valid, "count": count.reshape(grid.height, grid.width),
        "points_total": total, "points_used": used, "classes_seen": sorted(seen_classes),
        "rgb_16bit": sixteen_bit,
    }


def sample(path: str, max_points: int, *, classes: set[int] | None = None, on_progress=None) -> dict:
    """An evenly strided subset of at most ``max_points`` points: xyz float64 (N,3), rgb uint8 (N,3) or None."""
    xyz_parts, rgb_parts = [], []
    sixteen_bit = None
    total = used = 0
    with open_las(path) as f:
        total_points = int(f.header.point_count)
        stride = max(1, int(np.ceil(total_points / max(1, max_points))))
        offset = 0
        for chunk in f.chunk_iterator(CHUNK):
            n = len(chunk)
            pick = np.arange((-offset) % stride, n, stride)
            offset = (offset + n) % stride
            total += n
            if pick.size == 0:
                continue
            cm = _class_mask(chunk, classes)
            if cm is not None:
                pick = pick[cm[pick]]
                if pick.size == 0:
                    continue
            xyz = np.column_stack([np.asarray(chunk.x)[pick], np.asarray(chunk.y)[pick], np.asarray(chunk.z)[pick]])
            xyz_parts.append(xyz.astype(np.float64))
            rgb, sixteen_bit = _rgb8(chunk, sixteen_bit)
            if rgb is not None:
                rgb_parts.append(rgb[pick])
            used += pick.size
            if on_progress and total_points:
                on_progress(total / total_points)
    if used == 0:
        raise InputError("the point cloud has no points" + (" in the selected classes" if classes else ""))
    xyz = np.concatenate(xyz_parts, axis=0)
    rgb = np.concatenate(rgb_parts, axis=0) if rgb_parts and len(rgb_parts) == len(xyz_parts) else None
    return {"xyz": xyz, "rgb": rgb, "points_total": total, "points_used": used, "stride": stride}


def describe_classes(classes: list[int]) -> str:
    return ", ".join(f"{c} ({CLASS_NAMES.get(c, 'other')})" for c in classes)
