"""Raster metadata for task outputs (orthophoto, DSM, DTM).

Frappe has no GDAL. The geospatial service reads a raster's header
(``GET /raster/metadata``, or inline in the ``/export/cogify`` response) and
this module normalizes that dict into one ``WebODM Raster Metadata`` child row
per dataset on the task, then serves it back to consumers (viewer, tiling,
GeoAI plugins) in array form through ``to_public``.

Storage rules worth knowing:
  - arrays (geotransform, bounds) are split into Float columns so JSON columns
    never hold a list (Frappe rejects list values on save) and rows stay
    queryable; ``to_public`` reassembles them.
  - ``nodata`` is text: Float cannot represent "absent" (Frappe writes 0.0 for
    None) nor NaN, and a float DEM's NaN nodata must round-trip.
  - a failed extraction still writes a row with ``error`` set, so "never
    extracted" and "extraction failed: <why>" are distinguishable.

``record`` is the pipeline entry point and never raises: metadata is derived
data and must not fail a task that has otherwise completed.
"""

import math

import frappe
from frappe.utils import now_datetime

from webodm_core.plugins.geospatial import GeospatialError, fetch_raster_metadata

DOCTYPE = "WebODM Raster Metadata"
PARENTTYPE = "WebODM Task"
PARENTFIELD = "raster_metadata"

# Task fields that hold rasters this module describes.
RASTER_DATASETS = ("orthophoto", "dsm", "dtm")

_LOG_TITLE = "WebODM Raster Metadata"


# --- normalisation ----------------------------------------------------------

def _nodata_text(value) -> str:
    """Service nodata (number | "nan" | "inf" | "-inf" | None) -> stored text."""
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    value = float(value)
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return str(int(value)) if value.is_integer() else repr(value)


def nodata_value(text: str | None):
    """Stored text -> JSON-safe value: finite float, or "nan"/"inf"/"-inf", or None."""
    if not text:
        return None
    text = text.strip().lower()
    if text in ("nan", "inf", "-inf"):
        return text
    try:
        return float(text)
    except ValueError:
        return None


def _join(values) -> str:
    return ",".join(str(v) for v in values) if values else ""


def _split_ints(text: str | None) -> list[int]:
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return out


def _split(text: str | None) -> list[str]:
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def normalize(meta: dict) -> dict:
    """Map the geospatial service's metadata dict to ``WebODM Raster Metadata`` columns.

    Tolerates missing keys (an older service, a partial read): anything absent
    becomes its column's empty value. Arrays are unpacked; a raster without a
    geotransform gets ``has_transform=0`` and zeroed transform/bounds columns.
    """
    meta = meta or {}
    gt = meta.get("geotransform") or None
    px = meta.get("pixel_size") or None
    bounds = meta.get("bounds") or None
    b4326 = meta.get("bounds_4326") or None
    has_transform = bool(gt and len(gt) == 6)

    row = {
        "driver": meta.get("driver") or "",
        "file_size": int(meta.get("file_size") or 0),
        "width": int(meta.get("width") or 0),
        "height": int(meta.get("height") or 0),
        "band_count": int(meta.get("band_count") or 0),
        "dtype": meta.get("dtype") or "",
        "color_interp": _join(meta.get("color_interp") or []),
        "is_georeferenced": 1 if meta.get("is_georeferenced") else 0,
        "has_transform": 1 if has_transform else 0,
        "epsg": int(meta.get("epsg") or 0),
        "crs_units": meta.get("crs_units") or "",
        "crs_wkt": meta.get("crs_wkt") or "",
        "pixel_size_x": float(px[0]) if px else 0.0,
        "pixel_size_y": float(px[1]) if px else 0.0,
        "origin_x": float(gt[0]) if has_transform else 0.0,
        "origin_y": float(gt[3]) if has_transform else 0.0,
        "rotation_x": float(gt[2]) if has_transform else 0.0,
        "rotation_y": float(gt[4]) if has_transform else 0.0,
        "min_x": float(bounds[0]) if bounds else 0.0,
        "min_y": float(bounds[1]) if bounds else 0.0,
        "max_x": float(bounds[2]) if bounds else 0.0,
        "max_y": float(bounds[3]) if bounds else 0.0,
        "lon_min": float(b4326[0]) if b4326 else 0.0,
        "lat_min": float(b4326[1]) if b4326 else 0.0,
        "lon_max": float(b4326[2]) if b4326 else 0.0,
        "lat_max": float(b4326[3]) if b4326 else 0.0,
        "nodata": _nodata_text(meta.get("nodata")),
        "is_tiled": 1 if meta.get("is_tiled") else 0,
        "block_width": int(meta.get("block_width") or 0),
        "block_height": int(meta.get("block_height") or 0),
        "compression": meta.get("compression") or "",
        "overview_count": len(meta.get("overviews") or []),
        "overview_levels": _join(meta.get("overviews") or []),
        "is_cog": 1 if meta.get("is_cog") else 0,
        "error": "",
    }
    return row


def to_public(row) -> dict:
    """Row (Document or dict) -> consumer-facing dict with arrays reassembled.

    Shape mirrors the geospatial service's ``read_metadata`` so a plugin can use
    either source interchangeably: ``geotransform`` in GDAL order, ``pixel_size``
    ``[x, y]``, ``bounds``/``bounds_4326`` ``[minx, miny, maxx, maxy]``,
    ``block_size`` ``[w, h]``, ``overviews`` and ``color_interp`` lists.
    ``epsg`` is None (not 0) when unknown. ``error`` is None on success.
    """
    g = row.get  # both dict and Document expose .get(key) -> None when unset
    has_transform = bool(g("has_transform"))
    georef = bool(g("is_georeferenced"))
    px = [float(g("pixel_size_x") or 0), float(g("pixel_size_y") or 0)] if has_transform else None
    b4326 = [float(g("lon_min") or 0), float(g("lat_min") or 0), float(g("lon_max") or 0), float(g("lat_max") or 0)]

    return {
        "dataset": g("dataset"),
        "driver": g("driver") or None,
        "file_size": int(g("file_size") or 0) or None,
        "width": int(g("width") or 0) or None,
        "height": int(g("height") or 0) or None,
        "band_count": int(g("band_count") or 0) or None,
        "dtype": g("dtype") or None,
        "color_interp": _split(g("color_interp")),
        "is_georeferenced": georef,
        "epsg": int(g("epsg")) if g("epsg") else None,
        "crs_wkt": g("crs_wkt") or None,
        "crs_units": g("crs_units") or None,
        "geotransform": (
            [float(g("origin_x") or 0), px[0], float(g("rotation_x") or 0),
             float(g("origin_y") or 0), float(g("rotation_y") or 0), -px[1]]
            if has_transform else None
        ),
        "pixel_size": px,
        "bounds": (
            [float(g("min_x") or 0), float(g("min_y") or 0), float(g("max_x") or 0), float(g("max_y") or 0)]
            if has_transform else None
        ),
        # All-zero means the service could not reproject (exotic CRS), not a
        # raster at 0°N 0°E — treat it as unknown.
        "bounds_4326": b4326 if georef and any(b4326) else None,
        "nodata": nodata_value(g("nodata")),
        "is_tiled": bool(g("is_tiled")),
        "block_size": (
            [int(g("block_width")), int(g("block_height"))]
            if g("block_width") and g("block_height") else None
        ),
        "compression": g("compression") or None,
        "overviews": _split_ints(g("overview_levels")),
        "is_cog": bool(g("is_cog")),
        "extracted_at": str(g("extracted_at")) if g("extracted_at") else None,
        "error": g("error") or None,
    }


# --- persistence ------------------------------------------------------------

def _row_name(task_name: str, dataset: str) -> str | None:
    names = frappe.get_all(
        DOCTYPE,
        filters={"parent": task_name, "parenttype": PARENTTYPE, "dataset": dataset},
        pluck="name",
    )
    return names[0] if names else None


def store(task_name: str, dataset: str, values: dict) -> str:
    """Upsert the row for ``(task, dataset)`` and return its name.

    Writes the child row directly rather than saving the parent: the task
    runner owns the parent's processing-state columns via ``db_set`` and a
    full ``save()`` here would re-validate and rewrite them.
    """
    if dataset not in RASTER_DATASETS:
        raise ValueError(f"unknown raster dataset: {dataset}")

    frappe.db.delete(DOCTYPE, {"parent": task_name, "parenttype": PARENTTYPE, "dataset": dataset})
    idx = frappe.db.count(DOCTYPE, {"parent": task_name, "parenttype": PARENTTYPE}) + 1
    row = frappe.get_doc({
        "doctype": DOCTYPE,
        "parent": task_name,
        "parenttype": PARENTTYPE,
        "parentfield": PARENTFIELD,
        "idx": idx,
        "dataset": dataset,
        "extracted_at": now_datetime(),
        **values,
    })
    row.insert(ignore_permissions=True)
    return row.name


def store_error(task_name: str, dataset: str, error: str) -> str:
    return store(task_name, dataset, {"error": str(error)[:1000]})


def get(task_name: str, dataset: str) -> dict | None:
    """Public dict for one dataset, or None when nothing has been extracted yet."""
    name = _row_name(task_name, dataset)
    if not name:
        return None
    return to_public(frappe.get_doc(DOCTYPE, name))


def get_all(task_name: str) -> dict[str, dict]:
    """``{dataset: public dict}`` for every extracted dataset of a task."""
    rows = frappe.get_all(
        DOCTYPE, filters={"parent": task_name, "parenttype": PARENTTYPE}, fields=["name", "dataset"],
    )
    return {r.dataset: to_public(frappe.get_doc(DOCTYPE, r.name)) for r in rows}


# --- extraction -------------------------------------------------------------

def extract(task_name: str, dataset: str, abs_path: str) -> dict:
    """Read metadata for ``abs_path`` via the geospatial service and store it.

    Raises ``GeospatialError`` / ``GeospatialUnavailable`` (after recording the
    failure on the row) so an interactive caller can report why. Returns the
    public dict on success.
    """
    try:
        meta = fetch_raster_metadata(abs_path)
    except GeospatialError as e:
        store_error(task_name, dataset, str(e))
        raise
    store(task_name, dataset, normalize(meta))
    frappe.logger("webodm").info(f"raster metadata extracted: task={task_name} dataset={dataset}")
    return get(task_name, dataset)


def record(task, dataset: str, abs_path: str, metadata: dict | None = None) -> bool:
    """Pipeline hook: persist metadata for a raster that just became available.

    ``metadata`` is the dict the cogify call already returned, when it did;
    otherwise the service is asked directly. Every failure is logged and
    recorded on the row, never raised — the task's completion must not depend
    on derived data. Returns True when metadata (not an error) was stored.
    """
    task_name = task.name if hasattr(task, "name") else str(task)
    try:
        if metadata:
            store(task_name, dataset, normalize(metadata))
            frappe.logger("webodm").info(
                f"raster metadata stored from cogify: task={task_name} dataset={dataset}"
            )
            return True
        extract(task_name, dataset, abs_path)
        return True
    except GeospatialError as e:
        frappe.log_error(f"{task_name}/{dataset}: metadata extraction failed: {e}", _LOG_TITLE)
        return False
    except Exception as e:
        frappe.log_error(f"{task_name}/{dataset}: unexpected metadata error: {e}", _LOG_TITLE)
        try:
            store_error(task_name, dataset, str(e))
        except Exception:
            pass
        return False


def backfill(limit: int = 50, include_failed: bool = False) -> dict:
    """Extract metadata for completed tasks whose rasters have none yet.

    For tasks processed before this feature existed. Run manually
    (``bench execute webodm_core.webodm_core.processing.raster_metadata.backfill``)
    or from a job; processes at most ``limit`` rasters per call so it can be
    repeated safely. Returns ``{"extracted": n, "failed": n, "skipped": n}``.
    """
    from webodm_core.plugins.files import abs_path_for_file_url

    stats = {"extracted": 0, "failed": 0, "skipped": 0}
    tasks = frappe.get_all(
        PARENTTYPE, filters={"status": "Completed"},
        fields=["name", *RASTER_DATASETS], order_by="creation desc",
    )
    budget = limit
    for t in tasks:
        if budget <= 0:
            break
        existing = {
            r.dataset: r for r in frappe.get_all(
                DOCTYPE, filters={"parent": t.name, "parenttype": PARENTTYPE},
                fields=["dataset", "error"],
            )
        }
        for dataset in RASTER_DATASETS:
            if budget <= 0:
                break
            if not t.get(dataset):
                continue
            row = existing.get(dataset)
            if row is not None and not (include_failed and row.error):
                stats["skipped"] += 1
                continue
            try:
                path = abs_path_for_file_url(t.get(dataset))
            except Exception as e:
                frappe.log_error(f"{t.name}/{dataset}: cannot resolve raster path: {e}", _LOG_TITLE)
                stats["failed"] += 1
                budget -= 1
                continue
            ok = record(t.name, dataset, path)
            stats["extracted" if ok else "failed"] += 1
            budget -= 1
        frappe.db.commit()
    return stats
