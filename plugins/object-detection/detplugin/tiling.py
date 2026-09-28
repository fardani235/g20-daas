"""Tiling of the orthophoto and mapping detections back to raster pixels.

The orthophoto is far larger than a detector's fixed input, so it is processed
in overlapping tiles. Two accuracy guards beyond the raw model:

- **Tiling that ignores GSD is inconsistent.** ``tile_size`` is in raster
  pixels, so the same value covers a different ground area on a 5 cm
  orthophoto than on a 2 cm one, changing how large objects appear to the
  model. ``tile_size_m`` / ``overlap_m`` choose the tile by *ground* size
  instead, so object scale stays comparable across datasets.
- **Letterbox padding and tile edges produce fake boxes.** Edge tiles are not
  square, so they are padded to the model input. Detections centred in that
  padding, and boxes clipped at an interior tile edge (a neighbouring tile sees
  them whole), are dropped before they become annotations.
"""

from rasterio.windows import Window

from .params import TILE_MAX, TILE_MIN


def resolve_tiling(gsd_m: float, eff: dict) -> tuple[int, int]:
    """Effective (tile_px, overlap_px), honouring ground-size parameters when set."""
    if eff.get("tile_size_m"):
        tile = int(round(eff["tile_size_m"] / gsd_m))
        tile = max(TILE_MIN, min(TILE_MAX, tile))
        tile = max(32, round(tile / 32) * 32)
    else:
        tile = int(eff["tile_size"])

    if eff.get("overlap_m") is not None:
        overlap = int(round(eff["overlap_m"] / gsd_m))
    else:
        overlap = int(eff["overlap"])
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


def count_windows(width: int, height: int, tile: int, overlap: int) -> int:
    return sum(1 for _ in windows(width, height, tile, overlap))


def map_detection(det, scale, pad_x, pad_y, valid_w, valid_h, window, raster_w, raster_h, tol=1.5):
    """Map a model-space detection to global raster pixels, dropping artifacts.

    Returns ``(x1, y1, x2, y2)`` in raster pixels, or ``None`` when the
    detection is centred in letterbox padding or was clipped at an interior
    tile edge.
    """
    vx1, vy1 = pad_x, pad_y
    vx2, vy2 = pad_x + valid_w, pad_y + valid_h

    cx = (det["x1"] + det["x2"]) / 2
    cy = (det["y1"] + det["y2"]) / 2
    if not (vx1 <= cx < vx2 and vy1 <= cy < vy2):
        return None

    x1, y1 = max(det["x1"], vx1), max(det["y1"], vy1)
    x2, y2 = min(det["x2"], vx2), min(det["y2"], vy2)
    if x2 <= x1 or y2 <= y1:
        return None

    col0, row0 = int(window.col_off), int(window.row_off)
    col1, row1 = col0 + int(window.width), row0 + int(window.height)

    # A box clipped at an interior tile edge is seen whole by a neighbour tile.
    if (det["x1"] < vx1 - tol and col0 > 0) or (det["x2"] > vx2 + tol and col1 < raster_w):
        return None
    if (det["y1"] < vy1 - tol and row0 > 0) or (det["y2"] > vy2 + tol and row1 < raster_h):
        return None

    gx1 = max(0.0, min(col0 + (x1 - pad_x) / scale, raster_w))
    gy1 = max(0.0, min(row0 + (y1 - pad_y) / scale, raster_h))
    gx2 = max(0.0, min(col0 + (x2 - pad_x) / scale, raster_w))
    gy2 = max(0.0, min(row0 + (y2 - pad_y) / scale, raster_h))
    if gx2 <= gx1 or gy2 <= gy1:
        return None
    return gx1, gy1, gx2, gy2
