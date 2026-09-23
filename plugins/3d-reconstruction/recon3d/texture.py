"""Per-tile textures: orthophoto, point-cloud colours or a shaded relief.

Textures are square, one per mesh tile, and cover exactly the tile's world
extent, so the mesher's ``u = (col - col0) / cells`` maps onto them directly.
Encoding goes through GDAL's JPEG/PNG drivers (via rasterio's in-memory
files), the only image codecs available in the sandbox.
"""

import warnings

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT

from .grid import Grid
from .sources import RasterSource

NEUTRAL = np.array([128, 128, 128], dtype=np.uint8)


def orthophoto_tile(ortho: RasterSource, grid: Grid, col0: int, row0: int, cells: int, size: int):
    """RGB (3, size, size) uint8 plus a validity mask for one tile."""
    west, _south, east, north = grid.sub_extent(col0, row0, cells)
    px = (east - west) / size
    transform = from_origin(west, north, px, px)
    ratio = px / max(ortho.res_in(grid.crs), 1e-9)
    resampling = Resampling.average if ratio > 1.2 else Resampling.bilinear
    with rasterio.open(ortho.path) as src:
        bands = [1, 2, 3] if src.count >= 3 else [1, 1, 1]
        kwargs = dict(crs=grid.crs, transform=transform, width=size, height=size, resampling=resampling)
        if src.nodata is not None:
            kwargs["src_nodata"] = src.nodata
        with WarpedVRT(src, **kwargs) as vrt:
            data = vrt.read(bands)
            valid = vrt.dataset_mask() > 0
    if data.dtype != np.uint8:
        data = _to_uint8(data, valid)
    rgb = np.ascontiguousarray(data[:3]).astype(np.uint8, copy=False)
    rgb[:, ~valid] = NEUTRAL[:, None]
    return rgb, valid


def _to_uint8(data: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Stretch 16-bit / float imagery to 8-bit using the 2–98 percentile range."""
    out = np.zeros(data.shape, dtype=np.uint8)
    for i in range(data.shape[0]):
        band = data[i].astype(np.float64)
        vals = band[valid] if valid.any() else band.ravel()
        if vals.size == 0:
            continue
        lo, hi = np.percentile(vals, (2, 98))
        if hi <= lo:
            hi = lo + 1
        out[i] = np.clip((band - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    return out


def grid_colour_tile(rgb_grid: np.ndarray, col0: int, row0: int, cells: int, size: int) -> np.ndarray:
    """Upsample node colours (3, H, W) for a tile to (3, size, size) with nearest sampling.

    Nodes without colour (all zero) take the nearest coloured node's value so
    the texture has no black speckle where the cloud is sparse.
    """
    sub = rgb_grid[:, row0:row0 + cells + 1, col0:col0 + cells + 1]
    filled = _fill_zero_colours(sub)
    # Texel centres (i + 0.5) / size * cells -> node index.
    t = (np.arange(size) + 0.5) / size * cells
    nodes = np.clip(np.rint(t).astype(int), 0, cells)
    return np.ascontiguousarray(filled[:, nodes[:, None], nodes[None, :]])


def _fill_zero_colours(rgb: np.ndarray) -> np.ndarray:
    has = rgb.any(axis=0)
    if has.all() or not has.any():
        return rgb
    from rasterio.fill import fillnodata
    out = np.empty_like(rgb)
    mask = has.astype(np.uint8)
    for i in range(3):
        out[i] = np.clip(fillnodata(rgb[i].astype(np.float32), mask=mask, max_search_distance=64,
                                    smoothing_iterations=0), 0, 255).astype(np.uint8)
    out[:, ~has] = np.where(out[:, ~has] == 0, NEUTRAL[:, None], out[:, ~has])
    return out


# Elevation ramp (low -> high): greens through browns to white.
_RAMP = np.array([
    [0.00, 60, 120, 60],
    [0.30, 130, 170, 90],
    [0.55, 190, 170, 110],
    [0.80, 160, 120, 90],
    [1.00, 240, 240, 240],
], dtype=np.float64)


def hillshade_tile(z: np.ndarray, res: float, col0: int, row0: int, cells: int, size: int,
                   z_range=None, azimuth_deg: float = 315.0, altitude_deg: float = 45.0) -> np.ndarray:
    """Shaded relief coloured by elevation for one tile, (3, size, size) uint8.

    Computed at the *texture* resolution (nearest node per texel, never finer
    than the nodes) in float32, so a 2049² tile costs ~150 MB rather than the
    ~700 MB of a float64 node-resolution version.
    """
    sub = z[row0:row0 + cells + 1, col0:col0 + cells + 1]
    if not np.isfinite(sub).any():
        return np.broadcast_to(NEUTRAL[:, None, None], (3, size, size)).copy()
    # Sample nodes at texel centres (texels never exceed node count).
    n_samples = min(size, cells + 1)
    tpos = (np.arange(n_samples) + 0.5) / n_samples * cells
    nodes = np.clip(np.rint(tpos).astype(int), 0, cells)
    sampled = sub[nodes[:, None], nodes[None, :]].astype(np.float32)
    valid = np.isfinite(sampled)
    fill = np.float32(np.nanmean(sub))
    filled = np.where(valid, sampled, fill)
    step = np.float32(cells * res / n_samples)
    gy, gx = np.gradient(filled, step)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    del gx, gy
    az, alt = np.float32(np.deg2rad(azimuth_deg)), np.float32(np.deg2rad(altitude_deg))
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    del slope, aspect
    np.clip(shade, 0.0, 1.0, out=shade)
    lo, hi = z_range if z_range else (float(np.nanmin(sub)), float(np.nanmax(sub)))
    span = np.float32((hi - lo) or 1.0)
    t = np.clip((filled - np.float32(lo)) / span, 0.0, 1.0)
    del filled
    light = (0.35 + 0.65 * shade).astype(np.float32)
    del shade
    out = np.empty((3, n_samples, n_samples), dtype=np.uint8)
    for i in (1, 2, 3):
        band = np.interp(t, _RAMP[:, 0], _RAMP[:, i]).astype(np.float32)
        band *= light
        band[~valid] = NEUTRAL[i - 1]
        out[i - 1] = np.clip(band, 0, 255).astype(np.uint8)
    if n_samples != size:
        idx = np.clip((np.arange(size) * n_samples) // size, 0, n_samples - 1)
        out = np.ascontiguousarray(out[:, idx[:, None], idx[None, :]])
    return out


def encode(rgb: np.ndarray, fmt: str = "jpeg", quality: int = 85) -> tuple:
    """Encode (3, H, W) uint8 to bytes; returns (bytes, mime type)."""
    bands, h, w = rgb.shape
    if fmt == "png":
        driver, mime, kwargs = "PNG", "image/png", {}
    elif fmt == "webp":
        driver, mime, kwargs = "WEBP", "image/webp", {"quality": int(quality)}
    else:
        driver, mime, kwargs = "JPEG", "image/jpeg", {"quality": int(quality)}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)  # plain images have no geotransform
        with MemoryFile() as mem:
            with mem.open(driver=driver, width=w, height=h, count=bands, dtype="uint8", **kwargs) as dst:
                dst.write(rgb)
            data = mem.read()
    return data, mime


def decode(data: bytes, max_side: int | None = None) -> tuple:
    """Decode an image to (bands, H, W) uint8, downscaling so max(H, W) <= max_side.

    Returns (array, original_width, original_height). Downscaling happens in
    the decoder (GDAL reads JPEGs at 1/2, 1/4, 1/8 scale natively), so an
    8192² texture never has to be held at full size.
    """
    with warnings.catch_warnings(), MemoryFile(data) as mem, mem.open() as src:
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        w, h = src.width, src.height
        count = src.count
        if max_side and max(w, h) > max_side:
            scale = max_side / max(w, h)
            out_w, out_h = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
            arr = src.read(out_shape=(count, out_h, out_w), resampling=Resampling.average)
        else:
            arr = src.read()
        if arr.dtype != np.uint8:
            arr = np.clip(arr.astype(np.float64) / (65535.0 if arr.max() > 255 else 1.0), 0, 255).astype(np.uint8)
        return arr, w, h
