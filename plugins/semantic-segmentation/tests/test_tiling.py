import numpy as np
import pytest

from segplugin.tiling import NODATA, BandBlender, hann2d, pad_to, tile_layout


@pytest.mark.parametrize("w, h, tile, overlap", [
    (1000, 700, 512, 64), (512, 512, 512, 64), (300, 200, 512, 64), (1030, 1030, 512, 0), (7, 5, 4, 1),
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


def test_hann_weights_taper_only_in_overlap():
    w = hann2d(64, 64, 16)
    assert w[32, 32] == pytest.approx(1.0)
    assert w[0, 32] < 0.05 and w[32, 0] < 0.05
    assert w[16, 32] == pytest.approx(1.0)
    assert (hann2d(8, 8, 0) == 1).all()


def test_blender_matches_naive_weighted_argmax():
    rng = np.random.default_rng(1)
    W, H, tile, overlap, C = 130, 97, 40, 12, 3
    rows, tw, th = tile_layout(W, H, tile, overlap)
    weights = hann2d(th, tw, overlap)
    field = rng.random((C, H, W)).astype("float32")
    valid = np.ones((H, W), bool)
    valid[:, :5] = False                        # a nodata strip

    acc = np.zeros_like(field)
    wacc = np.zeros((H, W), "float32")
    blender = BandBlender(C, W, H, [10, 20, 30])
    labels = np.full((H, W), NODATA, "uint8")
    for row in rows:
        out = blender.begin_row(row[0].y0, th)
        if out:
            labels[out[0]:out[0] + out[1].shape[0]] = out[1]
        for t in row:
            sl = (slice(t.y0, t.y0 + t.h), slice(t.x0, t.x0 + t.w))
            probs = field[:, sl[0], sl[1]] + rng.normal(0, 0.05, (C, t.h, t.w)).astype("float32")
            v = valid[sl]
            blender.add(t, probs, weights, v)
            acc[:, sl[0], sl[1]] += probs * (weights * v)[None]
            wacc[sl] += weights * v
        blender.end_row()
    out = blender.finish()
    labels[out[0]:out[0] + out[1].shape[0]] = out[1]

    expected = np.array([10, 20, 30], "uint8")[np.argmax(acc, axis=0)]
    expected[wacc <= 0] = NODATA
    assert (labels == expected).all()
    assert (labels[:, :5] == NODATA).all()


def test_pad_to_reflects_small_tiles():
    a = np.arange(12, dtype="float32").reshape(3, 4)
    p = pad_to(a, 6, 6)
    assert p.shape == (6, 6) and (p[:3, :4] == a).all()
    assert pad_to(a[None], 3, 4).shape == (1, 3, 4)
