"""A small Potree 2.0 octree *writer* for tests (uncompressed encoding).

PotreeConverter is not available on a plain test runner, and the profile
reader must be exercised against every structural feature of the format:
multi-level LOD storage (points at every level), the 22-byte hierarchy
records, hierarchy chunking with proxy nodes, child-box bit order and the
interleaved attribute records. This writer produces exactly that layout
(see ``app/utils/potree_octree.py`` for the format notes) from an arbitrary
set of points, so the tests can build scenes with a known ground, trees,
buildings, wires, noise and gaps and assert the numbers the profile reports.

The real converter is still the reference: ``test_profile.py`` also runs the
reader over a PotreeConverter-built octree when the binary is available.
"""

from __future__ import annotations

import json
import os
import struct

import numpy as np

from app.utils.potree_octree import NODE_RECORD, NODE_INNER, NODE_LEAF, NODE_PROXY, record_dtype

ATTRIBUTES = [
    {"name": "position", "description": "", "size": 12, "numElements": 3, "elementSize": 4, "type": "int32"},
    {"name": "intensity", "description": "", "size": 2, "numElements": 1, "elementSize": 2, "type": "uint16"},
    {"name": "classification", "description": "", "size": 1, "numElements": 1, "elementSize": 1, "type": "uint8"},
    {"name": "rgb", "description": "", "size": 6, "numElements": 3, "elementSize": 2, "type": "uint16"},
]


class _Node:
    def __init__(self, name, level, bmin, bmax):
        self.name, self.level, self.bmin, self.bmax = name, level, bmin, bmax
        self.points = None  # indices into the point arrays stored at this node
        self.children = {}  # child index -> _Node
        self.byte_offset = 0
        self.byte_size = 0
        self.chunk_offset = 0
        self.chunk_size = 0

    @property
    def is_leaf(self):
        return not self.children


def _child_box(bmin, bmax, index):
    mn, mx = list(bmin), list(bmax)
    for bit, axis in ((1, 2), (2, 1), (4, 0)):
        half = (bmax[axis] - bmin[axis]) / 2
        if index & bit:
            mn[axis] += half
        else:
            mx[axis] -= half
    return tuple(mn), tuple(mx)


def _build(xyz, idx, name, level, bmin, bmax, *, leaf_capacity, max_depth, lod_fraction, rng):
    node = _Node(name, level, bmin, bmax)
    if len(idx) <= leaf_capacity or level >= max_depth:
        node.points = idx
        return node
    # LOD: a fraction of the points stays at this level, the rest sinks to the children.
    keep_n = max(1, int(len(idx) * lod_fraction))
    perm = rng.permutation(len(idx))
    node.points = idx[perm[:keep_n]]
    rest = idx[perm[keep_n:]]
    mid = [(bmin[a] + bmax[a]) / 2 for a in range(3)]
    p = xyz[rest]
    child_index = ((p[:, 0] >= mid[0]).astype(int) << 2) | ((p[:, 1] >= mid[1]).astype(int) << 1) | (p[:, 2] >= mid[2]).astype(int)
    for ci in range(8):
        sel = rest[child_index == ci]
        if len(sel) == 0:
            continue
        cb_min, cb_max = _child_box(bmin, bmax, ci)
        node.children[ci] = _build(xyz, sel, f"{name}{ci}", level + 1, cb_min, cb_max, leaf_capacity=leaf_capacity,
                                   max_depth=max_depth, lod_fraction=lod_fraction, rng=rng)
    return node


def _chunk_records(root, step_size):
    """Records of the chunk rooted at ``root`` (BFS) and the roots of its sub-chunks."""
    records, sub_roots = [], []
    frontier = [root]
    while frontier:
        nxt = []
        for node in frontier:
            if node.level - root.level >= step_size and node is not root:
                records.append(("proxy", node))
                sub_roots.append(node)
                continue
            records.append(("node", node))
            for ci in sorted(node.children):
                nxt.append(node.children[ci])
        frontier = nxt
    return records, sub_roots


def write_octree(out_dir: str, xyz: np.ndarray, *, classification=None, intensity=None, rgb=None,
                 spacing: float | None = None, projection: str = "EPSG:32632", leaf_capacity: int = 500,
                 max_depth: int = 8, step_size: int = 2, lod_fraction: float = 0.1, scale: float = 0.001,
                 seed: int = 1, name: str = "fixture") -> dict:
    """Write ``metadata.json`` / ``hierarchy.bin`` / ``octree.bin`` for ``xyz`` (N, 3).

    ``step_size`` is deliberately small (2) so even a modest scene produces
    proxy nodes and several hierarchy chunks. Returns the metadata dict.
    """
    os.makedirs(out_dir, exist_ok=True)
    xyz = np.asarray(xyz, dtype=np.float64)
    n = len(xyz)
    classification = np.zeros(n, dtype=np.uint8) if classification is None else np.asarray(classification, dtype=np.uint8)
    intensity = np.zeros(n, dtype=np.uint16) if intensity is None else np.asarray(intensity, dtype=np.uint16)
    rgb = np.zeros((n, 3), dtype=np.uint16) if rgb is None else np.asarray(rgb, dtype=np.uint16)

    tight_min, tight_max = xyz.min(axis=0), xyz.max(axis=0)
    cube = float(max(tight_max - tight_min)) or 1.0
    bmin = tuple(float(v) for v in tight_min)
    bmax = tuple(float(v + cube) for v in tight_min)
    if spacing is None:
        spacing = cube / 20
    rng = np.random.default_rng(seed)
    root = _build(xyz, np.arange(n), "r", 0, bmin, bmax, leaf_capacity=leaf_capacity, max_depth=max_depth,
                  lod_fraction=lod_fraction, rng=rng)

    # Point data, one node after another.
    dtype = record_dtype(ATTRIBUTES)
    offset = bmin
    nodes = []
    stack = [root]
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(node.children.values())
    blob = bytearray()
    for node in nodes:
        idx = node.points
        rec = np.zeros(len(idx), dtype=dtype)
        rec["position"] = np.round((xyz[idx] - np.array(offset)) / scale).astype(np.int32)
        rec["intensity"] = intensity[idx]
        rec["classification"] = classification[idx]
        rec["rgb"] = rgb[idx]
        node.byte_offset = len(blob)
        node.byte_size = len(idx) * dtype.itemsize
        blob += rec.tobytes()
    with open(os.path.join(out_dir, "octree.bin"), "wb") as fh:
        fh.write(bytes(blob))

    # Hierarchy: lay out the chunks (sizes first, then offsets), then write.
    chunks = []  # (root, records, sub_roots)
    pending = [root]
    while pending:
        r = pending.pop(0)
        records, subs = _chunk_records(r, step_size)
        r.chunk_size = len(records) * NODE_RECORD.size
        chunks.append((r, records, subs))
        pending.extend(subs)
    pos = 0
    for r, records, _subs in chunks:
        r.chunk_offset = pos
        pos += r.chunk_size
    out = bytearray()
    for r, records, _subs in chunks:
        for kind, node in records:
            mask = 0
            for ci in node.children:
                mask |= 1 << ci
            if kind == "proxy":
                out += NODE_RECORD.pack(NODE_PROXY, mask, len(node.points), node.chunk_offset, node.chunk_size)
            else:
                ntype = NODE_LEAF if node.is_leaf else NODE_INNER
                out += NODE_RECORD.pack(ntype, mask, len(node.points), node.byte_offset, node.byte_size)
    with open(os.path.join(out_dir, "hierarchy.bin"), "wb") as fh:
        fh.write(bytes(out))

    hist = np.bincount(classification, minlength=256).tolist()
    depth = max(nd.level for nd in nodes)
    metadata = {
        "version": "2.0",
        "name": name,
        "description": "",
        "points": int(n),
        "projection": projection,
        "hierarchy": {"firstChunkSize": root.chunk_size, "stepSize": step_size, "depth": depth},
        "offset": list(offset),
        "scale": [scale, scale, scale],
        "spacing": spacing,
        "boundingBox": {"min": list(bmin), "max": list(bmax)},
        "encoding": "UNCOMPRESSED",
        "attributes": [
            {**ATTRIBUTES[0], "min": tight_min.tolist(), "max": tight_max.tolist()},
            {**ATTRIBUTES[1], "min": [int(intensity.min())], "max": [int(intensity.max())]},
            {**ATTRIBUTES[2], "min": [int(classification.min())], "max": [int(classification.max())], "histogram": hist},
            {**ATTRIBUTES[3], "min": rgb.min(axis=0).tolist(), "max": rgb.max(axis=0).tolist()},
        ],
    }
    with open(os.path.join(out_dir, "metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=1)
    return metadata


# ------------------------------------------------------------- test scenes


def scene(*, size: float = 100.0, density: float = 8.0, seed: int = 7, origin=(500000.0, 4500000.0), base_z: float = 100.0):
    """A synthetic survey: sloping ground, a tree row, a building, a wire, noise and a hole.

    Returns ``(xyz, classification, facts)`` where ``facts`` holds the
    parameters the tests assert against. Coordinates are UTM-like.
    """
    rng = np.random.default_rng(seed)
    ox, oy = origin
    n_ground = int(size * size * density)
    gx = rng.uniform(0, size, n_ground)
    gy = rng.uniform(0, size, n_ground)
    slope = 0.05  # ground rises 5 % along +x
    gz = base_z + slope * gx + rng.normal(0, 0.02, n_ground)
    pts = [np.stack([gx, gy, gz], axis=1)]
    cls = [np.full(n_ground, 2, dtype=np.uint8)]

    # Building: 20 x 20 m footprint at x 30..50, y 40..60, flat roof 8 m above local ground.
    nb = int(20 * 20 * density)
    bx = rng.uniform(30, 50, nb)
    by = rng.uniform(40, 60, nb)
    bz = base_z + slope * bx + 8.0 + rng.normal(0, 0.02, nb)
    pts.append(np.stack([bx, by, bz], axis=1))
    cls.append(np.full(nb, 6, dtype=np.uint8))
    # No ground points under the building (occluded).
    under = (gx >= 30) & (gx <= 50) & (gy >= 40) & (gy <= 60)
    pts[0] = pts[0][~under]
    cls[0] = cls[0][~under]

    # Trees: a row of 10 m-tall canopies (class 5) along y = 20, x 60..90.
    nt = int(30 * 6 * density)
    tx = rng.uniform(60, 90, nt)
    ty = rng.uniform(17, 23, nt)
    tz = base_z + slope * tx + rng.uniform(4.0, 10.0, nt)
    pts.append(np.stack([tx, ty, tz], axis=1))
    cls.append(np.full(nt, 5, dtype=np.uint8))

    # Wire (class 14) along y = 70 at 12 m above ground, sparse.
    nw = 200
    wx = np.linspace(0, size, nw)
    wy = np.full(nw, 70.0)
    wz = base_z + slope * wx + 12.0
    pts.append(np.stack([wx, wy, wz], axis=1))
    cls.append(np.full(nw, 14, dtype=np.uint8))

    # Noise (class 7): a few points 30 m above everything.
    nn = 20
    nx = rng.uniform(0, size, nn)
    ny = rng.uniform(0, size, nn)
    nz = base_z + 30.0 + rng.uniform(0, 5, nn)
    pts.append(np.stack([nx, ny, nz], axis=1))
    cls.append(np.full(nn, 7, dtype=np.uint8))

    xyz = np.concatenate(pts)
    classification = np.concatenate(cls)
    # A hole with no data at all: x 10..15, y 75..85 (e.g. water / unreconstructed).
    hole = (xyz[:, 0] >= 10) & (xyz[:, 0] <= 15) & (xyz[:, 1] >= 75) & (xyz[:, 1] <= 85)
    xyz, classification = xyz[~hole], classification[~hole]
    xyz[:, 0] += ox
    xyz[:, 1] += oy
    facts = {"origin": origin, "size": size, "slope": slope, "base_z": base_z, "building": (30, 50, 40, 60, 8.0),
             "trees": (60, 90, 17, 23, 4.0, 10.0), "wire": (70.0, 12.0), "hole": (10, 15, 75, 85), "noise_z": base_z + 30}
    return xyz, classification, facts
