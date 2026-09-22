"""End-to-end run: request → aligned inputs → tiled inference → mask/polygons.

Stages (each logged and timed):

1. open and check the selected inputs
2. choose the model (explicit or ``auto``) and verify it fits the inputs
3. build the processing grid; open aligned readers and the height provider
4. tiled prediction with overlap blending (+ height fusion for RGB models)
5. post-processing (majority filter, sieve, class filter)
6. write the mask GeoTIFF, or polygons when the output path is ``.geojson``
"""

import json
import os

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window

from . import fusion, output, postprocess
from .backends import build_backend
from .backends.base import TileData
from .errors import InputError, ParameterError
from .grid import AlignedReader, build_grid, open_inputs
from .height import HeightProvider
from .params import Params
from .progress import Progress
from .registry import load_cards, select_model
from .tiling import NODATA, BandBlender, hann2d, pad_to, tile_layout

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")


# GDAL's block cache defaults to 5% of RAM, which can be most of the sandbox's
# address-space budget on a large host; the tiled reads here need far less.
GDAL_CACHE_MB = 128


def run(request: dict, progress: Progress | None = None, models_dir: str = MODELS_DIR) -> dict:
    with rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_MB, GDAL_NUM_THREADS=1):
        return _run(request, progress or Progress(), models_dir)


def _run(request: dict, progress: Progress, models_dir: str) -> dict:
    params = Params.from_request(request.get("params"))
    if params.unknown:
        progress.warn(f"ignoring unknown parameters: {', '.join(sorted(params.unknown))}")
    output_path = request["output_path"]
    want_vector = output_path.lower().endswith(".geojson")

    inputs = {}
    height = None
    readers = []
    try:
        with progress.stage("open inputs"):
            inputs = open_inputs(request.get("inputs") or {})
            selected = set(inputs)
            progress.log("selected inputs: " + ", ".join(
                f"{d} ({inputs[d].ds.width}x{inputs[d].ds.height}, {inputs[d].resolution_m():.3g} m, "
                f"{inputs[d].ds.crs.to_string()})" for d in inputs))

        with progress.stage("select model"):
            cards = load_cards(models_dir)
            for mid, reason in cards.unavailable.items():
                progress.warn(f"model '{mid}' skipped: {reason}")
            card = select_model(cards, params.model, selected)
            used, ignored = card.used_inputs(selected)
            if ignored:
                progress.warn(f"model '{card.id}' does not use the selected {', '.join(ignored)}")
            progress.log(f"model: {card.id} ({card.backend}); using {', '.join(used)}")
            backend = build_backend(card, params, progress)
            eff = params.effective(card)

        with progress.stage("build processing grid"):
            primary = [d for d in card.required_inputs() if d in inputs]
            one_of = [d for d in card.one_of_inputs() if d in inputs]
            if one_of:
                primary.append(one_of[0])
            grid = build_grid(inputs, primary, eff["resolution_m"], warn=progress.warn)
            progress.log(f"grid: {grid.width}x{grid.height} px at {grid.resolution_m:.3g} m, "
                         f"{grid.crs.to_string()}")
            if hasattr(backend, "configure"):
                backend.configure(grid.resolution_m)

            rgb_reader = None
            if "rgb" in backend.needs and "orthophoto" in inputs:
                rgb_reader = AlignedReader(inputs["orthophoto"], grid, Resampling.bilinear)
                readers.append(rgb_reader)
            elev_reader = None
            if "elevation" in backend.needs:
                source = one_of[0] if one_of else next((d for d in ("dtm", "dsm") if d in inputs), None)
                if source is None:
                    raise InputError(f"model '{card.id}' needs an elevation raster (DSM or DTM)")
                elev_reader = AlignedReader(inputs[source], grid, Resampling.bilinear)
                readers.append(elev_reader)
                progress.log(f"elevation source: {source}")
            height_inputs = {d: inputs[d] for d in ("dsm", "dtm") if d in inputs and d in used}
            fusion_wanted = bool(card.height_fusion) and params.height_weight > 0 and "dsm" in height_inputs
            if "ndsm" in backend.needs or fusion_wanted:
                height = HeightProvider(height_inputs, grid, ground_window_m=params.ground_window_m,
                                        warn=progress.warn)
                if not height.available:
                    height = None
            if "ndsm" in backend.needs and height is None:
                raise InputError(f"model '{card.id}' needs a DSM to derive height above ground")
            if card.height_fusion and params.height_weight > 0 and height is None and "dsm" not in inputs:
                progress.log("no DSM selected: running the RGB model without height fusion")

        tile = eff["tile_size"] or backend.default_tile
        if backend.fixed_size:
            tile = backend.fixed_size
        overlap = eff["overlap"]
        rows, tw, th = tile_layout(grid.width, grid.height, tile, overlap)
        n_tiles = sum(len(r) for r in rows)
        overlap_eff = max(0, min(overlap, tw - 1, th - 1))
        weights = hann2d(th, tw, overlap_eff)
        labels = np.full((grid.height, grid.width), NODATA, dtype="uint8")
        blender = BandBlender(len(card.class_ids), grid.width, grid.height, card.class_ids)
        ctx = int(backend.context or 0)
        fused_tiles = 0

        with progress.stage(f"predict {n_tiles} tiles of {tw}x{th} px (overlap {overlap_eff})"):
            done = 0
            report_every = max(1, n_tiles // 20)
            for row in rows:
                emitted = blender.begin_row(row[0].y0, th)
                if emitted:
                    y, lab = emitted
                    labels[y:y + lab.shape[0]] = lab
                for t in row:
                    data, crop = _read_tile(t, ctx, grid, backend, rgb_reader, elev_reader, height, fusion_wanted)
                    scores, valid = backend.predict(data)
                    if crop:
                        scores, valid = scores[:, crop[0]:crop[1], crop[2]:crop[3]], valid[crop[0]:crop[1], crop[2]:crop[3]]
                    if fusion_wanted and height is not None and data.ndsm is not None:
                        nd = data.ndsm if not crop else data.ndsm[crop[0]:crop[1], crop[2]:crop[3]]
                        scores = fusion.fuse(scores, nd, card.class_names, card.height_fusion,
                                             params.height_weight, eff["elevated_threshold_m"])
                        fused_tiles += 1
                    scores, valid = scores[:, :t.h, :t.w], valid[:t.h, :t.w]
                    blender.add(t, scores, weights[:t.h, :t.w], valid)
                    done += 1
                    if done % report_every == 0 or done == n_tiles:
                        progress.log(f"  {done}/{n_tiles} tiles")
                blender.end_row()
            emitted = blender.finish()
            if emitted:
                y, lab = emitted
                labels[y:y + lab.shape[0]] = lab

        with progress.stage("post-process"):
            if not (labels != NODATA).any():
                raise InputError("all pixels are nodata on the processing grid; check the inputs' coverage")
            labels = postprocess.majority_filter(labels, params.smoothing_radius_px, card.class_ids)
            min_px = int(round(params.min_segment_area_m2 / grid.pixel_area_m2))
            labels = postprocess.sieve(labels, min_px)
            keep_names = params.class_filter_names()
            if keep_names:
                unknown = [n for n in keep_names if card.class_by_name(n) is None]
                if unknown:
                    raise ParameterError(
                        f"class_filter names not in model '{card.id}': {', '.join(unknown)} "
                        f"(classes: {', '.join(card.class_names)})"
                    )
                keep_ids = {card.class_by_name(n)["id"] for n in keep_names}
                background = next(iter(sorted(card.background_ids)), None)
                labels = postprocess.apply_class_filter(labels, keep_ids, background)

        stats = output.class_stats(labels, grid, card)
        metadata = {
            "model": card.summary(),
            "model_id": card.id,
            "model_label": card.label,
            "backend": backend.describe(),
            "inputs_used": used,
            "inputs_ignored": ignored,
            # (not "height": the runner adds width/height/epsg/extent of the output)
            "height_mode": height.mode if height is not None else None,
            "height_fusion": bool(fusion_wanted and fused_tiles),
            "grid": grid.summary(),
            "tiles": {"count": n_tiles, "size": [tw, th], "overlap": overlap_eff},
            "parameters": {**{k: v for k, v in params.__dict__.items() if k != "unknown"}, **eff},
            "stats": stats,
            "legend": [{"name": c["name"], "color": c["color"], "id": c["id"]} for c in card.classes],
        }

        if want_vector:
            with progress.stage("vectorize"):
                collection = output.vectorize(
                    labels, grid, card, min_area_m2=params.min_segment_area_m2,
                    simplify_m=params.simplify_tolerance_m,
                )
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(collection, f)
                metadata["vector"] = output.vector_stats(collection)
                progress.log(f"{metadata['vector']['feature_count']} polygons")
        else:
            with progress.stage("write mask"):
                output.write_mask(output_path, labels, grid, card, {
                    "SEGMENTATION_INPUTS": ",".join(used),
                    "SEGMENTATION_RESOLUTION_M": f"{grid.resolution_m:.4f}",
                })
        metadata["progress"] = progress.summary()
        return metadata
    finally:
        for r in readers:
            r.close()
        if height is not None:
            height.close()
        for r in inputs.values():
            r.close()


def _read_tile(t, ctx, grid, backend, rgb_reader, elev_reader, height, fusion_wanted):
    """Aligned data for tile ``t`` plus ``ctx`` pixels of context on every side.

    Returns (TileData, crop) where ``crop`` is the slice that removes the
    context again (None when there is none). Data beyond the grid is
    edge-padded; tiles smaller than a backend's fixed size are reflect-padded.
    """
    x0, y0 = t.x0 - ctx, t.y0 - ctx
    x1, y1 = t.x0 + t.w + ctx, t.y0 + t.h + ctx
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(grid.width, x1), min(grid.height, y1)
    window = Window(cx0, cy0, cx1 - cx0, cy1 - cy0)
    pad = ((cy0 - y0, y1 - cy1), (cx0 - x0, x1 - cx1))

    def padded(a, is_mask=False):
        if not any(sum(p) for p in pad):
            return a
        widths = [(0, 0)] * (a.ndim - 2) + list(pad)
        return np.pad(a, widths, mode="edge")

    rgb = rgb_valid = elev = ndsm = None
    if rgb_reader is not None:
        rgb, rgb_valid = rgb_reader.read_rgb(window)
        rgb, rgb_valid = padded(rgb), padded(rgb_valid)
    if elev_reader is not None:
        elev = padded(elev_reader.read_elevation(window))
    if height is not None and ("ndsm" in backend.needs or fusion_wanted):
        ndsm = padded(height.ndsm(window))

    full_h, full_w = t.h + 2 * ctx, t.w + 2 * ctx
    fixed = backend.fixed_size
    if fixed and (full_h < fixed or full_w < fixed):
        rgb = pad_to(rgb, fixed, fixed) if rgb is not None else None
        rgb_valid = pad_to(rgb_valid, fixed, fixed) if rgb_valid is not None else None
        elev = pad_to(elev, fixed, fixed) if elev is not None else None
        ndsm = pad_to(ndsm, fixed, fixed) if ndsm is not None else None
        full_h, full_w = max(full_h, fixed), max(full_w, fixed)

    crop = (ctx, ctx + t.h, ctx, ctx + t.w) if (ctx or full_h > t.h or full_w > t.w) else None
    return TileData(rgb=rgb, rgb_valid=rgb_valid, elevation=elev, ndsm=ndsm,
                    pixel_size_m=grid.resolution_m), crop
