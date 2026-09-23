"""Heightfield triangulation, decimation and attribute generation.

A heightfield is a masked 2-D array of elevations on a ``Grid``. Every cell
whose four corner samples are valid becomes two triangles (split along the
diagonal with the smaller height difference, which follows ridges and
gutters instead of cutting across them); cells with exactly three valid
corners become one triangle, so hole edges stay tight. Winding is
counter-clockwise seen from above (+Z), the glTF front face.

Decimation uses quadric edge collapse (``fast_simplification``, MIT), which
does not carry vertex attributes — that is fine here because texture
coordinates are a pure function of X/Y (planar projection of the
orthophoto) and are recomputed after decimation.
"""

import numpy as np

from .heightfield import Grid


def triangulate(z: np.ma.MaskedArray, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Triangulate ``z`` (H, W) on ``grid``.

    Returns ``(vertices, faces)`` with vertices float64 (N, 3) in absolute CRS
    coordinates and faces uint32 (M, 3). Unreferenced vertices are dropped.
    """
    h, w = z.shape
    valid = ~np.ma.getmaskarray(z)
    data = np.ma.getdata(z).astype(np.float64)
    xs, ys = grid.cell_centers()

    # Corner indices per cell: a=(r,c) b=(r,c+1) c_=(r+1,c) d=(r+1,c+1)
    idx = np.arange(h * w, dtype=np.int64).reshape(h, w)
    a, b, c_, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]
    va, vb, vc, vd = valid[:-1, :-1], valid[:-1, 1:], valid[1:, :-1], valid[1:, 1:]
    za, zb, zc, zd = data[:-1, :-1], data[:-1, 1:], data[1:, :-1], data[1:, 1:]

    n_valid = va.astype(np.int8) + vb + vc + vd
    full = n_valid == 4
    # Diagonal choice: a-d when |za-zd| <= |zb-zc|, else b-c_.
    use_ad = full & (np.abs(za - zd) <= np.abs(zb - zc))
    use_bc = full & ~use_ad

    tris = [
        np.stack([a[use_ad], c_[use_ad], d[use_ad]], axis=1),
        np.stack([a[use_ad], d[use_ad], b[use_ad]], axis=1),
        np.stack([a[use_bc], c_[use_bc], b[use_bc]], axis=1),
        np.stack([b[use_bc], c_[use_bc], d[use_bc]], axis=1),
    ]
    three = n_valid == 3
    if three.any():
        m = three & ~va
        tris.append(np.stack([b[m], c_[m], d[m]], axis=1))
        m = three & ~vb
        tris.append(np.stack([a[m], c_[m], d[m]], axis=1))
        m = three & ~vc
        tris.append(np.stack([a[m], d[m], b[m]], axis=1))
        m = three & ~vd
        tris.append(np.stack([a[m], c_[m], b[m]], axis=1))
    faces = np.concatenate(tris, axis=0) if tris else np.zeros((0, 3), np.int64)

    used = np.unique(faces)
    remap = np.full(h * w, -1, dtype=np.int64)
    remap[used] = np.arange(used.size)
    rows, cols = np.divmod(used, w)
    vertices = np.column_stack([xs[cols], ys[rows], data.reshape(-1)[used]])
    return vertices, remap[faces].astype(np.uint32)


def simplify(vertices: np.ndarray, faces: np.ndarray, target_triangles: int, *, aggressiveness: float = 7.0):
    """Quadric decimation to at most ``target_triangles`` faces (no-op when already under)."""
    if faces.shape[0] <= target_triangles or target_triangles <= 0:
        return vertices, faces
    import fast_simplification

    reduction = 1.0 - target_triangles / faces.shape[0]
    v, f = fast_simplification.simplify(
        np.ascontiguousarray(vertices, dtype=np.float64),
        np.ascontiguousarray(faces, dtype=np.int64),
        target_reduction=float(reduction), agg=float(aggressiveness),
    )
    f = np.asarray(f, dtype=np.int64)
    # Drop degenerate faces the collapse may leave behind and compact vertices.
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    return compact(np.asarray(v, dtype=np.float64), f)


def compact(vertices: np.ndarray, faces: np.ndarray):
    """Remove vertices no face references."""
    used = np.unique(faces)
    if used.size == vertices.shape[0]:
        return vertices, faces.astype(np.uint32)
    remap = np.full(vertices.shape[0], -1, dtype=np.int64)
    remap[used] = np.arange(used.size)
    return vertices[used], remap[faces].astype(np.uint32)


def planar_uvs(vertices: np.ndarray, bounds) -> np.ndarray:
    """Texture coordinates for a north-up image covering ``bounds`` (glTF: v=0 is the image top)."""
    minx, miny, maxx, maxy = bounds
    u = (vertices[:, 0] - minx) / max(maxx - minx, 1e-12)
    v = (maxy - vertices[:, 1]) / max(maxy - miny, 1e-12)
    return np.clip(np.column_stack([u, v]), 0.0, 1.0).astype(np.float32)


def vertex_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    v = vertices.astype(np.float64)
    n = np.zeros_like(v)
    fn = np.cross(v[faces[:, 1]] - v[faces[:, 0]], v[faces[:, 2]] - v[faces[:, 0]])
    for k in range(3):
        np.add.at(n, faces[:, k], fn)
    length = np.linalg.norm(n, axis=1, keepdims=True)
    length[length == 0] = 1.0
    return (n / length).astype(np.float32)


def localize(vertices: np.ndarray, origin) -> np.ndarray:
    """Absolute float64 coordinates -> float32 offsets from ``origin``."""
    return (vertices - np.asarray(origin, dtype=np.float64)).astype(np.float32)


def mesh_stats(vertices: np.ndarray, faces: np.ndarray) -> dict:
    return {"vertices": int(vertices.shape[0]), "triangles": int(faces.shape[0])}
