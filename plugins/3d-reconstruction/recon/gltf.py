"""Minimal glTF 2.0 binary (GLB) writer and reader.

Why not a library: the output needs exactly one buffer, unlit textured
materials, optional Draco compression with a known attribute-id layout, the
``CESIUM_RTC`` extension and a custom ``extras`` block — about two hundred
lines of plain struct/JSON work, with no scene-graph abstraction to fight.

Output conventions (the same as ODM's ``odm_textured_model_geo.glb`` so the
platform's viewer treats both alike):

* Coordinates are the task's projected CRS (X east, Y north, Z up, metres),
  stored **relative to an origin** so float32 stays precise; the origin is the
  ``CESIUM_RTC.center`` and is repeated in ``asset.extras.webodm_georef``
  together with the EPSG code and the absolute bounds. The extension is
  listed in ``extensionsUsed`` only, so loaders that ignore it still work.
* Materials are ``KHR_materials_unlit`` (photogrammetry colour is already
  lit) and double sided.
* Draco: ``KHR_draco_mesh_compression`` is listed as *required* when used.
  The glTF ``attributes`` map (semantic -> Draco unique id) is read back
  from the encoded blob (``draco_attribute_ids``), never assumed; texture
  coordinates travel as a generic uint16 attribute because DracoPy cannot
  quantise ``tex_coord``. The layout was cross-checked with the Draco
  JavaScript decoder the viewer ships (``frontend/public/draco``).
"""

import json
import os
import struct
import zipfile

import numpy as np

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

FLOAT = 5126
UNSIGNED_BYTE = 5121
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
MODE_POINTS = 0
MODE_TRIANGLES = 4

_COMPONENT_DTYPES = {
    5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32,
}
_TYPE_SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
_DTYPE_COMPONENTS = {np.dtype(v): k for k, v in _COMPONENT_DTYPES.items()}

GEOREF_KEY = "webodm_georef"
GEOREF_VERSION = 1


def _pad(data: bytes, align: int = 4, fill: bytes = b"\x00") -> bytes:
    rem = len(data) % align
    return data if rem == 0 else data + fill * (align - rem)


# --------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------

class GlbBuilder:
    """Accumulates accessors, images, materials and meshes; ``build()`` emits bytes."""

    def __init__(self, generator: str = "webodm-3d-reconstruction"):
        self.gltf = {
            "asset": {"version": "2.0", "generator": generator},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [],
            "meshes": [],
            "materials": [],
            "textures": [],
            "images": [],
            "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}],
            "accessors": [],
            "bufferViews": [],
            "buffers": [{"byteLength": 0}],
        }
        self._chunks: list[bytes] = []
        self._offset = 0
        self._extensions_used: set[str] = set()
        self._extensions_required: set[str] = set()

    # -- buffers -----------------------------------------------------------

    def add_buffer_view(self, data: bytes, target: int | None = None, byte_stride: int | None = None) -> int:
        data = bytes(data)
        view = {"buffer": 0, "byteOffset": self._offset, "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        if byte_stride is not None:
            view["byteStride"] = byte_stride
        padded = _pad(data)
        self._chunks.append(padded)
        self._offset += len(padded)
        self.gltf["bufferViews"].append(view)
        return len(self.gltf["bufferViews"]) - 1

    def add_accessor(self, array: np.ndarray, *, target: int | None = None, normalized: bool = False,
                     minmax: bool = False, with_data: bool = True) -> int:
        """Register ``array`` (N,) or (N, C) as an accessor; ``with_data=False`` omits the bufferView (Draco)."""
        array = np.ascontiguousarray(array)
        if array.ndim == 1:
            array = array.reshape(-1, 1)
        if array.dtype not in _DTYPE_COMPONENTS:
            raise ValueError(f"unsupported accessor dtype {array.dtype}")
        count, comps = array.shape
        type_name = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}[comps]
        accessor = {"componentType": _DTYPE_COMPONENTS[array.dtype], "count": int(count), "type": type_name}
        if normalized:
            accessor["normalized"] = True
        if minmax and count:
            accessor["min"] = [float(v) if array.dtype.kind == "f" else int(v) for v in array.min(axis=0)]
            accessor["max"] = [float(v) if array.dtype.kind == "f" else int(v) for v in array.max(axis=0)]
        if with_data:
            stride = None
            if target == ARRAY_BUFFER and comps > 1:
                stride = int(array.dtype.itemsize * comps)
                if stride % 4:
                    stride = None  # glTF requires 4-byte aligned strides; let it be tightly packed
            accessor["bufferView"] = self.add_buffer_view(array.tobytes(), target=target, byte_stride=stride)
            accessor["byteOffset"] = 0
        self.gltf["accessors"].append(accessor)
        return len(self.gltf["accessors"]) - 1

    # -- textures / materials ---------------------------------------------

    def add_image(self, data: bytes, mime_type: str) -> int:
        view = self.add_buffer_view(data)
        self.gltf["images"].append({"bufferView": view, "mimeType": mime_type})
        return len(self.gltf["images"]) - 1

    def add_texture(self, image_index: int) -> int:
        self.gltf["textures"].append({"sampler": 0, "source": image_index})
        return len(self.gltf["textures"]) - 1

    def add_material(self, texture_index: int | None = None, base_color=(1.0, 1.0, 1.0, 1.0),
                     unlit: bool = True, double_sided: bool = True, name: str | None = None) -> int:
        pbr = {"baseColorFactor": [float(c) for c in base_color], "metallicFactor": 0.0, "roughnessFactor": 1.0}
        if texture_index is not None:
            pbr["baseColorTexture"] = {"index": texture_index, "texCoord": 0}
        material = {"pbrMetallicRoughness": pbr, "doubleSided": bool(double_sided)}
        if name:
            material["name"] = name
        if unlit:
            material["extensions"] = {"KHR_materials_unlit": {}}
            self._extensions_used.add("KHR_materials_unlit")
        self.gltf["materials"].append(material)
        return len(self.gltf["materials"]) - 1

    # -- geometry ----------------------------------------------------------

    def add_primitive(self, positions: np.ndarray, *, indices: np.ndarray | None = None,
                      uvs: np.ndarray | None = None, colors: np.ndarray | None = None,
                      normals: np.ndarray | None = None, material: int | None = None,
                      mode: int = MODE_TRIANGLES, draco: dict | None = None) -> dict:
        """Return a primitive dict. ``draco`` = {"bits": int, "level": int} compresses it.

        ``positions`` float32 (N,3); ``uvs`` float32 (N,2); ``colors`` uint8
        (N,3); ``normals`` float32 (N,3); ``indices`` uint16/uint32 (M,3) or (M,).
        """
        positions = np.ascontiguousarray(positions, dtype=np.float32)
        if indices is not None:
            indices = np.ascontiguousarray(indices).reshape(-1)
            indices = indices.astype(np.uint16 if positions.shape[0] <= 65535 else np.uint32, copy=False)
        prim = {"mode": mode, "attributes": {}}
        if material is not None:
            prim["material"] = material

        if draco:
            blob, ids = encode_draco(positions, indices, uvs=uvs, colors=colors, normals=normals,
                                     bits=draco.get("bits", 14), level=draco.get("level", 7))
            view = self.add_buffer_view(blob)
            prim["extensions"] = {"KHR_draco_mesh_compression": {"bufferView": view, "attributes": ids}}
            self._extensions_used.add("KHR_draco_mesh_compression")
            self._extensions_required.add("KHR_draco_mesh_compression")
            prim["attributes"]["POSITION"] = self.add_accessor(positions, minmax=True, with_data=False)
            if normals is not None:
                prim["attributes"]["NORMAL"] = self.add_accessor(np.asarray(normals, dtype=np.float32), with_data=False)
            if uvs is not None:
                # Matches the generic uint16 attribute encode_draco wrote (normalized VEC2).
                prim["attributes"]["TEXCOORD_0"] = self.add_accessor(
                    np.zeros((positions.shape[0], 2), np.uint16), normalized=True, with_data=False)
            if colors is not None:
                prim["attributes"]["COLOR_0"] = self.add_accessor(np.asarray(colors, dtype=np.uint8), normalized=True, with_data=False)
            if indices is not None:
                prim["indices"] = self.add_accessor(indices, with_data=False)
            return prim

        prim["attributes"]["POSITION"] = self.add_accessor(positions, target=ARRAY_BUFFER, minmax=True)
        if normals is not None:
            prim["attributes"]["NORMAL"] = self.add_accessor(np.asarray(normals, dtype=np.float32), target=ARRAY_BUFFER)
        if uvs is not None:
            prim["attributes"]["TEXCOORD_0"] = self.add_accessor(np.asarray(uvs, dtype=np.float32), target=ARRAY_BUFFER)
        if colors is not None:
            # uint8 RGB is 3 bytes/vertex; pad to RGBA so the stride is 4-aligned.
            c = np.asarray(colors, dtype=np.uint8)
            if c.shape[1] == 3:
                c = np.concatenate([c, np.full((c.shape[0], 1), 255, np.uint8)], axis=1)
            prim["attributes"]["COLOR_0"] = self.add_accessor(c, target=ARRAY_BUFFER, normalized=True)
        if indices is not None:
            prim["indices"] = self.add_accessor(indices, target=ELEMENT_ARRAY_BUFFER)
        return prim

    def add_mesh(self, primitives: list[dict], name: str | None = None) -> int:
        mesh = {"primitives": primitives}
        if name:
            mesh["name"] = name
        self.gltf["meshes"].append(mesh)
        return len(self.gltf["meshes"]) - 1

    def add_node(self, mesh_index: int, name: str | None = None) -> int:
        node = {"mesh": mesh_index}
        if name:
            node["name"] = name
        self.gltf["nodes"].append(node)
        index = len(self.gltf["nodes"]) - 1
        self.gltf["scenes"][0]["nodes"].append(index)
        return index

    # -- georeferencing ------------------------------------------------------

    def set_georef(self, georef: dict | None):
        """Record the origin / CRS: ``CESIUM_RTC`` + ``asset.extras.webodm_georef``."""
        if not georef:
            return
        origin = georef.get("origin")
        if origin is not None and georef.get("georeferenced", True):
            self.gltf["extensions"] = {**self.gltf.get("extensions", {}),
                                       "CESIUM_RTC": {"center": [float(v) for v in origin]}}
            self._extensions_used.add("CESIUM_RTC")
        extras = self.gltf["asset"].setdefault("extras", {})
        extras[GEOREF_KEY] = {"version": GEOREF_VERSION, "up_axis": "Z", "units": "metre", **georef}

    def set_extras(self, **extras):
        self.gltf["asset"].setdefault("extras", {}).update(extras)

    # -- output ------------------------------------------------------------

    def build(self) -> bytes:
        if self._extensions_used:
            self.gltf["extensionsUsed"] = sorted(self._extensions_used)
        if self._extensions_required:
            self.gltf["extensionsRequired"] = sorted(self._extensions_required)
        bin_chunk = b"".join(self._chunks)
        self.gltf["buffers"][0]["byteLength"] = len(bin_chunk)
        for key in ("materials", "textures", "images", "nodes", "meshes"):
            if not self.gltf[key]:
                del self.gltf[key]
        json_chunk = _pad(json.dumps(self.gltf, separators=(",", ":")).encode("utf-8"), fill=b" ")
        total = 12 + 8 + len(json_chunk) + (8 + len(bin_chunk) if bin_chunk else 0)
        out = [struct.pack("<4sII", GLB_MAGIC, 2, total), struct.pack("<II", len(json_chunk), CHUNK_JSON), json_chunk]
        if bin_chunk:
            out += [struct.pack("<II", len(bin_chunk), CHUNK_BIN), bin_chunk]
        return b"".join(out)

    def write(self, path: str) -> int:
        data = self.build()
        with open(path, "wb") as f:
            f.write(data)
        return len(data)


# --------------------------------------------------------------------------
# Draco
# --------------------------------------------------------------------------

# Draco attribute_type codes (draco::GeometryAttribute::Type).
_DRACO_POSITION, _DRACO_NORMAL, _DRACO_COLOR, _DRACO_TEX_COORD, _DRACO_GENERIC = 0, 1, 2, 3, 4
# Unique id we give the generic (uint16) texture-coordinate attribute.
_DRACO_UV_ID = 0
UV_MAX = 65535


def quantize_uvs(uvs: np.ndarray) -> np.ndarray:
    """float [0,1] texture coordinates -> uint16 (glTF ``normalized`` VEC2)."""
    return np.clip(np.round(np.asarray(uvs, dtype=np.float64) * UV_MAX), 0, UV_MAX).astype(np.uint16)


def draco_attribute_ids(blob: bytes) -> dict:
    """glTF semantic -> Draco unique id, read back from the encoded blob.

    Derived from the data rather than from DracoPy's (undocumented) insertion
    order, so a library upgrade cannot silently mis-map attributes.
    """
    import DracoPy

    ids = {}
    for att in DracoPy.decode(bytes(blob)).attributes:
        t, uid, comps = att["attribute_type"], int(att["unique_id"]), int(att["num_components"])
        if t == _DRACO_POSITION:
            ids["POSITION"] = uid
        elif t == _DRACO_NORMAL:
            ids["NORMAL"] = uid
        elif t == _DRACO_COLOR:
            ids["COLOR_0"] = uid
        elif t == _DRACO_TEX_COORD or (t == _DRACO_GENERIC and comps == 2):
            ids["TEXCOORD_0"] = uid
    return ids


def encode_draco(positions, indices=None, *, uvs=None, colors=None, normals=None, bits=14, level=7):
    """Draco-encode one primitive. Returns ``(blob, {semantic: unique_id})``.

    Texture coordinates go in as a *generic* uint16 attribute: DracoPy stores
    ``tex_coord`` as unquantised float32, which doubles the size of a textured
    mesh; 16-bit normalised UVs are exact to 1/65535 of the atlas and glTF
    accepts them natively (``componentType`` 5123, ``normalized``).
    """
    import DracoPy

    faces = None if indices is None else np.ascontiguousarray(indices, dtype=np.uint32).reshape(-1, 3)
    generic = None
    if uvs is not None:
        if faces is None:
            raise ValueError("point primitives cannot carry texture coordinates")
        generic = {_DRACO_UV_ID: quantize_uvs(uvs)}
    blob = DracoPy.encode(
        np.ascontiguousarray(positions, dtype=np.float32), faces,
        quantization_bits=int(bits), compression_level=int(level),
        colors=None if colors is None else np.ascontiguousarray(colors, dtype=np.uint8),
        normals=None if normals is None else np.ascontiguousarray(normals, dtype=np.float64),
        generic_attributes=generic,
    )
    ids = draco_attribute_ids(blob)
    expected = {"POSITION"} | ({"TEXCOORD_0"} if uvs is not None else set()) \
        | ({"COLOR_0"} if colors is not None else set()) | ({"NORMAL"} if normals is not None else set())
    if set(ids) != expected:
        raise RuntimeError(f"Draco attribute layout mismatch: expected {sorted(expected)}, got {sorted(ids)}")
    return blob, ids


def decode_draco(blob: bytes, attribute_ids: dict | None = None, uv_divisor: float = UV_MAX) -> dict:
    """positions/indices/uvs/normals/colors from a Draco blob (standard or generic integer UVs).

    ``attribute_ids`` is the primitive's ``KHR_draco_mesh_compression.attributes``
    map (used to recognise a generic UV attribute); ``uv_divisor`` is what an
    integer UV attribute is normalised by (from the glTF accessor's type).
    """
    import DracoPy

    mesh = DracoPy.decode(bytes(blob))
    out = {"positions": np.asarray(mesh.points, dtype=np.float32).reshape(-1, 3)}
    faces = getattr(mesh, "faces", None)
    if faces is not None and len(faces):
        out["indices"] = np.asarray(faces, dtype=np.uint32).reshape(-1, 3)
    uv_id = (attribute_ids or {}).get("TEXCOORD_0")
    for att in mesh.attributes:
        t, uid, data = att["attribute_type"], int(att["unique_id"]), np.asarray(att["data"])
        if t == _DRACO_NORMAL:
            out["normals"] = data.astype(np.float32)
        elif t == _DRACO_COLOR:
            out["colors"] = data[:, :3].astype(np.uint8) if data.dtype.kind in "ui" else \
                np.clip(data[:, :3] * 255 + 0.5, 0, 255).astype(np.uint8)
        elif t == _DRACO_TEX_COORD or (t == _DRACO_GENERIC and data.ndim == 2 and data.shape[1] == 2
                                       and (uv_id is None or uv_id == uid)):
            if data.dtype.kind in "ui":
                out["uvs"] = (data.astype(np.float32) / float(uv_divisor)).astype(np.float32)
            else:
                out["uvs"] = data.astype(np.float32)
    return out


# --------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------

class GltfDocument:
    """A parsed glTF/GLB: JSON plus resolved binary buffers and image bytes."""

    def __init__(self, gltf: dict, buffers: list[bytes], resources: dict[str, bytes] | None = None):
        self.gltf = gltf
        self.buffers = buffers
        self.resources = resources or {}

    # -- loading -------------------------------------------------------------

    @classmethod
    def from_glb_bytes(cls, data: bytes) -> "GltfDocument":
        if len(data) < 12 or data[:4] != GLB_MAGIC:
            raise ValueError("not a GLB file")
        _, version, length = struct.unpack_from("<4sII", data, 0)
        if version != 2:
            raise ValueError(f"unsupported GLB version {version}")
        offset, gltf, buffers = 12, None, []
        while offset + 8 <= min(length, len(data)):
            chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
            chunk = data[offset + 8: offset + 8 + chunk_len]
            if chunk_type == CHUNK_JSON:
                gltf = json.loads(chunk.decode("utf-8"))
            elif chunk_type == CHUNK_BIN:
                buffers.append(bytes(chunk))
            offset += 8 + chunk_len
        if gltf is None:
            raise ValueError("GLB has no JSON chunk")
        return cls(gltf, buffers)

    @classmethod
    def from_gltf_json(cls, text: str, resources: dict[str, bytes]) -> "GltfDocument":
        gltf = json.loads(text)
        buffers = []
        for buf in gltf.get("buffers", []):
            uri = buf.get("uri")
            if uri is None:
                raise ValueError(".gltf buffer without uri")
            buffers.append(_resolve_uri(uri, resources))
        return cls(gltf, buffers, resources)

    @classmethod
    def open(cls, path: str) -> "GltfDocument":
        """Open a ``.glb``, a ``.gltf`` (siblings on disk) or a zip holding either."""
        lower = path.lower()
        if lower.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                names = [n for n in zf.namelist() if not n.endswith("/")]
                glb = [n for n in names if n.lower().endswith(".glb")]
                gltf = [n for n in names if n.lower().endswith(".gltf")]
                if glb:
                    return cls.from_glb_bytes(zf.read(sorted(glb)[0]))
                if gltf:
                    entry = sorted(gltf)[0]
                    base = os.path.dirname(entry)
                    resources = {os.path.relpath(n, base) if base else n: zf.read(n) for n in names if n != entry}
                    return cls.from_gltf_json(zf.read(entry).decode("utf-8"), resources)
            raise ValueError("archive contains no .glb or .gltf")
        with open(path, "rb") as f:
            head = f.read(4)
        if head == GLB_MAGIC:
            with open(path, "rb") as f:
                return cls.from_glb_bytes(f.read())
        folder = os.path.dirname(path)
        resources = _DiskResources(folder)
        with open(path, encoding="utf-8") as f:
            return cls.from_gltf_json(f.read(), resources)

    # -- access --------------------------------------------------------------

    def buffer_view_bytes(self, index: int) -> bytes:
        view = self.gltf["bufferViews"][index]
        buf = self.buffers[view["buffer"]]
        start = view.get("byteOffset", 0)
        return buf[start: start + view["byteLength"]]

    def accessor(self, index: int) -> np.ndarray:
        acc = self.gltf["accessors"][index]
        dtype = np.dtype(_COMPONENT_DTYPES[acc["componentType"]])
        comps = _TYPE_SIZES[acc["type"]]
        count = acc["count"]
        if "bufferView" not in acc:
            return np.zeros((count, comps), dtype=dtype)
        view = self.gltf["bufferViews"][acc["bufferView"]]
        raw = self.buffers[view["buffer"]]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = view.get("byteStride")
        item = dtype.itemsize * comps
        if stride and stride != item:
            rows = np.frombuffer(raw, dtype=np.uint8, count=stride * (count - 1) + item, offset=start)
            rows = np.lib.stride_tricks.as_strided(rows, shape=(count, item), strides=(stride, 1))
            arr = np.ascontiguousarray(rows).view(dtype).reshape(count, comps)
        else:
            arr = np.frombuffer(raw, dtype=dtype, count=count * comps, offset=start).reshape(count, comps)
        return arr

    def image_bytes(self, index: int) -> tuple[bytes, str]:
        image = self.gltf["images"][index]
        if "bufferView" in image:
            return self.buffer_view_bytes(image["bufferView"]), image.get("mimeType", "image/jpeg")
        uri = image["uri"]
        data = _resolve_uri(uri, self.resources)
        mime = image.get("mimeType") or ("image/png" if uri.lower().endswith(".png") else "image/jpeg")
        return data, mime

    def primitive_geometry(self, prim: dict) -> dict:
        """positions/uvs/normals/colors/indices of a primitive, decoding Draco when present."""
        draco = (prim.get("extensions") or {}).get("KHR_draco_mesh_compression")
        if draco:
            uv_divisor = UV_MAX
            uv_acc = prim["attributes"].get("TEXCOORD_0")
            if uv_acc is not None:
                ctype = self.gltf["accessors"][uv_acc]["componentType"]
                uv_divisor = {UNSIGNED_BYTE: 255.0, UNSIGNED_SHORT: float(UV_MAX)}.get(ctype, 1.0)
            geo = decode_draco(self.buffer_view_bytes(draco["bufferView"]), draco.get("attributes"), uv_divisor)
        else:
            attrs = prim["attributes"]
            geo = {"positions": self.accessor(attrs["POSITION"]).astype(np.float32)}
            if "TEXCOORD_0" in attrs:
                geo["uvs"] = _normalized(self.accessor(attrs["TEXCOORD_0"]), self.gltf["accessors"][attrs["TEXCOORD_0"]])
            if "NORMAL" in attrs:
                geo["normals"] = self.accessor(attrs["NORMAL"]).astype(np.float32)
            if "COLOR_0" in attrs:
                c = _normalized(self.accessor(attrs["COLOR_0"]), self.gltf["accessors"][attrs["COLOR_0"]])
                geo["colors"] = np.clip(c[:, :3] * 255 + 0.5, 0, 255).astype(np.uint8)
            if "indices" in prim:
                geo["indices"] = self.accessor(prim["indices"]).reshape(-1).astype(np.uint32)
                if prim.get("mode", MODE_TRIANGLES) == MODE_TRIANGLES:
                    geo["indices"] = geo["indices"].reshape(-1, 3)
        geo["mode"] = prim.get("mode", MODE_TRIANGLES)
        return geo

    def primitives(self):
        """Yield ``(node_index, mesh_index, primitive)`` for every primitive in the default scene."""
        scene = self.gltf.get("scenes", [{}])[self.gltf.get("scene", 0)]
        stack = list(scene.get("nodes", []))
        seen = set()
        while stack:
            ni = stack.pop()
            if ni in seen:
                continue
            seen.add(ni)
            node = self.gltf["nodes"][ni]
            stack.extend(node.get("children", []))
            if "mesh" in node:
                for prim in self.gltf["meshes"][node["mesh"]]["primitives"]:
                    yield ni, node["mesh"], prim

    def rtc_center(self):
        center = ((self.gltf.get("extensions") or {}).get("CESIUM_RTC") or {}).get("center")
        return [float(v) for v in center] if center else None

    def georef(self) -> dict | None:
        return ((self.gltf.get("asset") or {}).get("extras") or {}).get(GEOREF_KEY)


class _DiskResources(dict):
    def __init__(self, folder: str):
        super().__init__()
        self.folder = folder

    def __missing__(self, key):
        with open(os.path.join(self.folder, key), "rb") as f:
            return f.read()

    def __contains__(self, key):
        return os.path.isfile(os.path.join(self.folder, key))


def _resolve_uri(uri: str, resources) -> bytes:
    if uri.startswith("data:"):
        import base64
        return base64.b64decode(uri.split(",", 1)[1])
    from urllib.parse import unquote
    name = unquote(uri)
    if name in resources:
        return resources[name]
    raise ValueError(f"missing resource {uri!r}")


def _normalized(arr: np.ndarray, accessor: dict) -> np.ndarray:
    if arr.dtype.kind == "f":
        return arr.astype(np.float32)
    if accessor.get("normalized"):
        return (arr.astype(np.float32) / np.iinfo(arr.dtype).max).astype(np.float32)
    return arr.astype(np.float32)


def read_glb_json(path: str) -> dict:
    """The JSON chunk only (cheap: header + JSON, no buffer copy)."""
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != GLB_MAGIC:
            raise ValueError("not a GLB file")
        chunk_len, chunk_type = struct.unpack("<II", f.read(8))
        if chunk_type != CHUNK_JSON:
            raise ValueError("GLB first chunk is not JSON")
        return json.loads(f.read(chunk_len).decode("utf-8"))


def triangle_count(gltf: dict) -> int:
    total = 0
    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            if prim.get("mode", MODE_TRIANGLES) != MODE_TRIANGLES:
                continue
            if "indices" in prim:
                total += gltf["accessors"][prim["indices"]]["count"] // 3
            elif "POSITION" in prim.get("attributes", {}):
                total += gltf["accessors"][prim["attributes"]["POSITION"]]["count"] // 3
    return int(total)


def point_count(gltf: dict) -> int:
    total = 0
    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            if prim.get("mode", MODE_TRIANGLES) == MODE_POINTS and "POSITION" in prim.get("attributes", {}):
                total += gltf["accessors"][prim["attributes"]["POSITION"]]["count"]
    return int(total)
