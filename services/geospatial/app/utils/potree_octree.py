"""Server-side reader for a Potree 2.0 octree (``metadata.json`` +
``hierarchy.bin`` + ``octree.bin``) as written by PotreeConverter 2.x with
``--encoding UNCOMPRESSED`` (what ``/pointcloud/to-potree`` produces).

Why read the octree rather than the LAZ: the octree is a spatial index over
the *whole* cloud — every point is stored exactly once, in the level where the
Poisson sampling placed it — so a query over a small region (a profile
corridor) touches only the nodes whose boxes intersect it, at every level,
and reads them with byte ranges. The LAZ would need a full decompressing
scan per query (minutes for a billion points) and a full download when the
cloud lives in object storage. The octree already exists for any cloud the
viewer has opened.

Format (see ``OctreeLoader.js`` / ``DecoderWorker.js`` in Potree, mirrored by
potree-core, which the viewer uses):

* ``hierarchy.bin`` is a sequence of chunks. A chunk is ``n`` records of 22
  bytes — ``type u8, childMask u8, numPoints u32, byteOffset i64, byteSize
  i64`` — in breadth-first order starting with the chunk's own root. Type 0
  is an inner node, 1 a leaf, 2 a *proxy*: its subtree lives in another chunk
  at ``byteOffset``/``byteSize`` **of hierarchy.bin**; for types 0/1 the
  offset/size address the node's points in ``octree.bin``. The root chunk
  starts at 0 and is ``metadata.hierarchy.firstChunkSize`` bytes.
* Child ``i`` of a node halves the box: bit 0 selects the upper Z half,
  bit 1 the upper Y half, bit 2 the upper X half.
* A node's points are ``numPoints`` interleaved records of
  ``sum(attribute.size)`` bytes in the order of ``metadata.attributes``;
  ``position`` is three ``int32`` scaled by ``metadata.scale`` and shifted by
  ``metadata.offset``.

Both files are accessed through a :class:`RangeSource` — a local file
(``os.pread``) or an S3 object (``GetObject`` with ``Range``) — so the same
code serves the host cache and object storage. Reads are coalesced
(:func:`coalesce_ranges`) and issued concurrently, which matters for S3.
"""

from __future__ import annotations

import json
import os
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np

from app.utils import objectstore

NODE_RECORD = struct.Struct("<BBIqq")
NODE_RECORD_SIZE = NODE_RECORD.size  # 22
NODE_INNER, NODE_LEAF, NODE_PROXY = 0, 1, 2

# Range coalescing: merge requests closer than GAP, never exceeding MAX_REQUEST.
COALESCE_GAP = 1 * 1024 * 1024
MAX_REQUEST = 32 * 1024 * 1024
READ_WORKERS = 8

_NUMPY_TYPES = {
    "int8": "i1", "uint8": "u1", "int16": "i2", "uint16": "u2", "int32": "i4", "uint32": "u4",
    "int64": "i8", "uint64": "u8", "float": "f4", "double": "f8",
}


class OctreeError(ValueError):
    """Unreadable or inconsistent octree (message is safe to show)."""


# ------------------------------------------------------------------ sources


class RangeSource:
    """Random-access byte reads from a file or object."""

    def read(self, offset: int, size: int) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def size(self) -> int:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self):
        pass


class LocalRange(RangeSource):
    def __init__(self, path: str):
        if not os.path.isfile(path):
            raise OctreeError(f"file not found: {os.path.basename(path)}")
        self.path = path
        self._fd = os.open(path, os.O_RDONLY)
        self._size = os.fstat(self._fd).st_size

    def read(self, offset: int, size: int) -> bytes:
        out = bytearray()
        while len(out) < size:
            chunk = os.pread(self._fd, size - len(out), offset + len(out))
            if not chunk:
                break
            out += chunk
        return bytes(out)

    def size(self) -> int:
        return self._size

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


class ObjectRange(RangeSource):
    """Byte ranges of an ``s3://`` object (bucket allow-listed by ``objectstore``)."""

    def __init__(self, uri: str):
        self.bucket, self.key = objectstore.parse_uri(uri)
        self.uri = uri
        self._client = objectstore._client()
        self._size = None

    def size(self) -> int:
        if self._size is None:
            size = objectstore.object_size(self.uri)
            if size is None:
                raise OctreeError(f"object not found: {self.key.rsplit('/', 1)[-1]}")
            self._size = size
        return self._size

    def read(self, offset: int, size: int) -> bytes:
        if size <= 0:
            return b""
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=self.key, Range=f"bytes={offset}-{offset + size - 1}")
            return resp["Body"].read()
        except Exception as e:  # boto errors are many; the message stays generic
            code = getattr(e, "response", {}).get("Error", {}).get("Code", "") if hasattr(e, "response") else ""
            raise objectstore.ObjectStoreError(f"range read failed: {code or e.__class__.__name__}") from None


def open_source(path_or_uri: str) -> RangeSource:
    return ObjectRange(path_or_uri) if objectstore.is_object_uri(path_or_uri) else LocalRange(path_or_uri)


def coalesce_ranges(ranges: Iterable[tuple[int, int]], gap: int = COALESCE_GAP,
                    max_request: int = MAX_REQUEST) -> list[tuple[int, int]]:
    """Merge ``(offset, size)`` ranges that are within ``gap`` bytes of each other.

    Returns sorted, non-overlapping ``(offset, size)`` requests, none larger
    than ``max_request`` unless a single input range already is.
    """
    out: list[list[int]] = []
    for off, size in sorted(r for r in ranges if r[1] > 0):
        if out:
            cur_off, cur_size = out[-1]
            cur_end = cur_off + cur_size
            merged_end = max(cur_end, off + size)
            if off - cur_end <= gap and merged_end - cur_off <= max_request:
                out[-1][1] = merged_end - cur_off
                continue
        out.append([off, size])
    return [(o, s) for o, s in out]


def read_ranges(source: RangeSource, ranges: list[tuple[int, int]], workers: int = READ_WORKERS) -> list[bytes]:
    """Read coalesced ranges concurrently; results align with ``ranges``."""
    if not ranges:
        return []
    if len(ranges) == 1 or workers <= 1:
        return [source.read(o, s) for o, s in ranges]
    with ThreadPoolExecutor(max_workers=min(workers, len(ranges))) as pool:
        return list(pool.map(lambda r: source.read(r[0], r[1]), ranges))


# -------------------------------------------------------------------- nodes


@dataclass
class Node:
    name: str
    level: int
    bmin: tuple[float, float, float]
    bmax: tuple[float, float, float]
    node_type: int = NODE_PROXY
    num_points: int = 0
    byte_offset: int = 0  # into octree.bin (types 0/1)
    byte_size: int = 0
    hierarchy_offset: int = 0  # into hierarchy.bin (type 2)
    hierarchy_size: int = 0
    child_mask: int = 0
    children: list["Node"] = field(default_factory=list)

    @property
    def is_proxy(self) -> bool:
        return self.node_type == NODE_PROXY

    def child_box(self, index: int) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        mn, mx = list(self.bmin), list(self.bmax)
        for bit, axis in ((1, 2), (2, 1), (4, 0)):
            half = (self.bmax[axis] - self.bmin[axis]) / 2
            if index & bit:
                mn[axis] += half
            else:
                mx[axis] -= half
        return (mn[0], mn[1], mn[2]), (mx[0], mx[1], mx[2])


def parse_hierarchy_chunk(root: Node, data: bytes) -> list[Node]:
    """Fill ``root`` (the chunk's first record) and create its descendants.

    Mirrors Potree's ``parseHierarchy``: records are breadth-first; a record
    whose node was a proxy (``root`` when expanding a sub-chunk) receives its
    point data location; a record of type 2 receives a hierarchy location
    and has no children in this chunk. Returns every node created or filled
    (``root`` first).
    """
    if len(data) % NODE_RECORD_SIZE:
        raise OctreeError("hierarchy chunk size is not a multiple of 22 bytes")
    count = len(data) // NODE_RECORD_SIZE
    if count == 0:
        raise OctreeError("empty hierarchy chunk")
    nodes: list[Node] = [root]
    pos = 1
    for i in range(count):
        if i >= len(nodes):
            raise OctreeError("hierarchy chunk has more records than nodes")
        current = nodes[i]
        ntype, mask, num_points, offset, size = NODE_RECORD.unpack_from(data, i * NODE_RECORD_SIZE)
        if current.node_type == NODE_PROXY and i == 0:
            current.byte_offset, current.byte_size, current.num_points = offset, size, num_points
        elif ntype == NODE_PROXY:
            current.hierarchy_offset, current.hierarchy_size, current.num_points = offset, size, num_points
        else:
            current.byte_offset, current.byte_size, current.num_points = offset, size, num_points
        current.node_type = ntype
        current.child_mask = mask
        if ntype == NODE_PROXY:
            continue
        for child_index in range(8):
            if not (mask & (1 << child_index)):
                continue
            bmin, bmax = current.child_box(child_index)
            child = Node(name=f"{current.name}{child_index}", level=current.level + 1, bmin=bmin, bmax=bmax)
            current.children.append(child)
            if pos >= count:
                raise OctreeError("hierarchy chunk ends before its children")
            nodes.append(child)
            pos += 1
    return nodes


# ------------------------------------------------------------------- octree


def record_dtype(attributes: list[dict]) -> np.dtype:
    """NumPy structured dtype of one interleaved point record."""
    names, formats, offsets = [], [], []
    offset = 0
    for attr in attributes:
        name = attr.get("name")
        base = _NUMPY_TYPES.get(attr.get("type"))
        n = int(attr.get("numElements") or 1)
        size = int(attr.get("size") or 0)
        if not name or base is None or size <= 0:
            raise OctreeError(f"unsupported attribute {attr!r}")
        if np.dtype(base).itemsize * n != size:
            raise OctreeError(f"attribute {name} has size {size} but {n} x {attr.get('type')}")
        names.append(name)
        formats.append((f"<{base}", (n,)) if n > 1 else f"<{base}")
        offsets.append(offset)
        offset += size
    if not names:
        raise OctreeError("octree has no attributes")
    return np.dtype({"names": names, "formats": formats, "offsets": offsets, "itemsize": offset})


class Octree:
    """A Potree 2.0 octree open for reading. Use :func:`open_octree`."""

    def __init__(self, metadata: dict, hierarchy: RangeSource, points: RangeSource):
        self.metadata = metadata
        self._hierarchy = hierarchy
        self._points = points
        try:
            bb = metadata["boundingBox"]
            self.bmin = tuple(float(v) for v in bb["min"])
            self.bmax = tuple(float(v) for v in bb["max"])
            self.offset = tuple(float(v) for v in metadata["offset"])
            self.scale = tuple(float(v) for v in metadata["scale"])
            self.spacing = float(metadata["spacing"])
            self.first_chunk_size = int(metadata["hierarchy"]["firstChunkSize"])
            self.attributes = list(metadata["attributes"])
        except (KeyError, TypeError, ValueError) as e:
            raise OctreeError(f"metadata.json is missing {e}") from None
        if str(metadata.get("encoding") or "UNCOMPRESSED").upper() not in ("UNCOMPRESSED", "DEFAULT"):
            raise OctreeError(f"unsupported octree encoding {metadata.get('encoding')!r}")
        self.dtype = record_dtype(self.attributes)
        self.bytes_per_point = self.dtype.itemsize
        self.points_total = int(metadata.get("points") or 0)
        self.projection = metadata.get("projection") or ""
        self.root = Node(name="r", level=0, bmin=self.bmin, bmax=self.bmax, node_type=NODE_PROXY,
                         hierarchy_offset=0, hierarchy_size=self.first_chunk_size)
        self._root_loaded = False
        self.bytes_read = 0
        self.requests = 0

    def close(self):
        self._hierarchy.close()
        self._points.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def has_attribute(self, name: str) -> bool:
        return name in self.dtype.names

    def level_spacing(self, level: int) -> float:
        return self.spacing / (2 ** level)

    # -- hierarchy -----------------------------------------------------

    def _load_chunks(self, proxies: list[Node]):
        """Expand several proxy nodes with one coalesced batch of hierarchy reads."""
        ranges = [(p.hierarchy_offset, p.hierarchy_size) for p in proxies]
        merged = coalesce_ranges(ranges)
        blobs = read_ranges(self._hierarchy, merged)
        self.requests += len(merged)
        self.bytes_read += sum(len(b) for b in blobs)
        for proxy in proxies:
            for (off, size), blob in zip(merged, blobs):
                if off <= proxy.hierarchy_offset and proxy.hierarchy_offset + proxy.hierarchy_size <= off + size:
                    start = proxy.hierarchy_offset - off
                    chunk = blob[start:start + proxy.hierarchy_size]
                    if len(chunk) != proxy.hierarchy_size:
                        raise OctreeError(f"hierarchy.bin is truncated at node {proxy.name}")
                    parse_hierarchy_chunk(proxy, chunk)
                    break
            else:  # pragma: no cover - coalesce_ranges always covers every input
                raise OctreeError(f"hierarchy range for node {proxy.name} was not read")

    def select_nodes(self, intersects: Callable[[tuple, tuple], bool], max_level: int | None = None) -> list[Node]:
        """Every node (all levels) whose box satisfies ``intersects(bmin, bmax)``.

        Sub-hierarchies are loaded lazily and in waves (all proxies of the
        current frontier in one coalesced batch), so the number of round
        trips to storage is bounded by the hierarchy depth / step size, not
        by the number of nodes. Nodes with no points are skipped.
        """
        if not self._root_loaded:
            self._load_chunks([self.root])
            self._root_loaded = True
        selected: list[Node] = []
        frontier = [self.root] if intersects(self.root.bmin, self.root.bmax) else []
        while frontier:
            proxies = [n for n in frontier if n.is_proxy and (max_level is None or n.level <= max_level)]
            if proxies:
                self._load_chunks(proxies)
            next_frontier: list[Node] = []
            for node in frontier:
                if node.is_proxy:
                    continue  # beyond max_level: not expanded
                if node.num_points > 0 and node.byte_size > 0:
                    selected.append(node)
                if max_level is not None and node.level >= max_level:
                    continue
                for child in node.children:
                    if intersects(child.bmin, child.bmax):
                        next_frontier.append(child)
            frontier = next_frontier
        return selected

    # -- points --------------------------------------------------------

    def read_nodes(self, nodes: list[Node]) -> np.ndarray:
        """The interleaved records of ``nodes`` as one structured array."""
        if not nodes:
            return np.empty(0, dtype=self.dtype)
        ranges = [(n.byte_offset, n.byte_size) for n in nodes]
        merged = coalesce_ranges(ranges)
        blobs = read_ranges(self._points, merged)
        self.requests += len(merged)
        self.bytes_read += sum(len(b) for b in blobs)
        parts = []
        for node in nodes:
            for (off, size), blob in zip(merged, blobs):
                if off <= node.byte_offset and node.byte_offset + node.byte_size <= off + size:
                    start = node.byte_offset - off
                    raw = blob[start:start + node.byte_size]
                    expected = node.num_points * self.bytes_per_point
                    if len(raw) < expected:
                        raise OctreeError(f"octree.bin is truncated at node {node.name}")
                    parts.append(np.frombuffer(raw, dtype=self.dtype, count=node.num_points))
                    break
            else:  # pragma: no cover
                raise OctreeError(f"point range for node {node.name} was not read")
        return np.concatenate(parts) if len(parts) > 1 else parts[0]

    def positions(self, records: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(x, y, z)`` float64 arrays in the cloud's CRS."""
        pos = records["position"].astype(np.float64)
        return (pos[:, 0] * self.scale[0] + self.offset[0],
                pos[:, 1] * self.scale[1] + self.offset[1],
                pos[:, 2] * self.scale[2] + self.offset[2])


def load_metadata(path_or_uri: str) -> dict:
    src = open_source(path_or_uri)
    try:
        raw = src.read(0, src.size())
    finally:
        src.close()
    try:
        metadata = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise OctreeError(f"metadata.json is not valid JSON: {e}") from None
    if not isinstance(metadata, dict):
        raise OctreeError("metadata.json is not an object")
    return metadata


def open_octree(metadata: str, hierarchy: str, octree: str) -> Octree:
    """Open the three files (absolute paths or ``s3://`` URIs, in any mix)."""
    meta = load_metadata(metadata)
    return Octree(meta, open_source(hierarchy), open_source(octree))
