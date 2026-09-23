"""Minimal glTF 2.0 / GLB writer tuned for web delivery.

Why write it by hand: the sandbox has no compiled helpers (no Draco, no
meshoptimizer, no gltf-transform), and the glTF binary container is simple —
a JSON chunk plus one binary buffer. Size is controlled with what the format
gives for free:

- ``KHR_mesh_quantization``: positions as uint16 (dequantized by the node's
  translation/scale), UVs as normalized uint16 — half the geometry bytes of
  float32. Supported natively by three.js (r111+), Babylon.js, Cesium, Blender.
- ``KHR_materials_unlit``: photogrammetry textures already contain the
  lighting; unlit materials need no normals (another 12 bytes per vertex saved)
  and the viewer maps them to MeshBasicMaterial without tone mapping.
- 16-bit indices whenever a tile has < 65 536 vertices.

Georeferencing travels in two places, both read by the plugin runner:
``extensions.CESIUM_RTC.center`` (the ODM convention: the local frame's origin
in the projected CRS, so existing ODM tooling keeps working) and
``extras.webodm_georef`` with the CRS, origin, bounds and up axis (the
authoritative record). Axes follow ODM: X east, Y north, Z up.
"""

import json
import struct

import numpy as np

GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942

FLOAT = 5126
UNSIGNED_BYTE = 5121
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963

_TYPE_FOR_WIDTH = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}
_NP_FOR_COMPONENT = {FLOAT: np.float32, UNSIGNED_BYTE: np.uint8, UNSIGNED_SHORT: np.uint16,
                     UNSIGNED_INT: np.uint32}


def _pad(data: bytes, align: int = 4, fill: bytes = b"\x00") -> bytes:
    rem = len(data) % align
    return data + fill * (align - rem) if rem else data


class GlbBuilder:
    def __init__(self, generator: str = "WebODM 3D Reconstruction plugin"):
        self.buffer = bytearray()
        self.buffer_views = []
        self.accessors = []
        self.images = []
        self.textures = []
        self.samplers = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}]
        self.materials = []
        self.meshes = []
        self.nodes = []
        self.extensions_used = set()
        self.extensions_required = set()
        self.generator = generator

    # -- raw data ---------------------------------------------------------

    def add_buffer_view(self, data: bytes, target: int | None = None, byte_stride: int | None = None) -> int:
        self.buffer.extend(b"\x00" * ((-len(self.buffer)) % 4))  # 4-byte align the view start
        view = {"buffer": 0, "byteOffset": len(self.buffer), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        if byte_stride is not None:
            view["byteStride"] = byte_stride
        self.buffer.extend(data)
        self.buffer_views.append(view)
        return len(self.buffer_views) - 1

    def add_accessor(self, array: np.ndarray, component_type: int, *, normalized: bool = False,
                     target: int | None = None, minmax: bool = True) -> int:
        arr = np.ascontiguousarray(array, dtype=_NP_FOR_COMPONENT[component_type])
        if arr.ndim == 1:
            arr = arr[:, None]
        width = arr.shape[1]
        stride = arr.dtype.itemsize * width
        # Vertex attribute strides must be multiples of 4 bytes.
        byte_stride = None
        if target == ARRAY_BUFFER and stride % 4:
            padded = ((stride + 3) // 4) * 4
            raw = np.zeros((len(arr), padded), dtype=np.uint8)
            raw[:, :stride] = arr.view(np.uint8).reshape(len(arr), stride)
            data, byte_stride = raw.tobytes(), padded
        else:
            data = arr.tobytes()
        view = self.add_buffer_view(data, target, byte_stride)
        accessor = {
            "bufferView": view, "componentType": component_type, "count": int(len(arr)),
            "type": _TYPE_FOR_WIDTH[width],
        }
        if normalized:
            accessor["normalized"] = True
        if minmax:
            accessor["min"] = [_num(v) for v in arr.min(axis=0)]
            accessor["max"] = [_num(v) for v in arr.max(axis=0)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1

    # -- materials --------------------------------------------------------

    def add_texture(self, image_bytes: bytes, mime_type: str) -> int:
        view = self.add_buffer_view(image_bytes)
        self.images.append({"bufferView": view, "mimeType": mime_type})
        self.textures.append({"sampler": 0, "source": len(self.images) - 1})
        return len(self.textures) - 1

    def add_unlit_material(self, texture: int | None = None, base_color=(0.8, 0.8, 0.8, 1.0),
                           name: str | None = None) -> int:
        pbr = {"metallicFactor": 0.0, "roughnessFactor": 1.0}
        if texture is not None:
            pbr["baseColorTexture"] = {"index": texture, "texCoord": 0}
        else:
            pbr["baseColorFactor"] = [float(c) for c in base_color]
        material = {"pbrMetallicRoughness": pbr, "doubleSided": True,
                    "extensions": {"KHR_materials_unlit": {}}}
        if name:
            material["name"] = name
        self.extensions_used.add("KHR_materials_unlit")
        self.materials.append(material)
        return len(self.materials) - 1

    # -- geometry ---------------------------------------------------------

    def add_mesh(self, positions: np.ndarray, indices: np.ndarray, material: int,
                 uvs: np.ndarray | None = None, *, quantize: bool = True, name: str | None = None) -> int:
        """Add a single-primitive mesh in its own node; returns the node index.

        With ``quantize`` positions are stored as uint16 in the node's local
        box (dequantized by the node's translation/scale), otherwise as float32.
        """
        positions = np.asarray(positions, dtype=np.float64)
        attributes = {}
        node = {"mesh": len(self.meshes)}
        if name:
            node["name"] = name
        if quantize:
            lo = positions.min(axis=0)
            hi = positions.max(axis=0)
            span = np.where(hi - lo > 0, hi - lo, 1.0)
            scale = span / 65535.0
            q = np.rint((positions - lo) / scale).clip(0, 65535).astype(np.uint16)
            attributes["POSITION"] = self.add_accessor(q, UNSIGNED_SHORT, target=ARRAY_BUFFER)
            node["translation"] = [float(v) for v in lo]
            node["scale"] = [float(v) for v in scale]
            self.extensions_used.add("KHR_mesh_quantization")
            self.extensions_required.add("KHR_mesh_quantization")
        else:
            attributes["POSITION"] = self.add_accessor(positions.astype(np.float32), FLOAT, target=ARRAY_BUFFER)
        if uvs is not None:
            uv16 = np.rint(np.clip(np.asarray(uvs, dtype=np.float64), 0.0, 1.0) * 65535.0).astype(np.uint16)
            attributes["TEXCOORD_0"] = self.add_accessor(uv16, UNSIGNED_SHORT, normalized=True,
                                                         target=ARRAY_BUFFER)
        flat = np.asarray(indices, dtype=np.uint32).ravel()
        if len(positions) <= 65535:
            idx = self.add_accessor(flat.astype(np.uint16), UNSIGNED_SHORT, target=ELEMENT_ARRAY_BUFFER,
                                    minmax=False)
        else:
            idx = self.add_accessor(flat, UNSIGNED_INT, target=ELEMENT_ARRAY_BUFFER, minmax=False)
        self.meshes.append({"primitives": [{"attributes": attributes, "indices": idx, "material": material,
                                            "mode": 4}]})
        self.nodes.append(node)
        return len(self.nodes) - 1

    # -- output -----------------------------------------------------------

    def to_json(self, *, extras: dict | None = None, rtc_center=None) -> dict:
        root = {
            "asset": {"version": "2.0", "generator": self.generator},
            "scene": 0,
            "scenes": [{"nodes": list(range(len(self.nodes)))}],
            "nodes": self.nodes,
            "meshes": self.meshes,
            "accessors": self.accessors,
            "bufferViews": self.buffer_views,
            "buffers": [{"byteLength": len(_pad(bytes(self.buffer)))}],
        }
        if self.materials:
            root["materials"] = self.materials
        if self.textures:
            root["textures"] = self.textures
            root["images"] = self.images
            root["samplers"] = self.samplers
        extensions = {}
        if rtc_center is not None:
            extensions["CESIUM_RTC"] = {"center": [float(v) for v in rtc_center]}
            self.extensions_used.add("CESIUM_RTC")
        if extensions:
            root["extensions"] = extensions
        if self.extensions_used:
            root["extensionsUsed"] = sorted(self.extensions_used)
        if self.extensions_required:
            root["extensionsRequired"] = sorted(self.extensions_required)
        if extras:
            root["extras"] = extras
        return root

    def to_glb(self, **kwargs) -> bytes:
        return pack_glb(self.to_json(**kwargs), bytes(self.buffer))


def pack_glb(root: dict, binary: bytes) -> bytes:
    json_bytes = _pad(json.dumps(root, separators=(",", ":")).encode("utf-8"), fill=b" ")
    bin_bytes = _pad(binary)
    if root.get("buffers"):
        root["buffers"][0]["byteLength"] = len(bin_bytes)
        json_bytes = _pad(json.dumps(root, separators=(",", ":")).encode("utf-8"), fill=b" ")
    total = 12 + 8 + len(json_bytes) + (8 + len(bin_bytes) if bin_bytes else 0)
    out = bytearray(struct.pack("<III", GLB_MAGIC, 2, total))
    out += struct.pack("<II", len(json_bytes), JSON_CHUNK) + json_bytes
    if bin_bytes:
        out += struct.pack("<II", len(bin_bytes), BIN_CHUNK) + bin_bytes
    return bytes(out)


def parse_glb(data: bytes) -> tuple:
    """Return (json dict, binary chunk bytes or b'') of a GLB; raises ValueError."""
    if len(data) < 12 or struct.unpack_from("<I", data, 0)[0] != GLB_MAGIC:
        raise ValueError("not a GLB file (bad magic)")
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise ValueError(f"unsupported GLB version {version}")
    if length > len(data):
        raise ValueError("GLB is truncated")
    offset = 12
    root, binary = None, b""
    while offset + 8 <= length:
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset:offset + chunk_len]
        offset += chunk_len
        if chunk_type == JSON_CHUNK:
            root = json.loads(chunk.decode("utf-8"))
        elif chunk_type == BIN_CHUNK:
            binary = bytes(chunk)
    if root is None:
        raise ValueError("GLB has no JSON chunk")
    return root, binary


def georef_extras(crs, origin, bounds, bounds_4326, *, source: str, extra: dict | None = None) -> dict:
    """The ``extras.webodm_georef`` record."""
    from .sources import epsg_of

    epsg = epsg_of(crs) if crs is not None else None
    record = {
        "version": 1,
        "crs": f"EPSG:{epsg}" if epsg else None,
        "epsg": epsg,
        "wkt": crs.to_wkt() if crs is not None else None,
        "origin": [float(v) for v in origin],
        "bounds": [float(v) for v in bounds] if bounds else None,
        "bounds_4326": [float(v) for v in bounds_4326] if bounds_4326 else None,
        "up_axis": "Z",
        "units": "metre",
        "source": source,
    }
    if extra:
        record.update(extra)
    return {"webodm_georef": record}


def _num(v):
    return int(v) if np.issubdtype(type(v), np.integer) else float(v)
