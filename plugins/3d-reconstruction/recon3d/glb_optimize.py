"""Optimize an existing glTF/GLB (ODM's ``odm_textured_model_geo.glb``) for the web.

ODM's textured model is geometrically web-sized already (``mesh-size`` defaults
to 200 k faces) but ships its texture atlases at full resolution — a real
survey carries five 8192² and ten 4096² JPEGs, over 2 GB once decoded, which
is what makes the viewer struggle. This module:

1. re-encodes every texture larger than ``texture_cap`` at that size (decoded
   at reduced scale by GDAL, so the full image is never in memory),
2. optionally quantizes positions to uint16 (``KHR_mesh_quantization``),
3. copies every other buffer view verbatim (indices, normals, Draco payloads,
   sparse accessors — nothing else is touched), and
4. records georeferencing (``CESIUM_RTC`` centre -> ``extras.webodm_georef``).

Input may be a GLB or a zip holding ``.gltf`` + ``.bin`` + images (the shape
WebODM stores when ODM produced a non-binary glTF).
"""

import base64
import copy
import os
import posixpath
import zipfile
from dataclasses import dataclass, field

import numpy as np

from . import gltf as gltf_mod
from . import texture as texture_mod
from .errors import InputError
from .sources import ModelSource

_MIME_FOR_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
_COMPONENT_DTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32,
                    5126: np.float32}
_TYPE_WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


@dataclass
class GltfDoc:
    root: dict
    buffers: list                       # list[bytes]
    image_bytes: dict = field(default_factory=dict)   # image index -> (bytes, mime) for URI images
    source_bytes: int = 0


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load(source: ModelSource) -> GltfDoc:
    with open(source.path, "rb") as f:
        data = f.read()
    if source.format == "glb":
        return _from_glb(data)
    return _from_zip(source.path, data)


def _from_glb(data: bytes) -> GltfDoc:
    try:
        root, binary = gltf_mod.parse_glb(data)
    except ValueError as e:
        raise InputError(f"model: {e}") from e
    buffers = []
    for i, buf in enumerate(root.get("buffers", [])):
        uri = buf.get("uri")
        if uri is None:
            buffers.append(binary if i == 0 else b"")
        elif uri.startswith("data:"):
            buffers.append(_data_uri(uri))
        else:
            raise InputError(f"model: GLB references external buffer '{uri}', which is not available")
    doc = GltfDoc(root, buffers, source_bytes=len(data))
    _resolve_uri_images(doc, lambda uri: _data_uri(uri) if uri.startswith("data:") else None)
    return doc


def _from_zip(path: str, _data: bytes) -> GltfDoc:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        glbs = [n for n in names if n.lower().endswith(".glb")]
        gltfs = [n for n in names if n.lower().endswith(".gltf")]
        if glbs and not gltfs:
            doc = _from_glb(zf.read(glbs[0]))
            doc.source_bytes = os.path.getsize(path)
            return doc
        if not gltfs:
            raise InputError("model: archive contains no .gltf file")
        entry = gltfs[0]
        base = posixpath.dirname(entry)
        import json
        root = json.loads(zf.read(entry).decode("utf-8"))
        lookup = {n.lower(): n for n in names}

        def member(uri):
            if uri.startswith("data:"):
                return _data_uri(uri)
            rel = posixpath.normpath(posixpath.join(base, uri.replace("\\", "/")))
            name = lookup.get(rel.lower()) or lookup.get(posixpath.basename(rel).lower())
            if name is None:
                raise InputError(f"model: archive is missing '{uri}' referenced by the glTF")
            return zf.read(name)

        buffers = [member(b["uri"]) if b.get("uri") else b"" for b in root.get("buffers", [])]
        doc = GltfDoc(root, buffers, source_bytes=os.path.getsize(path))
        _resolve_uri_images(doc, member)
        return doc


def _data_uri(uri: str) -> bytes:
    header, _, payload = uri.partition(",")
    if ";base64" not in header:
        raise InputError("model: unsupported (non-base64) data URI")
    return base64.b64decode(payload)


def _resolve_uri_images(doc: GltfDoc, fetch):
    for i, img in enumerate(doc.root.get("images", [])):
        uri = img.get("uri")
        if uri is None:
            continue
        data = fetch(uri)
        if data is None:
            raise InputError(f"model: image '{uri}' is not available")
        mime = img.get("mimeType") or _MIME_FOR_EXT.get(os.path.splitext(uri.split("?")[0])[1].lower(), "image/jpeg")
        doc.image_bytes[i] = (data, mime)


def image_data(doc: GltfDoc, index: int) -> tuple:
    img = doc.root["images"][index]
    if index in doc.image_bytes:
        return doc.image_bytes[index]
    view = doc.root["bufferViews"][img["bufferView"]]
    buf = doc.buffers[view["buffer"]]
    start = view.get("byteOffset", 0)
    return bytes(buf[start:start + view["byteLength"]]), img.get("mimeType", "image/jpeg")


def read_accessor(doc: GltfDoc, index: int) -> np.ndarray:
    """Accessor ``index`` as a (count, components) array (dequantized if normalized)."""
    acc = doc.root["accessors"][index]
    if "sparse" in acc or "bufferView" not in acc:
        raise InputError("model: sparse or buffer-less accessors are not supported for quantization")
    view = doc.root["bufferViews"][acc["bufferView"]]
    buf = doc.buffers[view["buffer"]]
    dtype = np.dtype(_COMPONENT_DTYPE[acc["componentType"]])
    width = _TYPE_WIDTH[acc["type"]]
    count = acc["count"]
    elem = dtype.itemsize * width
    stride = view.get("byteStride") or elem
    offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    raw = np.frombuffer(buf, dtype=np.uint8, offset=offset, count=stride * (count - 1) + elem)
    strided = np.lib.stride_tricks.as_strided(raw, shape=(count, elem), strides=(stride, 1))
    arr = np.ascontiguousarray(strided).view(dtype).reshape(count, width)
    if acc.get("normalized"):
        info = np.iinfo(dtype)
        arr = arr.astype(np.float64) / (info.max if info.min == 0 else -info.min)
    return arr


# --------------------------------------------------------------------------
# Optimization
# --------------------------------------------------------------------------

def optimize(doc: GltfDoc, *, texture_cap: int, quality: int, quantize: bool,
             crs=None, progress=None) -> tuple:
    """Return ``(glb_bytes, stats)``. ``crs`` is the CRS the RTC centre is in, if known."""
    root = copy.deepcopy(doc.root)
    builder = gltf_mod.GlbBuilder()
    stats = {"textures": 0, "textures_resized": 0, "texture_max_side_before": 0,
             "texture_max_side_after": 0, "texture_bytes_before": 0, "texture_bytes_after": 0,
             "meshes_quantized": 0, "triangles": 0, "vertices": 0, "bytes_before": doc.source_bytes}

    image_views = {img["bufferView"] for img in root.get("images", []) if "bufferView" in img}
    view_map = {}
    for i, view in enumerate(root.get("bufferViews", [])):
        if i in image_views:
            continue
        buf = doc.buffers[view["buffer"]]
        start = view.get("byteOffset", 0)
        data = bytes(buf[start:start + view["byteLength"]])
        view_map[i] = builder.add_buffer_view(data, view.get("target"), view.get("byteStride"))

    # Textures: resize the ones above the cap, keep the rest byte-for-byte.
    new_images = []
    for i, img in enumerate(root.get("images", [])):
        data, mime = image_data(doc, i)
        stats["textures"] += 1
        stats["texture_bytes_before"] += len(data)
        try:
            arr, w, h = texture_mod.decode(data, max_side=texture_cap)
        except Exception as e:
            raise InputError(f"model: texture {i} cannot be decoded ({e})") from e
        stats["texture_max_side_before"] = max(stats["texture_max_side_before"], w, h)
        if max(w, h) > texture_cap:
            has_alpha = arr.shape[0] in (2, 4) and bool((arr[-1] < 255).any())
            if has_alpha:
                data, mime = texture_mod.encode(arr, "png")
            else:
                rgb = arr[:3] if arr.shape[0] >= 3 else np.repeat(arr[:1], 3, axis=0)
                data, mime = texture_mod.encode(rgb, "jpeg", quality)
            stats["textures_resized"] += 1
            side = max(arr.shape[1], arr.shape[2])
            if progress:
                progress.log(f"texture {i}: {w}x{h} -> {arr.shape[2]}x{arr.shape[1]} {mime}")
        else:
            side = max(w, h)
        stats["texture_max_side_after"] = max(stats["texture_max_side_after"], side)
        stats["texture_bytes_after"] += len(data)
        new_images.append({"bufferView": builder.add_buffer_view(data), "mimeType": mime,
                           **({"name": img["name"]} if img.get("name") else {})})
    if new_images:
        root["images"] = new_images

    accessors = root.get("accessors", [])
    for acc in accessors:
        if "bufferView" in acc:
            acc["bufferView"] = view_map[acc["bufferView"]]
        sparse = acc.get("sparse")
        if sparse:
            sparse["indices"]["bufferView"] = view_map[sparse["indices"]["bufferView"]]
            sparse["values"]["bufferView"] = view_map[sparse["values"]["bufferView"]]

    # Geometry statistics + optional quantization.
    boxes = {}
    for mi, mesh in enumerate(root.get("meshes", [])):
        for prim in mesh.get("primitives", []):
            pos = prim.get("attributes", {}).get("POSITION")
            if pos is None:
                continue
            acc = accessors[pos]
            stats["vertices"] += acc["count"]
            if "indices" in prim:
                stats["triangles"] += accessors[prim["indices"]]["count"] // 3
            else:
                stats["triangles"] += acc["count"] // 3
            if "min" in acc and "max" in acc:
                lo, hi = np.array(acc["min"], float), np.array(acc["max"], float)
                if mi in boxes:
                    boxes[mi] = (np.minimum(boxes[mi][0], lo), np.maximum(boxes[mi][1], hi))
                else:
                    boxes[mi] = (lo, hi)

    # Scene box before quantization re-parents nodes (boxes are in mesh space).
    local_box = _scene_box(root, boxes)

    if quantize:
        stats["meshes_quantized"] = _quantize_meshes(doc, root, builder, accessors, boxes, progress)

    # Georeferencing.
    origin = None
    rtc = (root.get("extensions") or {}).get("CESIUM_RTC", {}).get("center")
    existing = (root.get("extras") or {}).get("webodm_georef") or {}
    if rtc and len(rtc) == 3:
        origin = [float(v) for v in rtc]
    elif existing.get("origin"):
        origin = [float(v) for v in existing["origin"]]
    bounds = bounds_4326 = None
    if origin is not None and local_box is not None and crs is not None:
        lo, hi = local_box
        bounds = (origin[0] + lo[0], origin[1] + lo[1], origin[0] + hi[0], origin[1] + hi[1])
        from .sources import bounds_to_4326
        try:
            bounds_4326 = bounds_to_4326(crs, bounds)
        except Exception:
            bounds_4326 = None
    extras = dict(root.get("extras") or {})
    extras.update(gltf_mod.georef_extras(
        crs, origin or [0.0, 0.0, 0.0], bounds, bounds_4326, source="optimize-model",
        extra={"origin_known": origin is not None, "triangles": stats["triangles"],
               "vertices": stats["vertices"], "textures": stats["textures"]},
    ))
    root["extras"] = extras
    root["bufferViews"] = builder.buffer_views
    root["buffers"] = [{"byteLength": len(builder.buffer)}]
    used = set(root.get("extensionsUsed", [])) | builder.extensions_used
    required = set(root.get("extensionsRequired", [])) | builder.extensions_required
    if origin is not None and "CESIUM_RTC" not in (root.get("extensions") or {}):
        root.setdefault("extensions", {})["CESIUM_RTC"] = {"center": origin}
    if "CESIUM_RTC" in (root.get("extensions") or {}):
        used.add("CESIUM_RTC")
    if used:
        root["extensionsUsed"] = sorted(used)
    if required:
        root["extensionsRequired"] = sorted(required)
    root.setdefault("asset", {})["generator"] = (
        (root["asset"].get("generator", "") + " + " if root["asset"].get("generator") else "")
        + "WebODM 3D Reconstruction plugin"
    )
    glb = gltf_mod.pack_glb(root, bytes(builder.buffer))
    stats["bytes_after"] = len(glb)
    stats["origin"] = origin
    stats["bounds"] = list(bounds) if bounds else None
    stats["bounds_4326"] = bounds_4326
    return glb, stats


def _quantize_meshes(doc, root, builder, accessors, boxes, progress) -> int:
    """Quantize POSITION of eligible meshes; move each into a dequantizing child node."""
    owners = {}
    for mi, mesh in enumerate(root.get("meshes", [])):
        for prim in mesh.get("primitives", []):
            pos = prim.get("attributes", {}).get("POSITION")
            if pos is not None:
                owners.setdefault(pos, set()).add(mi)
    skinned = {n["mesh"] for n in root.get("nodes", []) if "mesh" in n and "skin" in n}
    quantized = 0
    for mi, mesh in enumerate(root.get("meshes", [])):
        if mi in skinned or mi not in boxes:
            continue
        prims = mesh.get("primitives", [])
        eligible = all(
            "POSITION" in p.get("attributes", {})
            and accessors[p["attributes"]["POSITION"]]["componentType"] == gltf_mod.FLOAT
            and not p.get("targets")
            and "KHR_draco_mesh_compression" not in (p.get("extensions") or {})
            and owners[p["attributes"]["POSITION"]] == {mi}
            for p in prims
        )
        if not eligible or not prims:
            continue
        lo, hi = boxes[mi]
        span = np.where(hi - lo > 0, hi - lo, 1.0)
        scale = span / 65535.0
        for prim in prims:
            pos_idx = prim["attributes"]["POSITION"]
            positions = read_accessor(doc, pos_idx).astype(np.float64)
            q = np.rint((positions - lo) / scale).clip(0, 65535).astype(np.uint16)
            builder.add_accessor(q, gltf_mod.UNSIGNED_SHORT, target=gltf_mod.ARRAY_BUFFER)
            accessors.append(builder.accessors[-1])
            prim["attributes"]["POSITION"] = len(accessors) - 1
        # Re-parent: every node showing this mesh gets a child carrying the
        # dequantization transform so the node's own transform is untouched.
        for node in list(root.get("nodes", [])):
            if node.get("mesh") == mi:
                child = {"mesh": mi, "translation": [float(v) for v in lo], "scale": [float(v) for v in scale]}
                root["nodes"].append(child)
                node.setdefault("children", []).append(len(root["nodes"]) - 1)
                del node["mesh"]
        builder.extensions_used.add("KHR_mesh_quantization")
        builder.extensions_required.add("KHR_mesh_quantization")
        quantized += 1
    if progress and quantized:
        progress.log(f"quantized positions of {quantized} mesh(es) to 16 bit")
    return quantized


def _node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
    t = np.array(node.get("translation", [0, 0, 0]), dtype=np.float64)
    s = np.array(node.get("scale", [1, 1, 1]), dtype=np.float64)
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    rot = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    m = np.eye(4)
    m[:3, :3] = rot * s[None, :]
    m[:3, 3] = t
    return m


def _scene_box(root: dict, boxes: dict):
    """World-space bounding box of the (first) scene from the meshes' accessor boxes."""
    nodes = root.get("nodes", [])
    scenes = root.get("scenes") or []
    roots = scenes[root.get("scene", 0)]["nodes"] if scenes else list(range(len(nodes)))
    lo_all = hi_all = None
    stack = [(i, np.eye(4)) for i in roots]
    seen = set()
    while stack:
        idx, parent = stack.pop()
        if idx in seen or idx >= len(nodes):
            continue
        seen.add(idx)
        node = nodes[idx]
        m = parent @ _node_matrix(node)
        if "mesh" in node and node["mesh"] in boxes:
            lo, hi = boxes[node["mesh"]]
            corners = np.array([[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            world = (m @ corners.T).T[:, :3]
            clo, chi = world.min(axis=0), world.max(axis=0)
            lo_all = clo if lo_all is None else np.minimum(lo_all, clo)
            hi_all = chi if hi_all is None else np.maximum(hi_all, chi)
        for child in node.get("children", []):
            stack.append((child, m))
    return None if lo_all is None else (lo_all, hi_all)
