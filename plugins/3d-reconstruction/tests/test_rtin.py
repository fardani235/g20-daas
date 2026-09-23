import numpy as np

from recon3d import rtin


def _deviation(z, tri):
    ax, ay, bx, by, cx, cy = tri
    xs = np.arange(min(ax, bx, cx), max(ax, bx, cx) + 1)
    ys = np.arange(min(ay, by, cy), max(ay, by, cy) + 1)
    X, Y = np.meshgrid(xs, ys)
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    l1 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / d
    l2 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / d
    l3 = 1 - l1 - l2
    inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
    zi = l1 * z[ay, ax] + l2 * z[by, bx] + l3 * z[cy, cx]
    return np.abs(zi - z[Y, X])[inside].max()


def test_sizes():
    assert rtin.is_valid_size(3) and rtin.is_valid_size(17) and rtin.is_valid_size(2049)
    assert not rtin.is_valid_size(16) and not rtin.is_valid_size(2)
    assert rtin.next_valid_size(2) == 3
    assert rtin.next_valid_size(17) == 17
    assert rtin.next_valid_size(18) == 33
    assert rtin.next_valid_size(1000) == 1025


def test_zero_error_is_the_full_grid_and_flat_is_two_triangles():
    size = 17
    z = np.random.default_rng(0).random((size, size)).astype("float32")
    err = rtin.compute_errors([z], size)
    assert len(rtin.extract(err, -1.0)) == 2 * (size - 1) ** 2
    flat = rtin.compute_errors([np.zeros((size, size), "float32")], size)
    assert len(rtin.extract(flat, 0.0)) == 2


def test_error_bound_holds_for_every_emitted_triangle():
    size = 33
    z = (np.random.default_rng(1).random((size, size)) * 10).astype("float32")
    err = rtin.compute_errors([z], size)
    for max_error in (0.5, 1.0, 3.0):
        tris = rtin.extract(err, max_error)
        worst = max(_deviation(z, t) for t in tris)
        assert worst <= max_error + 1e-4
    # coarser tolerance -> fewer triangles
    assert len(rtin.extract(err, 0.5)) > len(rtin.extract(err, 3.0))


def test_tiling_keeps_every_triangle_inside_one_tile():
    size = 33
    flat = rtin.compute_errors([np.zeros((size, size), "float32")], size)
    tris = rtin.extract(flat, 0.0, tile_cells=8)
    for ax, ay, bx, by, cx, cy in tris:
        xs, ys = (ax, bx, cx), (ay, by, cy)
        assert min(xs) // 8 == (max(xs) - 1) // 8
        assert min(ys) // 8 == (max(ys) - 1) // 8
    assert len(tris) == 2 * 16  # two per tile


def test_validity_mask_drops_triangles_and_follows_the_boundary():
    size = 33
    z = np.zeros((size, size), "float32")
    valid = np.ones((size, size), bool)
    valid[:, :10] = False               # west strip has no data
    err = rtin.compute_errors([z, valid.astype("float32") * 1e6], size)
    tris = rtin.extract(err, 0.0, valid=valid)
    assert len(tris) > 2
    xs = tris[:, [0, 2, 4]]
    assert xs.min() >= 10                       # nothing in the strip
    # the mesh still reaches the boundary column
    assert xs.min() == 10


def test_choose_error_respects_budget():
    size = 65
    z = (np.random.default_rng(2).random((size, size)) * 5).astype("float32")
    err = rtin.compute_errors([z], size)
    e = rtin.choose_error(err, 400, z_range=5.0)
    assert len(rtin.extract(err, e)) <= 400
    assert len(rtin.extract(err, e / 1.5)) > 400 or e <= 0.005
