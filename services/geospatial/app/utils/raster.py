"""Raster processing utilities (GDAL / Rasterio).

These helpers wrap rasterio + rio-cogeo to convert ODM output rasters into
Cloud Optimized GeoTIFFs and to read their georeferencing (bounds reprojected
to EPSG:4326, plus the source CRS as an EPSG code or WKT string).
"""

import logging
import math
import os
import tempfile

import rasterio
from rasterio.warp import transform_bounds
from rio_cogeo.cogeo import cog_translate, cog_validate
from rio_tiler.io import Reader
from rio_tiler.colormap import cmap as default_cmaps
from rio_tiler.constants import WGS84_CRS
from rio_cogeo.profiles import cog_profiles

from app.utils import objectstore

log = logging.getLogger("webodm.geospatial.raster")


def _gdal(path: str) -> str:
    """Path as GDAL wants it: ``s3://`` -> ``/vsis3/``, local untouched."""
    return objectstore.resolve_read_path(path)


def is_cog(path: str) -> bool:
    """Return True if the raster at ``path`` (local or ``s3://``) is already a valid COG."""
    try:
        with objectstore.open_env():
            valid, _errors, _warnings = cog_validate(_gdal(path), quiet=True)
        return bool(valid)
    except Exception:
        return False


def to_cog(src_path: str, dst_path: str | None = None, web_optimized: bool = False) -> str:
    """Translate ``src_path`` into a Cloud Optimized GeoTIFF.

    ``src_path`` / ``dst_path`` are local paths or ``s3://`` URIs in any mix.
    Local -> local writes to ``dst_path`` (defaults to overwriting ``src_path``
    via a temp file). When the destination is an object URI the COG is built
    in container scratch space (``COG_SCRATCH_DIR``, never the shared serving
    cache) and uploaded, so an S3 -> S3 conversion touches no host disk. The
    source is read through ``/vsis3/`` either way. Returns the destination.
    Idempotent: a local source that is already a valid COG and has no separate
    destination is left untouched.
    """
    if dst_path is None:
        if objectstore.is_object_uri(src_path):
            raise ValueError("an object-storage source needs an explicit output_path")
        dst_path = src_path

    if dst_path == src_path and is_cog(src_path):
        return src_path

    profile = cog_profiles.get("deflate")
    config = {"GDAL_NUM_THREADS": "ALL_CPUS", "GDAL_TIFF_OVR_BLOCKSIZE": "512"}
    # Range-read tuning for an S3 source; credentials come from the enclosing
    # open_env() session, which the nested Env inside cog_translate inherits.
    config.update(objectstore.gdal_options())

    if objectstore.is_object_uri(dst_path):
        objectstore.parse_uri(dst_path)  # validate before doing any work
        fd, tmp_path = tempfile.mkstemp(prefix="cog-", suffix=".tif", dir=objectstore.scratch_dir())
        os.close(fd)
    else:
        # cog_translate cannot always write onto its own input, so stage a temp
        # file when converting in place.
        tmp_path = dst_path + ".cog.tmp"

    try:
        with objectstore.open_env():
            cog_translate(
                _gdal(src_path),
                tmp_path,
                profile,
                config=config,
                web_optimized=web_optimized,
                in_memory=False,
                quiet=True,
            )
        if objectstore.is_object_uri(dst_path):
            objectstore.upload_file(tmp_path, dst_path, "image/tiff")
        else:
            os.replace(tmp_path, dst_path)
    finally:
        if objectstore.is_object_uri(dst_path) or os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
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
    with objectstore.open_env(), rasterio.open(_gdal(path)) as ds:
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


class RasterMetadataError(ValueError):
    """The file is missing, not a raster GDAL can open, or unreadable."""


def _polygon(minx, miny, maxx, maxy) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny],
        ]],
    }


def _json_number(value):
    """Floats that JSON can carry: NaN/inf become strings, ints stay ints."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return "nan"
    if math.isinf(f):
        return "inf" if f > 0 else "-inf"
    return f


def _crs_info(crs) -> dict:
    out = {"epsg": None, "wkt": None, "units": None, "is_geographic": False, "is_projected": False}
    if crs is None:
        return out
    try:
        epsg = crs.to_epsg()
    except Exception:
        epsg = None
    out["epsg"] = int(epsg) if epsg is not None else None
    try:
        out["wkt"] = crs.to_wkt()
    except Exception:
        out["wkt"] = None
    try:
        out["is_geographic"] = bool(crs.is_geographic)
        out["is_projected"] = bool(crs.is_projected)
        if crs.is_geographic:
            out["units"] = "degree"
        else:
            out["units"] = crs.linear_units or None
    except Exception:
        pass
    return out


def read_metadata(path: str) -> dict:
    """Normalized metadata of a raster, read from its headers only.

    Nothing here touches pixel data: dimensions, CRS, geotransform, block
    layout, compression, overviews and colour interpretation are all in the
    file header (and the overview IFDs), so this is cheap even for a
    multi-gigabyte orthophoto. Statistics are deliberately *not* computed.

    ``georeference`` summarises how usable the georeferencing is:
      - ``full``: a CRS and a real geotransform (``bounds_4326``/``extent`` set)
      - ``no_crs``: a geotransform but no CRS (native bounds only)
      - ``no_transform``: a CRS but an identity transform (pixel space)
      - ``none``: neither

    Raises ``RasterMetadataError`` if the file is missing or GDAL cannot open it.
    """
    remote = objectstore.is_object_uri(path)
    if remote:
        try:
            size = objectstore.object_size(path)
        except objectstore.ObjectStoreError as e:
            raise RasterMetadataError(str(e)) from e
        if size is None:
            raise RasterMetadataError(f"raster not found: {path}")
        file_size = int(size)
    else:
        if not os.path.isfile(path):
            raise RasterMetadataError(f"raster not found: {path}")
        file_size = int(os.path.getsize(path))

    with objectstore.open_env():
        try:
            ds = rasterio.open(_gdal(path))
        except Exception as e:  # rasterio.errors.RasterioIOError and friends
            raise RasterMetadataError(f"cannot open raster: {e}") from e
        with ds:
            return _metadata_from_dataset(ds, path, file_size)


def _metadata_from_dataset(ds, path: str, file_size: int) -> dict:
    transform = ds.transform
    has_transform = not transform.is_identity
    crs = _crs_info(ds.crs)
    has_crs = ds.crs is not None

    if has_crs and has_transform:
        georeference = "full"
    elif has_transform:
        georeference = "no_crs"
    elif has_crs:
        georeference = "no_transform"
    else:
        georeference = "none"

    bounds = None
    pixel_size = None
    geotransform = None
    if has_transform:
        b = ds.bounds
        bounds = [float(b.left), float(b.bottom), float(b.right), float(b.top)]
        pixel_size = [abs(float(transform.a)), abs(float(transform.e))]
        # GDAL order: (x0, xres, xrot, y0, yrot, -yres)
        geotransform = [float(v) for v in transform.to_gdal()]

    bounds_4326 = None
    extent = None
    if georeference == "full":
        try:
            minx, miny, maxx, maxy = transform_bounds(
                ds.crs, "EPSG:4326", *bounds, densify_pts=21
            )
            if all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
                bounds_4326 = [minx, miny, maxx, maxy]
                extent = _polygon(minx, miny, maxx, maxy)
        except Exception as e:
            log.warning("bounds reprojection failed for %s: %s", path, e)

    bands = []
    for i in range(1, ds.count + 1):
        try:
            ci = ds.colorinterp[i - 1].name
        except Exception:
            ci = None
        try:
            rows, cols = ds.block_shapes[i - 1]  # rasterio: (rows, cols)
            block = [int(cols), int(rows)]        # ours: [width, height]
        except Exception:
            block = None
        try:
            overviews = [int(o) for o in ds.overviews(i)]
        except Exception:
            overviews = []
        bands.append({
            "index": i,
            "dtype": ds.dtypes[i - 1],
            "color_interpretation": ci,
            "nodata": _json_number(ds.nodatavals[i - 1]),
            "overviews": overviews,
            "block_size": block,
        })

    has_colormap = False
    if any(b["color_interpretation"] == "palette" for b in bands):
        try:
            has_colormap = bool(ds.colormap(1))
        except Exception:
            has_colormap = False

    # Driver-level structure tags (TIFF: COMPRESSION / INTERLEAVE / PREDICTOR).
    try:
        structure = ds.tags(ns="IMAGE_STRUCTURE")
    except Exception:
        structure = {}
    compression = None
    if ds.compression is not None:
        compression = ds.compression.name.lower()
    elif structure.get("COMPRESSION"):
        compression = str(structure["COMPRESSION"]).lower()
    interleave = None
    if ds.interleaving is not None:
        interleave = ds.interleaving.name.lower()
    elif structure.get("INTERLEAVE"):
        interleave = str(structure["INTERLEAVE"]).lower()

    try:
        tags = ds.tags()
    except Exception:
        tags = {}

    first = bands[0] if bands else {}
    # Same rule as rasterio's (deprecated) ``is_tiled``: a strip spans the
    # full raster width, a tile does not.
    is_tiled = bool(bands) and all(
        b["block_size"] is not None and b["block_size"][0] != ds.width for b in bands
    )

    out = {
        "path": path,
        "file_size": file_size,
        "driver": ds.driver,
        "width": int(ds.width),
        "height": int(ds.height),
        "band_count": int(ds.count),
        "dtype": first.get("dtype"),
        "dtypes": [b["dtype"] for b in bands],
        "crs": crs,
        "georeference": georeference,
        "geotransform": geotransform,
        "pixel_size": pixel_size,
        "bounds": bounds,
        "bounds_4326": bounds_4326,
        "extent": extent,
        "nodata": _json_number(ds.nodata),
        "bands": bands,
        "color_interpretation": [b["color_interpretation"] for b in bands],
        "has_colormap": has_colormap,
        "is_tiled": is_tiled,
        "block_size": first.get("block_size"),
        "compression": compression,
        "interleave": interleave,
        "predictor": structure.get("PREDICTOR"),
        "overviews": first.get("overviews", []),
        "overview_count": len(first.get("overviews", [])),
        "software": tags.get("TIFFTAG_SOFTWARE"),
        "area_or_point": tags.get("AREA_OR_POINT"),
    }

    # COG validation reads IFD offsets only, but it is TIFF-specific and may
    # emit warnings for odd files; keep it best-effort and outside the `with`.
    out["is_cog"] = is_cog(path) if out["driver"] == "GTiff" else False
    log.info(
        "raster metadata %s: %dx%d x%d %s crs=%s georef=%s tiled=%s cog=%s",
        path, out["width"], out["height"], out["band_count"], out["dtype"],
        crs["epsg"] or ("wkt" if crs["wkt"] else None), georeference, is_tiled, out["is_cog"],
    )
    return out


def tile_info(path: str) -> dict:
    """Web-mercator tiling info for a raster: bounds (4326), zoom range, band stats.

    ``rescale`` holds a per-dataset [min, max] suitable for stretching single-band
    DEMs (DSM/DTM); it is None for multi-band imagery which renders as RGB.
    """
    with objectstore.open_env(), Reader(_gdal(path)) as r:
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
    with objectstore.open_env(), Reader(_gdal(path)) as r:
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
