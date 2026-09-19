"""Raster processing utilities (GDAL / Rasterio).

These helpers wrap rasterio + rio-cogeo to convert ODM output rasters into
Cloud Optimized GeoTIFFs and to read their georeferencing (bounds reprojected
to EPSG:4326, plus the source CRS as an EPSG code or WKT string).
"""

import os

import rasterio
from rasterio.warp import transform_bounds
from rio_cogeo.cogeo import cog_translate, cog_validate
from rio_tiler.io import Reader
from rio_tiler.colormap import cmap as default_cmaps
from rio_tiler.constants import WGS84_CRS
from rio_cogeo.profiles import cog_profiles


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

        b = ds.bounds
        minx, miny, maxx, maxy = transform_bounds(
            crs, "EPSG:4326", b.left, b.bottom, b.right, b.top, densify_pts=21
        )
        result["bounds_4326"] = [minx, miny, maxx, maxy]
        result["extent"] = {
            "type": "Polygon",
            "coordinates": [[
                [minx, miny],
                [maxx, miny],
                [maxx, maxy],
                [minx, maxy],
                [minx, miny],
            ]],
        }
        return result


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

    Raises rio_tiler.errors.TileOutsideBounds when the tile does not intersect the raster.
    """
    with Reader(path) as r:
        img = r.tile(x, y, z, tilesize=tilesize)

        colormap = None
        if kind in ("dsm", "dtm") or img.count == 1:
            stats = r.statistics()
            band = next(iter(stats.values()))
            img.rescale(in_range=((band.min, band.max),))
            colormap = default_cmaps.get("terrain")

        return img.render(img_format="PNG", colormap=colormap)
