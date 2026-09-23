#!/usr/bin/env python
"""Render a quick oblique preview of a GLB produced by the plugin (no browser needed).

    python tools/preview.py out.glb preview.png [--top] [--size 1200]

A painter's-algorithm software rasteriser: every triangle is filled with the
texture colour at its centroid (or its vertex colour / a height ramp), sorted
far-to-near. Good enough to eyeball holes, texture alignment and orientation;
it is not the viewer. Also prints the GLB's summary (extensions, georef,
counts) so a file can be inspected in one go.
"""

import argparse
import io
import json
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from recon import gltf  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("png")
    ap.add_argument("--top", action="store_true", help="nadir view instead of oblique")
    ap.add_argument("--size", type=int, default=1200)
    ap.add_argument("--max-faces", type=int, default=400_000)
    args = ap.parse_args()

    doc = gltf.GltfDocument.open(args.glb)
    g = doc.gltf
    print(json.dumps({
        "generator": g["asset"].get("generator"), "extensionsUsed": g.get("extensionsUsed"),
        "extensionsRequired": g.get("extensionsRequired"), "rtc": doc.rtc_center(),
        "georef": {k: v for k, v in (doc.georef() or {}).items() if k != "wkt"},
        "triangles": gltf.triangle_count(g), "points": gltf.point_count(g),
        "images": len(g.get("images", [])), "bytes": os.path.getsize(args.glb),
    }, indent=1, default=str))

    faces_all, colors_all, verts_all, pts, pt_colors = [], [], [], [], []
    offset = 0
    images = {}
    for _n, _m, prim in doc.primitives():
        geo = doc.primitive_geometry(prim)
        pos = geo["positions"].astype(np.float64)
        if geo["mode"] == gltf.MODE_POINTS:
            pts.append(pos)
            pt_colors.append(geo.get("colors"))
            continue
        idx = geo.get("indices")
        if idx is None:
            continue
        col = None
        mat = g["materials"][prim["material"]] if "material" in prim else {}
        tex = (mat.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
        if tex is not None and geo.get("uvs") is not None:
            src = g["textures"][tex["index"]]["source"]
            if src not in images:
                data, _ = doc.image_bytes(src)
                images[src] = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
            img = images[src]
            uv = geo["uvs"][idx].mean(axis=1)
            x = np.clip((uv[:, 0] * (img.shape[1] - 1)).astype(int), 0, img.shape[1] - 1)
            y = np.clip((uv[:, 1] * (img.shape[0] - 1)).astype(int), 0, img.shape[0] - 1)
            col = img[y, x]
        elif geo.get("colors") is not None:
            col = geo["colors"][idx].mean(axis=1).astype(np.uint8)
        verts_all.append(pos)
        faces_all.append(idx.astype(np.int64) + offset)
        colors_all.append(col)
        offset += pos.shape[0]

    W = H = args.size
    canvas = Image.new("RGB", (W, H), (28, 32, 48))
    draw = ImageDraw.Draw(canvas)

    def project(p):
        # Z-up world -> oblique camera: rotate about X so we look down at ~55°.
        if args.top:
            return np.column_stack([p[:, 0], p[:, 1], p[:, 2]])
        a = math.radians(55)
        y = p[:, 1] * math.cos(a) + p[:, 2] * math.sin(a)
        d = -p[:, 1] * math.sin(a) + p[:, 2] * math.cos(a)
        return np.column_stack([p[:, 0], y, d])

    allp = np.concatenate(verts_all + pts, axis=0) if (verts_all or pts) else np.zeros((1, 3))
    center = (allp.min(axis=0) + allp.max(axis=0)) / 2
    scale = 0.92 * W / max(np.ptp(allp[:, :2], axis=0).max(), 1e-6)

    def to_px(p):
        q = project(p - center)
        return np.column_stack([W / 2 + q[:, 0] * scale, H / 2 - q[:, 1] * scale]), q[:, 2]

    if verts_all:
        V = np.concatenate(verts_all, axis=0)
        F = np.concatenate(faces_all, axis=0)
        C = np.concatenate([c if c is not None else np.full((len(f), 3), 160, np.uint8)
                            for c, f in zip(colors_all, faces_all)], axis=0)
        if F.shape[0] > args.max_faces:
            keep = np.random.default_rng(0).choice(F.shape[0], args.max_faces, replace=False)
            F, C = F[keep], C[keep]
        px, depth = to_px(V)
        order = np.argsort(depth[F].mean(axis=1))
        for fi in order:
            tri = F[fi]
            draw.polygon([tuple(px[tri[0]]), tuple(px[tri[1]]), tuple(px[tri[2]])], fill=tuple(int(v) for v in C[fi]))
        print(f"drew {F.shape[0]} triangles")
    if pts:
        P = np.concatenate(pts, axis=0)
        C = np.concatenate([c if c is not None else np.full((len(p), 3), 200, np.uint8) for c, p in zip(pt_colors, pts)])
        if P.shape[0] > 400_000:
            keep = np.random.default_rng(0).choice(P.shape[0], 400_000, replace=False)
            P, C = P[keep], C[keep]
        px, depth = to_px(P)
        order = np.argsort(depth)
        arr = np.asarray(canvas).copy()
        xi = np.clip(px[order, 0].astype(int), 0, W - 1)
        yi = np.clip(px[order, 1].astype(int), 0, H - 1)
        arr[yi, xi] = C[order]
        canvas = Image.fromarray(arr)
        print(f"drew {P.shape[0]} points")
    canvas.save(args.png)
    print("saved", args.png)


if __name__ == "__main__":
    main()
