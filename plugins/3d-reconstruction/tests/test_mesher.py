import numpy as np
import pytest
from rasterio.crs import CRS

from recon3d.grid import Grid
from recon3d.mesher import build_terrain_mesh


def _grid(size=33, res=1.0):
    return Grid(CRS.from_epsg(32633), 500000.0, 4500000.0, res, size, size)


def test_grid_geometry():
    g = _grid(17, 2.0)
    assert g.bounds == (500000.0, 4500000.0 - 32.0, 500000.0 + 32.0, 4500000.0)
    assert g.node_transform * (0.5, 0.5) == (500000.0, 4500000.0)   # pixel centre = node 0,0
    assert g.sub_extent(8, 8, 8) == (500016.0, 4500000.0 - 32.0, 500032.0, 4500000.0 - 16.0)
    sq = Grid.square_for(g.crs, (0, 0, 100, 60), 1.0, 2049)
    assert sq.width == sq.height == 129 and sq.res == 1.0
    coarse = Grid.square_for(g.crs, (0, 0, 10000, 10000), 1.0, 129)
    assert coarse.width == 129 and coarse.res == pytest.approx(10000 / 128)


def test_vertices_sit_on_heightfield_nodes_and_face_up():
    g = _grid(33, 1.0)
    yy, xx = np.mgrid[0:33, 0:33]
    z = (100 + 0.1 * xx + 5 * ((xx > 10) & (xx < 20) & (yy > 10) & (yy < 20))).astype("float32")
    mesh = build_terrain_mesh(g, z, max_triangles=5000, tiles_per_side=1)
    tile = mesh.tiles[0]
    # every vertex is a node: world position -> node -> height matches
    world = tile.positions + np.array(mesh.origin)
    cols = np.rint((world[:, 0] - g.x0) / g.res).astype(int)
    rows = np.rint((g.y0 - world[:, 1]) / g.res).astype(int)
    assert np.allclose(world[:, 2], z[rows, cols])
    # UVs match the node position within the tile
    assert np.allclose(tile.uvs[:, 0], cols / 32) and np.allclose(tile.uvs[:, 1], rows / 32)
    # counter-clockwise from above
    a, b, c = (tile.positions[tile.indices[:, i], :2] for i in range(3))
    signed = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    assert (signed > 0).all()
    assert mesh.origin == (500016.0, 4500000.0 - 16.0, 0.0)


def test_budget_and_error_control():
    g = _grid(65, 1.0)
    z = (np.random.default_rng(0).random((65, 65)) * 3).astype("float32")
    small = build_terrain_mesh(g, z, max_triangles=300)
    assert small.triangle_count <= 300
    big = build_terrain_mesh(g, z, max_triangles=8000)
    assert big.triangle_count > small.triangle_count
    fixed = build_terrain_mesh(g, z, max_triangles=100000, max_error=1.0)
    assert fixed.max_error == 1.0
    grid_mesh = build_terrain_mesh(g, z, max_triangles=100000, method="grid")
    assert grid_mesh.triangle_count == 2 * 64 * 64


def test_tiles_and_nodata():
    g = _grid(33, 1.0)
    z = np.full((33, 33), 50.0, dtype="float32")
    z[:, :8] = np.nan            # west quarter missing
    z[14:17, 14:17] = np.nan     # small hole
    mesh = build_terrain_mesh(g, z, max_triangles=5000, tiles_per_side=2)
    assert {(t.tile_x, t.tile_y) for t in mesh.tiles} == {(0, 0), (1, 0), (0, 1), (1, 1)}
    for t in mesh.tiles:
        assert t.uvs.min() >= 0 and t.uvs.max() <= 1
        assert t.indices.max() < len(t.positions)
        assert np.isfinite(t.positions).all()
    world_x = np.concatenate([t.positions[:, 0] for t in mesh.tiles]) + mesh.origin[0]
    assert world_x.min() >= g.x0 + 8 * g.res - 1e-6
    with pytest.raises(ValueError):
        build_terrain_mesh(g, np.full((33, 33), np.nan, "float32"), max_triangles=10)
