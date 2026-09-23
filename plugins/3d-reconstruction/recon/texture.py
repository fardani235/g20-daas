"""Texture images for the terrain mesh and texture optimisation for existing meshes.

Colour sources, in order of preference when ``texture_source`` is ``auto``:

1. the orthophoto, warped onto the model bounds (``WarpedVRT`` reads from the
   overview level closest to the texture resolution);
2. point-cloud RGB rasterised on the same grid (``pointcloud.rasterize``);
3. shaded relief: hillshade of the heightfield itself blended with a
   hypsometric (elevation) ramp — always available, so a DSM-only task still
   yields a readable model.

Whatever the source, cells it does not cover are filled from the next source
down (or the relief), so the mesh never shows black holes. Images are encoded
as JPEG — the format the viewer's texture-budget decoder handles best.
"""

import io

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from .crs import require_projected
from .heightfield import Grid, overview_level_for, raster_info

Image.MAX_IMAGE_PIXELS = None  # 8192² atlases from ODM exceed Pillow's default bomb check

MAX_TEXTURE_SIDE = 8192
MIN_TEXTURE_SIDE = 256
# Accept an overview up to this much coarser than the texel size instead of
# warping from full resolution (4x the pixels for <35% more sharpness).
TEXTURE_OVERVIEW_TOLERANCE = 1.35

# Hypsometric ramp (low -> high), muted so it reads as terrain rather than a heatmap.
_RAMP = np.array([
    [72, 110, 62], [118, 150, 80], [176, 178, 110], [208, 186, 140], [196, 160, 128], [222, 214, 206],
], dtype=np.float32)


STRIP_ROWS = 1024


def _open_warped(path: str, grid: Grid):
    """Context: (WarpedVRT over the best overview, source dataset)."""
    import contextlib

    @contextlib.contextmanager
    def cm():
        with rasterio.open(path) as src:
            require_projected(src.crs, path)
            level = overview_level_for(src, grid.res, tolerance=TEXTURE_OVERVIEW_TOLERANCE)
            ovr = rasterio.open(path, overview_level=level) if level is not None else None
            try:
                with WarpedVRT(ovr or src, crs=grid.crs, transform=grid.transform, width=grid.width,
                               height=grid.height, resampling=Resampling.bilinear, src_nodata=src.nodata,
                               nodata=src.nodata if src.nodata is not None else None) as vrt:
                    yield vrt, src
            finally:
                if ovr is not None:
                    ovr.close()
    return cm()


def paint_orthophoto(path: str, grid: Grid, out: np.ndarray, *, strip_rows: int = STRIP_ROWS,
                     on_progress=None) -> float:
    """Paint the orthophoto (warped onto ``grid``) into ``out`` (H, W, 3) where it has data.

    Works in horizontal strips so the transient memory is a strip, not the
    whole texture (an 8192² texture would otherwise cost ~1 GB in copies).
    Returns the share of texels painted.
    """
    from rasterio.windows import Window

    eight_bit = raster_info(path)["dtype"] == "uint8"
    painted = 0
    with _open_warped(path, grid) as (vrt, src):
        bands = [1, 2, 3] if src.count >= 3 else [1, 1, 1]
        has_mask = src.nodata is not None or src.count >= 4 or any(f.name != "all_valid" for f in src.mask_flag_enums[0])
        lo_hi = None
        for r0 in range(0, grid.height, strip_rows):
            rows = min(strip_rows, grid.height - r0)
            window = Window(0, r0, grid.width, rows)
            valid = vrt.dataset_mask(window=window) > 0 if has_mask else np.ones((rows, grid.width), bool)
            if not valid.any():
                continue
            target = out[r0:r0 + rows]
            for i, b in enumerate(bands):
                if eight_bit:
                    band = vrt.read(b, window=window, out_dtype=np.uint8)
                else:
                    band = vrt.read(b, window=window, out_dtype=np.float32)
                    if lo_hi is None:  # stretch from the first strip with data (16-bit / float orthos)
                        sample = band[valid]
                        lo_hi = tuple(np.percentile(sample, [1, 99])) if sample.size > 1 else (0.0, 1.0)
                    np.clip((band - lo_hi[0]) / max(lo_hi[1] - lo_hi[0], 1e-6) * 255, 0, 255, out=band)
                    band = band.astype(np.uint8)
                np.copyto(target[..., i], band, where=valid)
            painted += int(valid.sum())
            if on_progress:
                on_progress((r0 + rows) / grid.height)
    return painted / float(grid.cells)


def orthophoto_texture(path: str, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """``(rgb uint8 (H, W, 3), valid bool (H, W))`` of the orthophoto warped onto ``grid``."""
    rgb = np.zeros((grid.height, grid.width, 3), np.uint8)
    with _open_warped(path, grid) as (vrt, src):
        valid = vrt.dataset_mask() > 0
    paint_orthophoto(path, grid, rgb)
    return rgb, valid


def hillshade(z: np.ndarray, res: float, *, azimuth_deg: float = 315.0, altitude_deg: float = 45.0) -> np.ndarray:
    """Lambertian hillshade in 0..1 for elevation ``z`` (NaNs allowed)."""
    zf = np.where(np.isfinite(z), z, np.nanmean(z) if np.isfinite(z).any() else 0.0)
    gy, gx = np.gradient(zf, res)
    az = np.radians(360.0 - azimuth_deg + 90.0)
    alt = np.radians(altitude_deg)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(shade, 0.0, 1.0).astype(np.float32)


def shaded_relief(z: np.ma.MaskedArray, res: float) -> np.ndarray:
    """RGB uint8 (H, W, 3): elevation ramp modulated by hillshade."""
    data = np.ma.filled(z.astype(np.float32), np.nan)
    finite = np.isfinite(data)
    if finite.any():
        lo, hi = np.nanpercentile(data, [2, 98])
    else:
        lo, hi = 0.0, 1.0
    t = np.clip((np.nan_to_num(data, nan=lo) - lo) / max(hi - lo, 1e-6), 0, 1)
    pos = t * (len(_RAMP) - 1)
    i = np.clip(np.floor(pos).astype(int), 0, len(_RAMP) - 2)
    frac = (pos - i)[..., None]
    color = _RAMP[i] * (1 - frac) + _RAMP[i + 1] * frac
    shade = hillshade(data, res)[..., None]
    rgb = color * (0.35 + 0.65 * shade)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def resample_rgb(rgb: np.ndarray, valid: np.ndarray | None, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray | None]:
    """Resize an RGB image (and its validity mask) to ``(width, height)``."""
    width, height = size
    out = np.array(Image.fromarray(np.ascontiguousarray(rgb)).resize((width, height), Image.BILINEAR))
    if valid is None or valid.all():
        return out, None
    m = Image.fromarray((valid * 255).astype(np.uint8)).resize((width, height), Image.NEAREST)
    return out, np.asarray(m) > 0


def fill_rgb_gaps(rgb: np.ndarray, valid: np.ndarray, max_px: int) -> tuple[np.ndarray, np.ndarray]:
    """Fill colour cells within ``max_px`` of a coloured cell (point clouds leave pinholes)."""
    from rasterio.fill import fillnodata

    if valid.all() or max_px <= 0:
        return rgb, valid
    out = np.empty_like(rgb)
    mask = valid.astype(np.uint8)
    for i in range(3):
        band = rgb[..., i].astype(np.float32)
        band[~valid] = np.nan
        filled = fillnodata(band, mask=mask, max_search_distance=float(max_px), smoothing_iterations=0)
        out[..., i] = np.nan_to_num(filled, nan=0).astype(np.uint8)
    reached = np.isfinite(filled) | valid
    return out, reached


def compose(base: np.ndarray, overlays: list[tuple[str, np.ndarray, np.ndarray | None]]) -> tuple[np.ndarray, dict]:
    """Paint ``overlays`` (name, rgb, valid) over ``base`` in order, first overlay on top.

    Works in place on ``base`` (which becomes the result) with one 2-D mask,
    and reports the share of texels each source ended up covering.
    """
    remaining = np.ones(base.shape[:2], bool)
    report = {}
    for name, rgb, valid in overlays:
        take = remaining if valid is None else (remaining & valid)
        np.copyto(base, rgb, where=take[..., None])
        report[name] = round(float(take.mean()), 4)
        remaining &= ~take
        if not remaining.any():
            break
    report["shaded-relief"] = round(float(remaining.mean()), 4)
    return base, report


def encode_jpeg(rgb: np.ndarray, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb)).save(buf, format="JPEG", quality=int(quality), optimize=True,
                                                    subsampling=0 if quality >= 90 else 2)
    return buf.getvalue()


def shrink_image(data: bytes, max_side: int, quality: int = 85) -> tuple[bytes, str, tuple[int, int], tuple[int, int]]:
    """Downscale an encoded image so its longest side is <= ``max_side``; re-encode as JPEG.

    Returns ``(bytes, mime, (w, h) before, (w, h) after)``. Images already small
    enough are re-encoded only when they are not JPEG (PNG atlases are huge).
    """
    with Image.open(io.BytesIO(data)) as img:
        before = img.size
        scale = max(img.size) / float(max_side)
        if scale <= 1.0 and (img.format or "").upper() == "JPEG":
            return data, "image/jpeg", before, before
        if scale > 1.0:
            size = (max(1, int(round(img.size[0] / scale))), max(1, int(round(img.size[1] / scale))))
            img = img.resize(size, Image.LANCZOS)
        rgb = img.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=int(quality), optimize=True)
        return buf.getvalue(), "image/jpeg", before, rgb.size


def image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


def texture_side_for_budget(sizes: list[tuple[int, int]], max_side: int, budget_pixels: int) -> int:
    """Largest per-image side cap (<= max_side) that keeps the total decoded pixels within ``budget_pixels``."""
    side = int(max_side)
    while side > MIN_TEXTURE_SIDE:
        total = 0
        for w, h in sizes:
            scale = max(w, h) / float(side)
            if scale > 1.0:
                w, h = w / scale, h / scale
            total += w * h
        if total <= budget_pixels:
            break
        side //= 2
    return max(side, MIN_TEXTURE_SIDE)
