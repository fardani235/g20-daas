import numpy as np
import pytest
from rasterio.windows import Window

from detplugin import tiling


def test_windows_cover_full_extent_with_overlap():
    windows = list(tiling.windows(width=100, height=100, tile=64, overlap=16))
    assert len(windows) >= 4
    covered = np.zeros((100, 100), dtype=bool)
    for w in windows:
        covered[int(w.row_off):int(w.row_off + w.height), int(w.col_off):int(w.col_off + w.width)] = True
    assert covered.all()
    assert tiling.count_windows(100, 100, 64, 16) == len(windows)


def test_small_raster_is_a_single_window():
    assert list(tiling.windows(50, 30, 640, 64)) == [Window(0, 0, 50, 30)]


def test_resolve_tiling_uses_ground_size_when_set():
    tile, overlap = tiling.resolve_tiling(0.05, {"tile_size": 640, "overlap": 64,
                                                "tile_size_m": 32.0, "overlap_m": 6.4})
    assert tile == 640    # 32 m / 0.05 m
    assert overlap == 128  # 6.4 m / 0.05 m


def test_resolve_tiling_rounds_to_32_and_clamps():
    tile, overlap = tiling.resolve_tiling(0.1, {"tile_size": 640, "overlap": 64, "tile_size_m": 1.0,
                                               "overlap_m": None})
    assert tile == 64 and overlap == 63  # clamped to the minimum tile, overlap below the tile
    tile, _ = tiling.resolve_tiling(0.01, {"tile_size": 640, "overlap": 0, "tile_size_m": 1000.0,
                                          "overlap_m": None})
    assert tile == 4096


def test_resolve_tiling_pixels_when_no_ground_size():
    assert tiling.resolve_tiling(0.05, {"tile_size": 512, "overlap": 32, "tile_size_m": None,
                                        "overlap_m": None}) == (512, 32)


def test_map_detection_drops_padding_centres():
    win = Window(100, 100, 200, 200)
    det = {"x1": -60, "y1": 100, "x2": 0, "y2": 150, "class_id": 0, "confidence": 0.9}
    assert tiling.map_detection(det, 1.0, 20, 0, 160, 200, win, 1000, 1000) is None


def test_map_detection_drops_interior_edge_clip():
    win = Window(100, 100, 200, 200)  # col0 > 0 -> left edge is interior
    det = {"x1": 10, "y1": 60, "x2": 120, "y2": 140, "class_id": 0, "confidence": 0.9}
    assert tiling.map_detection(det, 1.0, 20, 0, 160, 200, win, 1000, 1000) is None


def test_map_detection_clamps_at_raster_edge_and_maps_inside():
    win = Window(100, 100, 200, 200)
    inside = {"x1": 40, "y1": 40, "x2": 120, "y2": 120, "class_id": 0, "confidence": 0.9}
    assert tiling.map_detection(inside, 1.0, 20, 0, 160, 200, win, 1000, 1000) == (120.0, 140.0, 200.0, 220.0)

    edge_win = Window(0, 100, 200, 200)  # col0 == 0 -> outer edge, clipping allowed
    clipped = {"x1": 10, "y1": 60, "x2": 120, "y2": 140, "class_id": 0, "confidence": 0.9}
    box = tiling.map_detection(clipped, 1.0, 20, 0, 160, 200, edge_win, 1000, 1000)
    assert box is not None and box[0] == 0.0


def test_map_detection_applies_scale():
    win = Window(0, 0, 256, 256)  # 256 px tile letterboxed into 640 -> scale 2.5
    det = {"x1": 270, "y1": 270, "x2": 370, "y2": 370, "class_id": 0, "confidence": 0.9}
    box = tiling.map_detection(det, 2.5, 0, 0, 640, 640, win, 256, 256)
    assert box == pytest.approx((108.0, 108.0, 148.0, 148.0))
