"""Raster processing utilities (GDAL / Rasterio).

These helpers wrap rasterio + rio-cogeo to convert ODM output rasters into
Cloud Optimized GeoTIFFs and to read their georeferencing (bounds reprojected
to EPSG:4326, plus the source CRS as an EPSG code or WKT string) and header
metadata (``read_metadata``: size, bands, dtype, tiling, compression,
overviews ...) without touching pixel data.
"""

import logging
import math
import os

import rasterio
from rasterio.warp import transform_bounds
from rio_cogeo.cogeo import cog_translate, cog_validate
from rio_cogeo.profiles import cog_profiles
from rio_tiler.colormap import cmap as default_cmaps
from rio_tiler.constants import WGS84_CRS
from rio_tiler.io import Reader

log = logging.getLogger(__name__)


def is_cog(path: str) -> bool:
    """Return True if the raster at ``path`` is already a valid COG."""
    try:
        valid, _errors, _warnings = cog_validate(path, quiet=True)
        return bool(valid)
    except Exception:
        return False


def to_cog(src_path: str, dst_path: str | None = None, web_optimized: bool = False) -> str:
    """Translate ``src_path`` into a Cloud Optimized GeoTIFF.

    Writes to ``dst_path`` (defaults to overwriting ``src_path`` via a temp file).
    Returns the path of the resulting COG. Idempotent: if the source is already
    a valid COG and no separate destination is requested, it is left untouched.
    """
    if dst_path is None:
        dst_path = src_path

    if dst_path == src_path and is_cog(src_path):
        return src_path

    profile = cog_profiles.get("deflate")
    config = {"GDAL_NUM_THREADS": "ALL_CPUS", "GDAL_TIFF_OVR_BLOCKSIZE": "512"}

    # cog_translate cannot always write onto its own input, so stage a temp file
    # when converting in place.
    tmp_path = dst_path + ".cog.tmp"
    cog_translate(
        src_path,
        tmp_path,
        profile,
        config=config,
        web_optimized=web_optimized,
        in_memory=False,
        quiet=True,
    )
    os.replace(tmp_path, dst_path)
    return dst_path


def _bounds_4326(crs, bounds) -> tuple[list[float], dict]:
    """Reproject a native BoundingBox to EPSG:4326: ``([minx, miny, maxx, maxy], GeoJSON Polygon)``."""
    minx, miny, maxx, maxy = transform_bounds(
        crs, "EPSG:4326", bounds.left, bounds.bottom, bounds.right, bounds.top, densify_pts=21
    )
    extent = {
        "type": "Polygon",
        "coordinates": [[
            [minx, miny],
            [maxx, miny],
            [maxx, maxy],
            [minx, maxy],
            [minx, miny],
        ]],
    }
    return [minx, miny, maxx, maxy], extent


def read_georef(path: str) -> dict:
    """Read georeferencing from a raster.

    Returns a dict with:
      - ``extent``: GeoJSON Polygon of the bounds in EPSG:4326 (or None if ungeoreferenced)
      - ``bounds_4326``: [minx, miny, maxx, maxy] in EPSG:4326 (or None)
      - ``epsg``: int EPSG code of the source CRS (or None)
      - ``wkt``: source CRS as WKT when no EPSG code is available (or None)
      - ``band_count``, ``width``, ``height``
    """
    with rasterio.open(path) as ds:
        result = {
            "epsg": None,
            "wkt": None,
            "extent": None,
            "bounds_4326": None,
            "band_count": ds.count,
            "width": ds.width,
            "height": ds.height,
        }

        crs = ds.crs
        if crs is None:
            return result

        epsg = crs.to_epsg()
        if epsg is not None:
            result["epsg"] = int(epsg)
        else:
            result["wkt"] = crs.to_wkt()

        result["bounds_4326"], result["extent"] = _bounds_4326(crs, ds.bounds)
        return result


def _json_number(value):
    """Return ``value`` as a JSON-safe float: NaN/inf become their string names, None stays None."""
    if value is None:
        return None
    value = float(value)
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return value


def _crs_units(crs) -> str | None:
    if crs is None:
        return None
    if crs.is_geographic:
        return "degree"
    try:
        units = crs.linear_units
    except Exception:
        return None
    return units if units and units != "unknown" else None


def read_metadata(path: str) -> dict:
    """Read normalized header metadata from a raster without loading pixel data.

    Everything comes from the dataset header (IFDs for a GeoTIFF), so this is
    equally cheap for a 10 KB test fixture and a 50 GB orthophoto. Fields that
    do not apply to a file are ``None`` rather than missing, so consumers can
    rely on the key set:

      - ``driver``, ``file_size``, ``width``, ``height``, ``band_count``, ``dtype``
      - ``epsg`` / ``crs_wkt`` (WKT only when no EPSG code exists) / ``crs_units``
      - ``is_georeferenced``: a CRS **and** a real (non-identity) geotransform
      - ``geotransform``: GDAL-order ``[origin_x, pixel_w, rot_x, origin_y, rot_y, -pixel_h]``
        (None for an ungeoreferenced raster), ``pixel_size`` ``[xres, yres]`` in CRS units
      - ``bounds`` (native CRS), ``bounds_4326`` and ``extent`` (GeoJSON Polygon, EPSG:4326)
      - ``nodata``: number, ``"nan"`` (a float DEM's NaN cannot travel through JSON) or None
      - ``block_width`` / ``block_height`` / ``is_tiled``, ``compression``, ``overviews``
        (decimation factors), ``is_cog``, ``color_interp`` (per band, lower-case names)

    Raises ``rasterio.errors.RasterioIOError`` (or ``OSError``) when the file
    cannot be opened as a raster; the caller decides how to degrade.
    """
    file_size = os.path.getsize(path)

    with rasterio.open(path) as ds:
        crs = ds.crs
        transform = ds.transform
        has_transform = not transform.is_identity
        profile = ds.profile

        meta = {
            "driver": ds.driver,
            "file_size": file_size,
            "width": ds.width,
            "height": ds.height,
            "band_count": ds.count,
            "dtype": ds.dtypes[0] if ds.dtypes else None,
            "epsg": None,
            "crs_wkt": None,
            "crs_units": _crs_units(crs),
            "is_georeferenced": bool(crs is not None and has_transform),
            "geotransform": None,
            "pixel_size": None,
            "bounds": None,
            "bounds_4326": None,
            "extent": None,
            "nodata": _json_number(ds.nodata),
            "block_width": None,
            "block_height": None,
            "is_tiled": False,
            "compression": None,
            "overviews": [],
            "is_cog": False,
            "color_interp": [ci.name for ci in ds.colorinterp],
        }

        if crs is not None:
            epsg = crs.to_epsg()
            if epsg is not None:
                meta["epsg"] = int(epsg)
            else:
                meta["crs_wkt"] = crs.to_wkt()

        if has_transform:
            meta["geotransform"] = [float(v) for v in transform.to_gdal()]
            meta["pixel_size"] = [abs(float(ds.res[0])), abs(float(ds.res[1]))]
            b = ds.bounds
            meta["bounds"] = [float(b.left), float(b.bottom), float(b.right), float(b.top)]
            if crs is not None:
                try:
                    meta["bounds_4326"], meta["extent"] = _bounds_4326(crs, b)
                except Exception as e:  # exotic CRS with no transformation to WGS84
                    log.warning("could not reproject bounds of %s to EPSG:4326: %s", path, e)

        if ds.block_shapes:
            block_h, block_w = ds.block_shapes[0]
            meta["block_width"] = int(block_w)
            meta["block_height"] = int(block_h)
            # A striped TIFF has strips as wide as the image; rasterio's own
            # ``is_tiled`` is deprecated, so derive it the same way it did.
            tiled = profile.get("tiled")
            meta["is_tiled"] = bool(tiled) if tiled is not None else int(block_w) != ds.width

        compression = ds.compression
        if compression is not None:
            meta["compression"] = compression.value.lower()

        if ds.count:
            meta["overviews"] = [int(f) for f in ds.overviews(1)]

    # Header-only validation (IFD layout), but a second open — keep it last so
    # a raster that fails validation for exotic reasons still yields metadata.
    meta["is_cog"] = is_cog(path)
    return meta


def tile_info(path: str) -> dict:
    """Web-mercator tiling info for a raster: bounds (4326), zoom range, band stats.

    ``rescale`` holds a per-dataset [min, max] suitable for stretching single-band
    DEMs (DSM/DTM); it is None for multi-band imagery which renders as RGB.
    """
    with Reader(path) as r:
        info = r.info()
        bounds = list(r.get_geographic_bounds(WGS84_CRS))  # (minx, miny, maxx, maxy) in 4326
        out = {
            "bounds": bounds,
            "minzoom": r.minzoom,
            "maxzoom": r.maxzoom,
            "band_count": info.count,
            "rescale": None,
        }
        if info.count == 1:
            stats = r.statistics()
            band = next(iter(stats.values()))
            out["rescale"] = [band.min, band.max]
        return out


def render_tile(path: str, z: int, x: int, y: int, kind: str = "orthophoto",
                tilesize: int = 256) -> bytes:
    """Render a single XYZ tile as PNG bytes.

    - orthophoto: rendered as RGB(A); alpha masks nodata so surrounding area is transparent.
    - dsm/dtm: single-band DEM stretched to its min/max and colored with a terrain ramp.
    - paletted single band (a GeoTIFF colour table, e.g. a classification
      mask): drawn with its own palette, no stretch, so class ids keep the
      colours the producer assigned.

    Raises rio_tiler.errors.TileOutsideBounds when the tile does not intersect the raster.
    """
    with Reader(path) as r:
        img = r.tile(x, y, z, tilesize=tilesize)

        colormap = None
        if img.count == 1 and r.colormap:
            colormap = r.colormap
        elif kind in ("dsm", "dtm") or img.count == 1:
            stats = r.statistics()
            band = next(iter(stats.values()))
            img.rescale(in_range=((band.min, band.max),))
            colormap = default_cmaps.get("terrain")

        return img.render(img_format="PNG", colormap=colormap)
