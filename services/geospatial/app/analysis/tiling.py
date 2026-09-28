"""Raster tiling and image preparation shared by the ML analysis operations.

Large rasters are processed in overlapping windows. ``tile_size`` is in raster
pixels, so the same value covers a different ground area on a 5 cm orthophoto
than on a 2 cm one; the ``tile_size_m`` / ``overlap_m`` parameters choose the
tile by *ground* size instead, keeping object scale comparable across datasets.
"""

import numpy as np
from PIL import Image
from rasterio.windows import Window

TILE_MIN, TILE_MAX = 64, 4096


def gsd(ds) -> float:
    """Mean ground sample distance of an open raster, in CRS units per pixel."""
    return (abs(ds.res[0]) + abs(ds.res[1])) / 2 or 1.0


def resolve_tiling(ds, params) -> tuple[int, int]:
    """Effective (tile_px, overlap_px) for ``params`` (``tile_size``, ``overlap``
    and the optional ``tile_size_m`` / ``overlap_m``), honouring ground-size
    parameters when set."""
    res = gsd(ds)
    tile_size_m = getattr(params, "tile_size_m", None)
    overlap_m = getattr(params, "overlap_m", None)
    if tile_size_m:
        tile = int(round(tile_size_m / res))
        tile = max(TILE_MIN, min(TILE_MAX, tile))
        tile = max(32, round(tile / 32) * 32)
    else:
        tile = params.tile_size

    if overlap_m is not None:
        overlap = int(round(overlap_m / res))
    else:
        overlap = params.overlap
    overlap = max(0, min(tile - 1, overlap))
    return tile, overlap


def windows(width: int, height: int, tile: int, overlap: int):
    """Row-major windows covering the raster; the last row/column may be smaller."""
    stride = max(1, tile - overlap)
    for row_off in range(0, max(1, height), stride):
        for col_off in range(0, max(1, width), stride):
            w = min(tile, width - col_off)
            h = min(tile, height - row_off)
            if w <= 0 or h <= 0:
                continue
            yield Window(col_off, row_off, w, h)


def tile_to_image(array: np.ndarray) -> np.ndarray:
    """``(bands, h, w)`` raster window -> ``(h, w, 3)`` uint8 RGB-ish image."""
    bands = array.shape[0]
    if bands >= 3:
        hwc = np.transpose(array[:3], (1, 2, 0))
    else:
        hwc = np.repeat(np.transpose(array[:1], (1, 2, 0)), 3, axis=2)
    if hwc.dtype != np.uint8:
        hwc = np.clip(hwc, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(hwc)


def letterbox(image: np.ndarray, size: tuple[int, int]):
    """Resize an ``H x W x C`` uint8 image into ``size`` (h, w) preserving aspect.

    Returns ``(padded, scale, pad_x, pad_y)`` where ``padded`` is the resized
    image placed on a mid-grey canvas of exactly ``size``.
    """
    target_h, target_w = size
    src_h, src_w = image.shape[:2]
    scale = min(target_h / src_h, target_w / src_w)
    new_h, new_w = max(1, round(src_h * scale)), max(1, round(src_w * scale))

    pil = Image.fromarray(image).resize((new_w, new_h), Image.BILINEAR)
    canvas = Image.new("RGB", (target_w, target_h), (114, 114, 114))
    pad_x = (target_w - new_w) // 2
    pad_y = (target_h - new_h) // 2
    canvas.paste(pil, (pad_x, pad_y))
    return np.asarray(canvas, dtype=np.uint8), scale, pad_x, pad_y


def preprocess(image: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, float, int, int]:
    """Letterbox and scale an ``H x W x C`` image to [0, 1] as a ``1x3xHxW`` tensor."""
    padded, scale, pad_x, pad_y = letterbox(image, size)
    tensor = padded.astype(np.float32) / 255.0
    tensor = np.transpose(tensor, (2, 0, 1))[None, ...]  # HWC -> 1CHW
    return np.ascontiguousarray(tensor), scale, pad_x, pad_y
