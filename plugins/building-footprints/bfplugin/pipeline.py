"""End-to-end run: request → grid → windowed inference → instances → GeoJSON.

Stages (each logged, timed and reported as progress):

1. open the orthophoto
2. choose the model card and load the ONNX model (file, checksum, graph contract)
3. build the processing grid (fails with the resolution to use when over the pixel budget)
4. run the model over overlapping windows, Gaussian-blend the mask probability
   and signed-distance planes
5. threshold the mask and separate touching buildings with a watershed on the
   distance plane
6. vectorise each instance, simplify, regularise, drop tiny regions
7. write the GeoJSON (EPSG:4326) and the run metadata
"""

import json
import os

import numpy as np
import rasterio

from . import vectorize
from .errors import InputError
from .grid import DEFAULT_MAX_PIXELS, RgbReader, build_grid, open_orthophoto
from .inference import OnnxModel
from .instances import separate_instances
from .params import Params
from .progress import Progress
from .registry import load_cards, select_model
from .tiling import PlaneBlender, gaussian2d, pad_to, tile_layout

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

# GDAL's block cache defaults to 5% of RAM, which can be most of the sandbox's
# address-space budget on a large host; the windowed reads here need far less.
GDAL_CACHE_MB = 128

GAUSSIAN_SIGMA_SCALE = 1 / 8


def run(request: dict, progress: Progress | None = None, models_dir: str | None = None,
        max_pixels: int | None = None) -> dict:
    with rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_MB, GDAL_NUM_THREADS=1):
        return _run(request, progress or Progress(), models_dir or MODELS_DIR, max_pixels or DEFAULT_MAX_PIXELS)


def _run(request: dict, progress: Progress, models_dir: str, max_pixels: int) -> dict:
    params = Params.from_request(request.get("params"))
    if params.unknown:
        progress.warn(f"ignoring unknown parameters: {', '.join(sorted(params.unknown))}")
    output_path = request["output_path"]

    ortho = None
    reader = None
    try:
        with progress.stage("open orthophoto", 1):
            ortho = open_orthophoto(request.get("inputs") or {})
            progress.log(f"orthophoto: {ortho.describe()}")

        with progress.stage("load model", 3):
            cards = load_cards(models_dir)
            for mid, reason in cards.unavailable.items():
                progress.warn(f"model '{mid}' skipped: {reason}")
            card = select_model(cards, params.model)
            progress.log(f"model: {card.id} — {card.label} ({card.license})")
            model = OnnxModel(card, progress)
            eff = params.effective(card)
            if eff["tile_size"] != model.input_size:
                progress.warn(f"tile_size {eff['tile_size']} ignored: the model's input is fixed at "
                              f"{model.input_size} px")
                eff["tile_size"] = model.input_size

        with progress.stage("build processing grid", 5):
            grid = build_grid(ortho, eff["resolution_m"], max_pixels=max_pixels, warn=progress.warn)
            progress.log(f"grid: {grid.width}x{grid.height} px at {grid.resolution_m:.3g} m, {grid.crs.to_string()}")
            reader = RgbReader(ortho, grid)
            if reader.overview_level is not None:
                progress.log(f"reading from overview level {reader.overview_level}")

        tile = eff["tile_size"]
        overlap = eff["overlap"]
        rows, tw, th = tile_layout(grid.width, grid.height, tile, overlap)
        n_tiles = sum(len(r) for r in rows)
        overlap_eff = max(0, min(overlap, tw - 1, th - 1))
        weights = gaussian2d(th, tw, GAUSSIAN_SIGMA_SCALE)
        prob = np.zeros((grid.height, grid.width), dtype="uint8")
        dist = np.full((grid.height, grid.width), -1.0, dtype="float32")
        covered_px = 0
        skipped = 0

        def emit(result):
            nonlocal covered_px
            if result is None:
                return
            y0, planes, covered = result
            h = planes.shape[1]
            p = np.clip(planes[0], 0.0, 1.0)
            prob[y0:y0 + h] = np.where(covered, np.rint(p * 255.0), 0).astype("uint8")
            dist[y0:y0 + h] = np.where(covered, planes[1], -1.0).astype("float32")
            covered_px += int(covered.sum())

        with progress.stage(f"predict {n_tiles} windows of {tw}x{th} px (overlap {overlap_eff})", 8):
            blender = PlaneBlender(2, grid.width, grid.height)
            done = 0
            report_every = max(1, n_tiles // 25)
            for row in rows:
                emit(blender.begin_row(row[0].y0, th))
                for t in row:
                    rgb, valid = reader.read(t.window)
                    if not valid.any():
                        skipped += 1
                    else:
                        full = model.input_size
                        rgb_in = pad_to(rgb, full, full) if (t.h < full or t.w < full) else rgb
                        planes = model.predict(rgb_in)
                        stack = np.stack([planes["mask"][:t.h, :t.w], planes["distance"][:t.h, :t.w]])
                        blender.add(t, stack, weights[:t.h, :t.w], valid)
                    done += 1
                    if done % report_every == 0 or done == n_tiles:
                        progress.log(f"  {done}/{n_tiles} windows")
                        progress.report(8 + 62 * done / n_tiles)
                blender.end_row()
            emit(blender.finish())
            if skipped:
                progress.log(f"skipped {skipped} windows without valid pixels")
            if covered_px == 0:
                raise InputError("the orthophoto has no valid pixels on the processing grid; check its alpha/nodata")

        reader.close()
        reader = None

        with progress.stage("separate instances", 72):
            mask = prob > eff["confidence"] * 255.0
            min_area_px = int(round(eff["min_area_m2"] / grid.pixel_area_m2))
            building_px = int(mask.sum())
            progress.log(f"{building_px} building pixels ({building_px * grid.pixel_area_m2:.0f} m²) "
                         f"above confidence {eff['confidence']:.2f}")
            labels, inst = separate_instances(
                mask, dist,
                seed_min_distance=eff["seed_min_distance"],
                large_blob_area_px=eff["large_blob_area_px"],
                h_maxima_depth=eff["h_maxima_depth"],
                min_area_px=min_area_px,
                log=progress.log,
            )
            mask = dist = None  # free the planes (closed over by emit(), so not `del`)
            progress.log(f"{inst.components} blobs → {inst.instances} buildings "
                         f"({inst.split} blobs split, {inst.dropped_small} tiny blobs dropped)")

        with progress.stage("vectorize", 88):
            footprints, vstats = vectorize.build_footprints(
                labels, prob, grid,
                simplify_m=eff["simplify_m"],
                min_area_m2=eff["min_area_m2"],
                regularize_area_threshold=eff["regularize_area_threshold"],
                regularize_overlap_tol_m2=eff["regularize_overlap_tol_m2"],
            )
            labels = prob = None
            collection = vectorize.feature_collection(footprints, grid, card.class_name)
            progress.log(f"{vstats['feature_count']} footprints ({vstats['regularized']} regularised as rectangles)")

        with progress.stage("write output", 96):
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(collection, f)

        metadata = {
            "model": card.summary(),
            "model_id": card.id,
            "model_label": card.label,
            "backend": model.describe(),
            "inputs_used": ["orthophoto"],
            "grid": grid.summary(),
            "tiles": {
                "count": n_tiles, "size": [tw, th], "overlap": overlap_eff, "skipped_empty": skipped,
                "weighting": "gaussian", "sigma_px": round(tw * GAUSSIAN_SIGMA_SCALE, 1),
            },
            "parameters": {"requested": params.raw(), "effective": eff},
            "instances": inst.as_dict(),
            "vector": {**vstats, **vectorize.collection_stats(collection)},
            "legend": [{"name": card.class_name, "color": card.class_color}],
            "progress": progress.summary(),
        }
        progress.report(100, "finished")
        return metadata
    finally:
        if reader is not None:
            reader.close()
        if ortho is not None:
            ortho.close()
