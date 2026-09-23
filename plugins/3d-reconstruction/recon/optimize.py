"""The ``mesh`` workflow: make an existing ODM textured mesh web-ready.

ODM's ``odm_textured_model_geo.glb`` already has a sensible triangle count
(``mesh-size``, 200k by default) — what makes it heavy on the web are the
texture atlases: a survey easily ships five 8192² JPEGs, over 2 GB once
decoded. So this workflow keeps the geometry as it is and

* shrinks every atlas so the longest side fits ``texture_size`` **and** the
  total decoded pixels fit ``texture_budget_mp`` (uniform cap, so detail is
  lost evenly rather than per atlas);
* drops normals (materials are unlit) and re-encodes the geometry with Draco
  (or raw float32) into a single buffer;
* records the georeference: ODM's ``CESIUM_RTC`` centre becomes the origin,
  the task's EPSG (from the run context) the CRS. Meshes stored in absolute
  projected coordinates are re-centred so float32 stays precise.

Inputs may be a ``.glb``, a ``.gltf`` archive or ODM's OBJ archive fallback.
"""

import numpy as np

from . import gltf, texture
from .crs import Georef, crs_from_epsg, crs_from_wkt, local_origin
from .errors import InputError
from .params import Params

LARGE_COORD = 1e4  # beyond this the mesh is in absolute projected coordinates


def _load(path: str) -> dict:
    """Uniform ``{"primitives": [...], "rtc": [...]|None, "source": str}`` from GLB/glTF/OBJ."""
    try:
        doc = gltf.GltfDocument.open(path)
    except ValueError as e:
        if path.lower().endswith(".zip"):
            from .objloader import load_obj_zip
            loaded = load_obj_zip(path)
            loaded["rtc"] = None
            loaded["generator"] = "obj"
            return loaded
        raise InputError(f"cannot read the 3D model: {e}") from e

    primitives = []
    image_cache: dict[int, tuple[bytes, str]] = {}
    for _node, _mesh, prim in doc.primitives():
        if prim.get("mode", gltf.MODE_TRIANGLES) != gltf.MODE_TRIANGLES:
            continue
        geo = doc.primitive_geometry(prim)
        entry = {"positions": geo["positions"], "uvs": geo.get("uvs"), "indices": geo.get("indices"),
                 "colors": geo.get("colors"), "image": None, "mime": None, "material": None}
        mat_index = prim.get("material")
        if mat_index is not None:
            material = doc.gltf["materials"][mat_index]
            entry["material"] = material.get("name") or f"material_{mat_index}"
            tex = (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
            if tex is not None:
                source = doc.gltf["textures"][tex["index"]].get("source")
                if source is not None:
                    if source not in image_cache:
                        image_cache[source] = doc.image_bytes(source)
                    entry["image"], entry["mime"] = image_cache[source]
                    entry["image_id"] = source
        if entry["indices"] is None:
            n = entry["positions"].shape[0] - entry["positions"].shape[0] % 3
            entry["indices"] = np.arange(n, dtype=np.uint32).reshape(-1, 3)
        primitives.append(entry)
    if not primitives:
        raise InputError("the 3D model contains no triangle meshes")
    return {"primitives": primitives, "rtc": doc.rtc_center(), "source": path,
            "generator": (doc.gltf.get("asset") or {}).get("generator", ""), "georef": doc.georef()}


def optimize_model(path: str, params: Params, context: dict, progress) -> dict:
    with progress.stage("reading model", 6):
        model = _load(path)
    prims = model["primitives"]
    total_tris = int(sum(p["indices"].shape[0] for p in prims))
    total_verts = int(sum(p["positions"].shape[0] for p in prims))
    progress.log(f"{len(prims)} primitives, {total_tris} triangles, {total_verts} vertices; generator {model.get('generator')!r}")

    # Origin: keep ODM's RTC centre; re-centre meshes stored in absolute coordinates.
    all_pos = np.concatenate([p["positions"] for p in prims], axis=0).astype(np.float64)
    rtc = model.get("rtc")
    recentred = False
    if rtc is None and np.abs(all_pos.mean(axis=0)).max() > LARGE_COORD:
        lo, hi = all_pos.min(axis=0), all_pos.max(axis=0)
        origin = local_origin((lo[0], lo[1], hi[0], hi[1]), lo[2])
        for p in prims:
            p["positions"] = (p["positions"].astype(np.float64) - origin).astype(np.float32)
        all_pos -= origin
        recentred = True
        progress.log(f"re-centred absolute coordinates on origin {origin}")
    else:
        origin = tuple(float(v) for v in (rtc or (0.0, 0.0, 0.0)))

    # CRS: from the run context (task EPSG / WKT); a previous run's extras as fallback.
    task = (context or {}).get("task") or {}
    crs = crs_from_epsg(task.get("epsg")) or crs_from_wkt(task.get("wkt"))
    previous = model.get("georef") or {}
    if crs is None and previous.get("epsg"):
        crs = crs_from_epsg(previous["epsg"])
    georeferenced = rtc is not None or recentred or bool(previous.get("origin"))
    if not georeferenced:
        progress.warn("model has no CESIUM_RTC centre or absolute coordinates; the output keeps its local frame "
                      "and will not carry a map extent")
    if georeferenced and crs is None:
        progress.warn("task has no EPSG code; the origin is recorded but the CRS is unknown")

    lo, hi = all_pos.min(axis=0), all_pos.max(axis=0)
    bounds = (origin[0] + lo[0], origin[1] + lo[1], origin[0] + hi[0], origin[1] + hi[1])
    georef = Georef(crs=crs if georeferenced else None, origin=origin, bounds=bounds,
                    z_range=(origin[2] + lo[2], origin[2] + hi[2]), source="model",
                    extra={"georeferenced": georeferenced})

    # Textures: one shrink per distinct image, sized to the budget.
    images = {}
    for p in prims:
        key = p.get("image_id", id(p["image"])) if p["image"] else None
        if key is not None and key not in images:
            images[key] = {"data": p["image"], "mime": p["mime"]}
    sizes = []
    for entry in images.values():
        try:
            entry["size"] = texture.image_size(entry["data"])
        except Exception as e:
            raise InputError(f"a texture image is unreadable: {e}") from e
        sizes.append(entry["size"])
    side = texture.texture_side_for_budget(sizes, params.texture_size, params.texture_budget_mp * 1_000_000) if sizes else 0
    progress.log(f"{len(images)} textures {sorted(set(sizes))}; per-texture cap {side} px "
                 f"(budget {params.texture_budget_mp} MP)")

    builder = gltf.GlbBuilder()
    texture_report = []
    tex_index = {}
    with progress.stage("shrinking textures", 20):
        for i, (key, entry) in enumerate(images.items()):
            data, mime, before, after = texture.shrink_image(entry["data"], side, params.texture_quality)
            tex_index[key] = builder.add_texture(builder.add_image(data, mime))
            texture_report.append({"before": list(before), "after": list(after), "bytes": len(data)})
            progress.report(20 + 40 * (i + 1) / max(len(images), 1))
            entry["data"] = None  # release the original

    with progress.stage("encoding geometry", 62):
        material_cache = {}
        out_prims = []
        for i, p in enumerate(prims):
            key = p.get("image_id", id(p["image"])) if p["image"] else None
            mkey = (key, p["material"])
            if mkey not in material_cache:
                material_cache[mkey] = builder.add_material(tex_index.get(key), name=p["material"])
            uvs = p["uvs"] if (key is not None and p["uvs"] is not None) else None
            colors = p.get("colors") if uvs is None else None
            out_prims.append(builder.add_primitive(
                np.asarray(p["positions"], dtype=np.float32), indices=p["indices"], uvs=uvs, colors=colors,
                material=material_cache[mkey], draco={"bits": 14, "level": 7} if params.draco else None))
            p["positions"] = p["indices"] = None
            progress.report(62 + 28 * (i + 1) / len(prims))
        builder.add_node(builder.add_mesh(out_prims, name="model"), name="model")
        builder.set_georef(georef.to_extras())
    builder.set_extras(source_model={"generator": model.get("generator"), "primitives": len(prims),
                                     "triangles": total_tris})

    return {
        "builder": builder, "inputs_used": {"model": "optimised"}, "georef": georef,
        "mesh": {"triangles": total_tris, "vertices": total_verts, "primitives": len(prims),
                 "compression": params.compression, "recentred": recentred},
        "texture": {"count": len(images), "cap_px": side, "images": texture_report,
                    "total_pixels_before": int(sum(w * h for w, h in sizes)),
                    "total_pixels_after": int(sum(r["after"][0] * r["after"][1] for r in texture_report)),
                    "quality": params.texture_quality},
    }
