"""Vectorized Right-Triangulated Irregular Network (RTIN) meshing.

A numpy re-formulation of the "Martini" algorithm (Mapbox, ISC licence;
Evans et al., *Right-Triangulated Irregular Networks*, 1997). The sandbox has no
compiler and no pip, so the Cython/C++ ports (pymartini, pydelatin) cannot be
used; instead every level of the implicit triangle binary tree is processed as
a whole with array operations, which keeps the pure-Python overhead to a few
dozen numpy calls per level.

Terminology (grid of ``size = 2**k + 1`` nodes per side):

- ``compute_errors`` builds the *error map*: for every grid node the largest
  vertical deviation that would be introduced anywhere below it in the tree if
  the triangle whose hypotenuse midpoint it is were *not* split. Errors are
  accumulated bottom-up, so a coarse triangle's error is the max over all of
  its descendants — that is what makes the later top-down cut consistent.
- ``extract`` walks the tree top-down (one numpy pass per level) and emits the
  triangles that do not need splitting for a given ``max_error``. It can also
  force splits so that every triangle fits inside a square tile (needed to give
  each tile its own texture) and it drops triangles touching invalid nodes.

Grid coordinates are (x = column, y = row); the mesher converts to world space.
"""

import math

import numpy as np


def is_valid_size(size: int) -> bool:
    n = size - 1
    return size >= 3 and n & (n - 1) == 0


def next_valid_size(n: int) -> int:
    """Smallest ``2**k + 1`` that is >= n (and >= 3)."""
    n = max(int(n), 3)
    return (1 << math.ceil(math.log2(n - 1))) + 1


# Triangles per numpy pass; bounds the temporaries of the deepest levels
# (a 2049² grid has 4.2 M triangles at its finest level) to ~100 MB.
CHUNK = 1 << 20


def _level_corners(level: int, tile: int, start: int = 0, stop: int | None = None):
    """Corner coordinates a, b (hypotenuse ends) of triangles ``start:stop`` at ``level``.

    Level 1 is the two halves of the square; each subsequent level splits every
    triangle at the midpoint of its hypotenuse. Mirrors Martini's implicit
    binary-tree indexing so the same node is the midpoint for exactly one level.
    """
    n = 1 << level
    stop = n if stop is None else min(stop, n)
    ids = np.arange(n + start, n + stop, dtype=np.int64)
    odd = (ids & 1).astype(bool)
    zero = np.zeros(len(ids), dtype=np.int32)
    full = np.full(len(ids), tile, dtype=np.int32)
    # odd id: bottom-left triangle a=(0,0) b=(t,t) c=(t,0); even: a=(t,t) b=(0,0) c=(0,t)
    ax = np.where(odd, zero, full)
    ay = ax.copy()
    bx = np.where(odd, full, zero)
    by = bx.copy()
    cx = np.where(odd, full, zero)
    cy = np.where(odd, zero, full)
    ids >>= 1
    for _ in range(level - 1):
        mx = (ax + bx) >> 1
        my = (ay + by) >> 1
        left = (ids & 1).astype(bool)
        nax = np.where(left, cx, bx)
        nay = np.where(left, cy, by)
        nbx = np.where(left, ax, cx)
        nby = np.where(left, ay, cy)
        ax, ay, bx, by = nax, nay, nbx, nby
        cx, cy = mx, my
        ids >>= 1
    return ax, ay, bx, by


def compute_errors(fields, size: int) -> np.ndarray:
    """Error map (``size`` x ``size`` float32) for one or more scalar fields.

    ``fields`` are ``size`` x ``size`` arrays without NaNs. Passing several
    fields (e.g. the heights and a scaled validity mask) yields the element-wise
    maximum of their error maps, so a triangle is split when *any* field says so.
    """
    if not is_valid_size(size):
        raise ValueError(f"grid size must be 2^k+1, got {size}")
    flats = [np.ascontiguousarray(f, dtype=np.float32).ravel() for f in fields]
    for f in flats:
        if f.size != size * size:
            raise ValueError("field shape does not match grid size")
    tile = size - 1
    levels = 2 * int(round(math.log2(tile)))
    errors = np.zeros(size * size, dtype=np.float32)

    for level in range(levels, 0, -1):
        count = 1 << level
        # Nodes are the midpoint of exactly one level's triangles, so the
        # children's errors read here are final and chunk order is irrelevant.
        for start in range(0, count, CHUNK):
            ax, ay, bx, by = _level_corners(level, tile, start, start + CHUNK)
            mx = (ax + bx) >> 1
            my = (ay + by) >> 1
            cx = mx + my - ay
            cy = my + ax - mx
            a_idx = ay * size + ax
            b_idx = by * size + bx
            mid = my * size + mx

            err = None
            for f in flats:
                e = np.abs((f[a_idx] + f[b_idx]) * 0.5 - f[mid])
                err = e if err is None else np.maximum(err, e)
            if level < levels:  # accumulate the children's (already final) errors
                lc = ((ay + cy) >> 1) * size + ((ax + cx) >> 1)
                rc = ((by + cy) >> 1) * size + ((bx + cx) >> 1)
                err = np.maximum(err, np.maximum(errors[lc], errors[rc]))
            np.maximum.at(errors, mid, err)
    return errors.reshape(size, size)


def extract(errors: np.ndarray, max_error: float, tile_cells: int | None = None,
            valid: np.ndarray | None = None) -> np.ndarray:
    """Triangles for ``max_error`` as an ``(M, 6)`` int32 array ``ax ay bx by cx cy``.

    ``tile_cells`` forces splitting until every triangle's bounding box lies
    within one ``tile_cells`` x ``tile_cells`` square (tiles are aligned to the
    grid origin; ``tile_cells`` must divide ``size - 1``). ``valid`` (bool grid)
    drops every triangle with an invalid corner; pair it with a validity field
    in ``compute_errors`` so the cut follows the data boundary at full detail.
    """
    size = errors.shape[0]
    top = size - 1
    ef = np.ascontiguousarray(errors, dtype=np.float32).ravel()
    tris = np.array([[0, 0, top, top, top, 0], [top, top, 0, 0, 0, top]], dtype=np.int32)
    leaves = []
    while len(tris):
        ax, ay, bx, by, cx, cy = (tris[:, i] for i in range(6))
        mx = (ax + bx) >> 1
        my = (ay + by) >> 1
        leg = np.abs(ax - cx) + np.abs(ay - cy)
        split = (leg > 1) & (ef[my * size + mx] > max_error)
        if tile_cells and tile_cells < top:
            minx = np.minimum(np.minimum(ax, bx), cx)
            maxx = np.maximum(np.maximum(ax, bx), cx)
            miny = np.minimum(np.minimum(ay, by), cy)
            maxy = np.maximum(np.maximum(ay, by), cy)
            fits = (minx // tile_cells == (maxx - 1) // tile_cells) & \
                   (miny // tile_cells == (maxy - 1) // tile_cells)
            split |= ~fits
        leaves.append(tris[~split])
        if not split.any():
            break
        s = tris[split]
        smx = mx[split]
        smy = my[split]
        left = np.stack([s[:, 4], s[:, 5], s[:, 0], s[:, 1], smx, smy], axis=1)
        right = np.stack([s[:, 2], s[:, 3], s[:, 4], s[:, 5], smx, smy], axis=1)
        tris = np.concatenate([left, right])
    out = np.concatenate(leaves) if leaves else np.zeros((0, 6), dtype=np.int32)
    if valid is not None and len(out):
        vf = np.ascontiguousarray(valid, dtype=bool).ravel()
        ok = vf[out[:, 1] * size + out[:, 0]] & vf[out[:, 3] * size + out[:, 2]] & \
            vf[out[:, 5] * size + out[:, 4]]
        out = out[ok]
    return out


def choose_error(errors: np.ndarray, budget: int, floor: float = 0.005,
                 tile_cells: int | None = None, valid: np.ndarray | None = None,
                 z_range: float | None = None) -> float:
    """Smallest max_error (>= ``floor``) whose mesh has at most ``budget`` triangles.

    Descends geometrically from the height range (cheap, few triangles) and
    bisects the last interval, so the expensive high-detail extractions are
    only ever run close to the budget and a full-resolution mesh (millions of
    triangles) is never materialised just to be counted.
    """
    def count(e):
        return len(extract(errors, e, tile_cells, valid))

    hi = float(z_range if z_range and z_range > 0 else np.nanmax(errors) or 1.0)
    hi = max(hi, floor)
    if count(hi) > budget:
        # Even the coarsest cut exceeds the budget (only possible via forced
        # tile splits or a tiny budget): return the height range and let the
        # caller warn.
        return hi
    lo = floor
    while hi / 2 >= lo:
        if count(hi / 2) <= budget:
            hi /= 2
        else:
            lo = hi / 2
            break
    else:
        return floor if count(floor) <= budget else hi
    if hi <= floor:
        return floor
    for _ in range(6):
        mid = math.sqrt(lo * hi)
        if count(mid) <= budget:
            hi = mid
        else:
            lo = mid
    return hi
