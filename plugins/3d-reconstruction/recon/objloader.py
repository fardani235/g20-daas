"""Wavefront OBJ/MTL loader for ODM's ``odm_texturing`` fallback archive.

When NodeODM produced no GLB the platform stores the whole ``odm_texturing``
folder as a zip: ``odm_textured_model_geo.obj``, its ``.mtl`` and one texture
per material. This reads that (triangles only, which is what ODM writes) into
the same per-material primitive shape ``optimize.py`` gets from a GLB.

Vertex/texture-coordinate pairs are de-duplicated per material so each
primitive is indexed; ``vn`` normals are ignored (materials are unlit).
"""

import os
import posixpath
import zipfile

import numpy as np

from .errors import InputError


def _parse_mtl(text: str) -> dict[str, str | None]:
    """material name -> diffuse texture file (or None)."""
    materials, current = {}, None
    for line in text.splitlines():
        parts = line.strip().split()
        if not parts or parts[0].startswith("#"):
            continue
        if parts[0] == "newmtl" and len(parts) > 1:
            current = parts[1]
            materials[current] = None
        elif parts[0] == "map_Kd" and current is not None and len(parts) > 1:
            materials[current] = parts[-1]
    return materials


def _read_member(zf: zipfile.ZipFile, base: str, name: str) -> bytes | None:
    for candidate in (posixpath.join(base, name) if base else name, name, posixpath.basename(name)):
        try:
            return zf.read(candidate)
        except KeyError:
            continue
    return None


def load_obj_zip(path: str) -> dict:
    """Return ``{"primitives": [{positions, uvs, indices, image, mime, material}], "source": name}``."""
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        objs = sorted(n for n in names if n.lower().endswith(".obj"))
        if not objs:
            raise InputError("the model archive contains neither a GLB/glTF nor an OBJ")
        # Prefer the georeferenced mesh; ODM also writes a 2.5D one in odm_texturing_25d.
        obj_name = next((n for n in objs if "geo" in posixpath.basename(n).lower()), objs[0])
        base = posixpath.dirname(obj_name)
        obj_text = zf.read(obj_name).decode("utf-8", errors="replace")

        positions, uvs = [], []
        groups: dict[str | None, list] = {}
        materials: dict[str, str | None] = {}
        current = None
        for line in obj_text.splitlines():
            if not line or line[0] == "#":
                continue
            parts = line.split()
            tag = parts[0]
            if tag == "v":
                positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif tag == "vt":
                uvs.append((float(parts[1]), float(parts[2]) if len(parts) > 2 else 0.0))
            elif tag == "f":
                if len(parts) < 4:
                    continue
                corners = []
                for p in parts[1:]:
                    bits = p.split("/")
                    vi = int(bits[0])
                    ti = int(bits[1]) if len(bits) > 1 and bits[1] else 0
                    corners.append((vi, ti))
                # Fan-triangulate anything with more than three corners.
                for k in range(1, len(corners) - 1):
                    groups.setdefault(current, []).extend((corners[0], corners[k], corners[k + 1]))
            elif tag == "usemtl":
                current = parts[1] if len(parts) > 1 else None
            elif tag == "mtllib":
                data = _read_member(zf, base, parts[1]) if len(parts) > 1 else None
                if data:
                    materials.update(_parse_mtl(data.decode("utf-8", errors="replace")))

        if not positions or not groups:
            raise InputError("the OBJ has no faces")
        P = np.asarray(positions, dtype=np.float64)
        T = np.asarray(uvs, dtype=np.float32) if uvs else np.zeros((0, 2), np.float32)

        primitives = []
        for material, corners in groups.items():
            arr = np.asarray(corners, dtype=np.int64).reshape(-1, 2)
            vi = np.where(arr[:, 0] > 0, arr[:, 0] - 1, P.shape[0] + arr[:, 0])
            ti = np.where(arr[:, 1] > 0, arr[:, 1] - 1, np.where(arr[:, 1] < 0, T.shape[0] + arr[:, 1], -1))
            keys = np.stack([vi, ti], axis=1)
            uniq, inverse = np.unique(keys, axis=0, return_inverse=True)
            prim = {
                "positions": P[uniq[:, 0]],
                "indices": inverse.reshape(-1, 3).astype(np.uint32),
                "material": material,
                "image": None, "mime": None, "uvs": None,
            }
            if T.shape[0] and (uniq[:, 1] >= 0).all():
                uv = T[uniq[:, 1]].copy()
                uv[:, 1] = 1.0 - uv[:, 1]  # OBJ v is bottom-up, glTF top-down
                prim["uvs"] = uv.astype(np.float32)
            tex = materials.get(material) if material else None
            if tex:
                data = _read_member(zf, base, tex)
                if data:
                    prim["image"] = data
                    prim["mime"] = "image/png" if tex.lower().endswith(".png") else "image/jpeg"
            primitives.append(prim)
        return {"primitives": primitives, "source": os.path.basename(obj_name)}
