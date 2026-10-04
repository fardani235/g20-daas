import numpy as np
import pytest

from bfplugin.tiling import PlaneBlender, gaussian2d, pad_to, tile_layout


@pytest.mark.parametrize("w, h, tile, overlap", [
    (1000, 700, 256, 64), (256, 256, 256, 64), (300, 200, 256, 128), (1030, 1030, 256, 0), (7, 5, 4, 1),
])
def test_layout_covers_everything_with_full_tiles(w, h, tile, overlap):
    rows, tw, th = tile_layout(w, h, tile, overlap)
    cover = np.zeros((h, w), dtype=int)
    for row in rows:
        for t in row:
            assert t.w == tw and t.h == th
            assert 0 <= t.x0 and t.x0 + t.w <= w and 0 <= t.y0 and t.y0 + t.h <= h
            cover[t.y0:t.y0 + t.h, t.x0:t.x0 + t.w] += 1
    assert (cover >= 1).all()


def test_gaussian_weights_peak_in_the_centre_and_stay_positive():
    w = gaussian2d(256, 256)
    assert w[127:129, 127:129].max() == pytest.approx(1.0, abs=1e-3)
    assert w[0, 128] < 0.01 and w[128, 0] < 0.01 and w.min() >= 1e-3
    assert w[128, 64] == pytest.approx(np.exp(-0.5 * (64 / 32) ** 2), rel=0.05)   # sigma = side / 8
    assert np.allclose(w, w.T) and np.allclose(w, w[::-1, ::-1])


def test_blender_matches_naive_weighted_mean():
    rng = np.random.default_rng(1)
    W, H, tile, overlap, C = 130, 97, 40, 12, 2
    rows, tw, th = tile_layout(W, H, tile, overlap)
    weights = gaussian2d(th, tw)
    field = rng.random((C, H, W)).astype("float32")
    valid = np.ones((H, W), bool)
    valid[:, :5] = False                                   # a nodata strip

    acc = np.zeros_like(field)
    wacc = np.zeros((H, W), "float32")
    blender = PlaneBlender(C, W, H)
    out_planes = np.full((C, H, W), np.nan, "float32")
    out_cov = np.zeros((H, W), bool)

    def take(res):
        if res:
            y0, planes, covered = res
            out_planes[:, y0:y0 + planes.shape[1]] = planes
            out_cov[y0:y0 + planes.shape[1]] = covered

    for row in rows:
        take(blender.begin_row(row[0].y0, th))
        for t in row:
            sl = (slice(t.y0, t.y0 + t.h), slice(t.x0, t.x0 + t.w))
            planes = field[:, sl[0], sl[1]] + rng.normal(0, 0.05, (C, t.h, t.w)).astype("float32")
            v = valid[sl]
            blender.add(t, planes, weights, v)
            acc[:, sl[0], sl[1]] += planes * (weights * v)[None]
            wacc[sl] += weights * v
        blender.end_row()
    take(blender.finish())

    expected = acc / np.where(wacc > 0, wacc, 1)[None]
    assert (out_cov == (wacc > 0)).all() and not out_cov[:, :5].any()
    assert np.allclose(out_planes[:, out_cov], expected[:, out_cov], atol=1e-5)


def test_pad_to_reflects_small_tiles():
    a = np.arange(12, dtype="float32").reshape(1, 3, 4)
    p = pad_to(a, 5, 6)
    assert p.shape == (1, 5, 6) and (p[:, :3, :4] == a).all()
    assert pad_to(a, 2, 2) is a
