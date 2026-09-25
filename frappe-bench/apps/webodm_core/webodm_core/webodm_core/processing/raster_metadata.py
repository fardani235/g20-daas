"""Normalized raster metadata for task outputs (orthophoto / DSM / DTM).

Frappe has no GDAL, so the geospatial service reads the file header
(``GET /raster/metadata``; the same document also rides along in the cogify
response) and this module keeps the *normalized* subset in the
``WebODM Raster Metadata`` child table of ``WebODM Task`` - one row per
dataset, typed columns, no raw tag dumps. Consumers (viewer, tiler, plugins)
read it through ``rows_for_task`` / ``row_to_dict`` or the
``api.task.get_raster_metadata`` endpoint.

Everything here is best-effort: a metadata failure is recorded on the row
(``status = Failed`` + ``error``) and logged, and never propagates into the
processing pipeline.
"""

import json
import math

import frappe
from frappe.utils import cint, flt, now_datetime

from webodm_core.plugins import geospatial
from webodm_core.plugins.files import abs_path_for_file_url

CHILD_DOCTYPE = "WebODM Raster Metadata"
PARENT_DOCTYPE = "WebODM Task"
PARENT_FIELD = "raster_metadata"
RASTER_DATASETS = ("orthophoto", "dsm", "dtm")
LOG_TITLE = "WebODM Raster Metadata"

# Columns written from the service document (so a Failed row for a *new* file
# can blank the values of the previous file instead of misrepresenting it).
_VALUE_COLUMNS = (
    "driver", "width", "height", "band_count", "dtype", "color_interpretation",
    "has_colormap", "nodata", "has_nodata", "file_size", "software",
    "georeference", "epsg", "crs_wkt", "crs_units", "pixel_size_x", "pixel_size_y",
    "geotransform", "min_x", "min_y", "max_x", "max_y", "west", "south", "east", "north",
    "is_tiled", "block_width", "block_height", "compression", "interleave",
    "overview_count", "overview_levels", "is_cog",
)


# --------------------------------------------------------------------------
# Normalization: service document -> child-row values
# --------------------------------------------------------------------------

def _text(value, limit: int = 140):
    if value is None:
        return None
    s = str(value).strip()
    return s[:limit] if s else None


def _int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value):
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _nodata_text(value):
    """Service nodata (number, ``"nan"``, or None) -> stored text (or None)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() or None
    try:
        return repr(float(value))
    except (TypeError, ValueError):
        return None


def _pair(values):
    if isinstance(values, (list, tuple)) and len(values) >= 2:
        return values[0], values[1]
    return None, None


def _quad(values):
    if isinstance(values, (list, tuple)) and len(values) >= 4:
        return values[:4]
    return None, None, None, None


def blank_values() -> dict:
    """All value columns unset. Ints/Floats read back as 0 (Frappe coerces None
    on insert); ``georeference`` is the flag that says whether they mean anything."""
    values = {col: None for col in _VALUE_COLUMNS}
    values["georeference"] = "none"
    return values


def normalize(meta: dict) -> dict:
    """Map the geospatial ``/raster/metadata`` document to child-row values.

    Only compact, useful facts are kept: per-band detail collapses to band 1
    plus comma-joined lists, the WKT is stored only when no EPSG code names
    the CRS, and the geotransform is kept as a JSON string so full float
    precision survives (the Float columns are decimal(21,9)).
    """
    meta = meta if isinstance(meta, dict) else {}
    crs = meta.get("crs")
    if not isinstance(crs, dict):
        crs = {}
    epsg = _int(crs.get("epsg"))
    px_x, px_y = _pair(meta.get("pixel_size"))
    min_x, min_y, max_x, max_y = _quad(meta.get("bounds"))
    west, south, east, north = _quad(meta.get("bounds_4326"))
    block_w, block_h = _pair(meta.get("block_size"))
    overviews = [o for o in (meta.get("overviews") or []) if _int(o) is not None]
    colorinterp = [c for c in (meta.get("color_interpretation") or []) if c]
    geotransform = meta.get("geotransform")
    nodata = _nodata_text(meta.get("nodata"))

    values = blank_values()
    values.update({
        "driver": _text(meta.get("driver")),
        "width": _int(meta.get("width")),
        "height": _int(meta.get("height")),
        "band_count": _int(meta.get("band_count")),
        "dtype": _text(meta.get("dtype")),
        "color_interpretation": _text(",".join(str(c) for c in colorinterp)) if colorinterp else None,
        "has_colormap": 1 if meta.get("has_colormap") else 0,
        "nodata": nodata,
        "has_nodata": 1 if nodata is not None else 0,
        "file_size": _int(meta.get("file_size")),
        "software": _text(meta.get("software")),
        "georeference": _text(meta.get("georeference")) or "none",
        "epsg": epsg,
        "crs_wkt": crs.get("wkt") if epsg is None and crs.get("wkt") else None,
        "crs_units": _text(crs.get("units")),
        "pixel_size_x": _float(px_x),
        "pixel_size_y": _float(px_y),
        "geotransform": json.dumps([float(v) for v in geotransform])
        if isinstance(geotransform, (list, tuple)) and len(geotransform) == 6 else None,
        "min_x": _float(min_x), "min_y": _float(min_y),
        "max_x": _float(max_x), "max_y": _float(max_y),
        "west": _float(west), "south": _float(south),
        "east": _float(east), "north": _float(north),
        "is_tiled": 1 if meta.get("is_tiled") else 0,
        "block_width": _int(block_w),
        "block_height": _int(block_h),
        "compression": _text(meta.get("compression")),
        "interleave": _text(meta.get("interleave")),
        "overview_count": len(overviews),
        "overview_levels": _text(",".join(str(_int(o)) for o in overviews)) if overviews else None,
        "is_cog": 1 if meta.get("is_cog") else 0,
    })
    return values


# --------------------------------------------------------------------------
# Row -> API / plugin shape
# --------------------------------------------------------------------------

def _parse_nodata(text):
    if not text:
        return None
    s = str(text).strip().lower()
    if s in ("nan", "inf", "-inf"):
        return s
    try:
        return float(s)
    except ValueError:
        return None


def row_to_dict(row, task=None) -> dict:
    """Public shape of one metadata row (JSON-serialisable, typed, nulls for unknowns).

    ``task`` (optional) supplies the ``<dataset>_extent`` GeoJSON polygon so
    callers get bounds and extent from one place.
    """
    get = row.get
    georef = get("georeference") or "none"
    has_transform = georef in ("full", "no_crs")
    has_crs = georef in ("full", "no_transform")
    epsg = cint(get("epsg")) or None

    geotransform = None
    raw_gt = get("geotransform")
    if raw_gt:
        try:
            parsed = json.loads(raw_gt) if isinstance(raw_gt, str) else raw_gt
            if isinstance(parsed, list) and len(parsed) == 6:
                geotransform = [float(v) for v in parsed]
        except (TypeError, ValueError):
            geotransform = None

    # The Float columns are decimal(21,9); the geotransform JSON keeps the full
    # doubles, so derive pixel size and native bounds from it when possible
    # (north-up rasters, which is what ODM writes) so grid alignment downstream
    # is exact. The columns remain the fallback.
    width, height = cint(get("width")), cint(get("height"))
    pixel_size = [flt(get("pixel_size_x")), flt(get("pixel_size_y"))]
    bounds = [flt(get("min_x")), flt(get("min_y")), flt(get("max_x")), flt(get("max_y"))]
    if geotransform and width and height and geotransform[2] == 0 and geotransform[4] == 0:
        x0, xres, _, y0, _, yres = geotransform
        pixel_size = [abs(xres), abs(yres)]
        xs = sorted((x0, x0 + width * xres))
        ys = sorted((y0, y0 + height * yres))
        bounds = [xs[0], ys[0], xs[1], ys[1]]

    overviews = []
    if get("overview_levels"):
        overviews = [int(o) for o in str(get("overview_levels")).split(",") if o.strip().isdigit()]

    extent = None
    if task is not None:
        raw_extent = task.get(f"{get('dataset')}_extent")
        if raw_extent:
            try:
                extent = frappe.parse_json(raw_extent) if isinstance(raw_extent, str) else raw_extent
            except Exception:
                extent = None

    block = None
    if get("block_width") and get("block_height"):
        block = [cint(get("block_width")), cint(get("block_height"))]

    extracted_at = get("extracted_at")
    return {
        "dataset": get("dataset"),
        "file_url": get("file_url"),
        "status": get("status") or "Extracted",
        "error": get("error") or None,
        "extracted_at": str(extracted_at) if extracted_at else None,
        "driver": get("driver") or None,
        "width": cint(get("width")) or None,
        "height": cint(get("height")) or None,
        "band_count": cint(get("band_count")) or None,
        "dtype": get("dtype") or None,
        "crs": {
            "epsg": epsg if has_crs else None,
            "wkt": (get("crs_wkt") or None) if has_crs else None,
            "units": (get("crs_units") or None) if has_crs else None,
        },
        "georeference": georef,
        "pixel_size": pixel_size if has_transform else None,
        "geotransform": geotransform if has_transform else None,
        "bounds": bounds if has_transform else None,
        "bounds_4326": [flt(get("west")), flt(get("south")), flt(get("east")), flt(get("north"))]
        if georef == "full" else None,
        "extent": extent,
        "nodata": _parse_nodata(get("nodata")) if get("has_nodata") else None,
        "is_tiled": bool(get("is_tiled")),
        "block_size": block,
        "compression": get("compression") or None,
        "interleave": get("interleave") or None,
        "overviews": overviews,
        "overview_count": len(overviews),
        "is_cog": bool(get("is_cog")),
        "color_interpretation": [c for c in str(get("color_interpretation") or "").split(",") if c],
        "has_colormap": bool(get("has_colormap")),
        "file_size": cint(get("file_size")) or None,
        "software": get("software") or None,
    }


def rows_for_task(task) -> dict:
    """``{dataset: row_to_dict}`` for every metadata row the task has."""
    out = {}
    for row in task.get(PARENT_FIELD) or []:
        if row.get("dataset"):
            out[row.get("dataset")] = row_to_dict(row, task)
    return out


def find_row(task_name: str, dataset: str):
    """The stored row for ``dataset`` as a ``frappe._dict`` (all columns), or None."""
    rows = frappe.get_all(
        CHILD_DOCTYPE,
        filters={"parent": task_name, "parenttype": PARENT_DOCTYPE, "dataset": dataset},
        fields=["*"],
        order_by="idx asc",
        limit=1,
    )
    return rows[0] if rows else None


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

_NUMERIC_FIELDTYPES = ("Int", "Long Int", "Float", "Check", "Percent", "Currency")


def _db_safe(values: dict) -> dict:
    """Numeric columns are ``NOT NULL DEFAULT 0`` in Frappe's schema (and None
    is coerced to 0 on insert anyway), so write 0 for "unknown" explicitly and
    let ``row_to_dict`` turn it back into null using ``georeference`` /
    ``has_nodata`` / zero-means-unset."""
    meta = frappe.get_meta(CHILD_DOCTYPE)
    out = dict(values)
    for col, val in values.items():
        if val is None:
            df = meta.get_field(col)
            if df is not None and df.fieldtype in _NUMERIC_FIELDTYPES:
                out[col] = 0
    return out


def _save_row(task_name: str, dataset: str, values: dict):
    """Upsert the child row directly (no full ``task.save``: the poll job is
    the single writer of processing state and uses ``db_set`` throughout)."""
    existing = find_row(task_name, dataset)
    values = _db_safe(values)
    values["extracted_at"] = now_datetime()
    if existing:
        frappe.db.set_value(CHILD_DOCTYPE, existing.name, values, update_modified=True)
        return existing.name

    idx = frappe.db.count(CHILD_DOCTYPE, {"parent": task_name, "parenttype": PARENT_DOCTYPE}) + 1
    row = frappe.get_doc({
        "doctype": CHILD_DOCTYPE,
        "parent": task_name,
        "parenttype": PARENT_DOCTYPE,
        "parentfield": PARENT_FIELD,
        "idx": idx,
        "dataset": dataset,
        **values,
    })
    row.insert(ignore_permissions=True)
    return row.name


def record(task_name: str, dataset: str, file_url: str, metadata: dict | None = None,
           error: str | None = None):
    """Persist one dataset's metadata (or a Failed row). Returns the stored
    values as a ``frappe._dict`` or None if even that failed. Never raises."""
    if dataset not in RASTER_DATASETS:
        frappe.log_error(f"{task_name}: unknown raster dataset {dataset!r}", LOG_TITLE)
        return None
    try:
        if metadata:
            values = normalize(metadata)
            values.update({"status": "Extracted", "error": None})
        else:
            existing = find_row(task_name, dataset)
            # Keep the previous file's values only if they describe this file.
            values = blank_values() if not existing or existing.file_url != file_url else {}
            values.update({"status": "Failed", "error": (error or "metadata unavailable")[:1000]})
        values["file_url"] = file_url
        _save_row(task_name, dataset, values)
        if metadata:
            frappe.logger("webodm").info(
                f"{task_name}: {dataset} metadata stored "
                f"({values.get('width')}x{values.get('height')} x{values.get('band_count')} "
                f"{values.get('dtype')}, epsg={values.get('epsg')}, georef={values.get('georeference')})"
            )
        else:
            frappe.log_error(f"{task_name}: {dataset} metadata extraction failed: {error}", LOG_TITLE)
        return find_row(task_name, dataset)
    except Exception as e:
        frappe.log_error(f"{task_name}: could not store {dataset} metadata: {e}", LOG_TITLE)
        return None


def extract(abs_path: str):
    """Ask the geospatial service for the header metadata of ``abs_path``.

    Returns ``(metadata, None)`` or ``(None, error_message)``; never raises.
    """
    try:
        return geospatial.raster_metadata(abs_path), None
    except geospatial.GeospatialError as e:
        return None, str(e)
    except Exception as e:  # defensive: an unexpected client bug must not fail the task
        return None, f"unexpected error: {e}"


def _source_path(task, dataset: str, file_url: str) -> str:
    """Local path when cached, else the ``s3://`` URI (or the local path for host-only files)."""
    from webodm_core.storage import assets, cache

    try:
        _kind, source = assets.raster_source(task, dataset)
        return source
    except cache.CacheMiss:
        # raster_source already proved the File belongs to this task (and a
        # forged pointer raises there), so this fallback is for a legitimate
        # host-only blob whose cache copy is gone.
        return abs_path_for_file_url(file_url)
    except frappe.DoesNotExistError:
        return abs_path_for_file_url(file_url)


def _maybe_set_task_resolution(task, dataset: str, values):
    """Fill the task's ``resolution`` (cm/pixel) from the orthophoto's ground
    sampling distance when the user never set one and the CRS is metric."""
    if dataset != "orthophoto" or not values or values.get("status") != "Extracted":
        return
    if task.get("resolution"):
        return
    units = (values.get("crs_units") or "").lower()
    if values.get("georeference") != "full" or units not in ("metre", "meter", "m"):
        return
    px = flt(values.get("pixel_size_x"))
    if px > 0:
        task.db_set("resolution", round(px * 100.0, 3))


def capture(task, dataset: str, file_url: str, metadata: dict | None = None,
            abs_path: str | None = None):
    """Extract (unless ``metadata`` is already at hand, e.g. from the cogify
    response) and persist the metadata of one task raster. Best-effort: logs
    and records failures, never raises. Returns the stored row or None."""
    error = None
    if not metadata:
        # Cache first, object storage second: an evicted raster is read by the
        # geospatial service straight from S3 (header-only, a few range reads).
        try:
            abs_path = abs_path or _source_path(task, dataset, file_url)
        except Exception as e:
            abs_path, error = None, f"cannot resolve {file_url}: {e}"
        if abs_path:
            metadata, error = extract(abs_path)
    row = record(task.name, dataset, file_url, metadata=metadata, error=error)
    try:
        _maybe_set_task_resolution(task, dataset, row)
    except Exception as e:
        frappe.log_error(f"{task.name}: could not set resolution from metadata: {e}", LOG_TITLE)
    return row


def refresh(task_name: str, dataset: str | None = None):
    """(Re-)extract metadata for one or all of a task's rasters. Usable as an
    RQ job or from the API. Returns ``{dataset: row_to_dict | None}``."""
    task = frappe.get_doc(PARENT_DOCTYPE, task_name)
    out = {}
    for ds in ([dataset] if dataset else RASTER_DATASETS):
        file_url = task.get(ds)
        if not file_url:
            out[ds] = None
            continue
        row = capture(task, ds, file_url)
        out[ds] = row_to_dict(row, task) if row else None
    return out
