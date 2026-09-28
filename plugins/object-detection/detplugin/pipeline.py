"""End-to-end run: request → orthophoto → tiled inference → merged GeoJSON.

Stages (each logged and timed):

1. open and check the orthophoto
2. resolve the model card, verify the weights, load and inspect the ONNX model
3. work out the tiling (pixels, or ground metres via the GSD)
4. detect tile by tile, mapping boxes back to raster pixels
5. merge duplicates across tiles (per-class NMS), cap, write GeoJSON
"""

import os

import rasterio

from . import output, tiling, torchvision_det, yolo
from .errors import InputError, ParameterError
from .inputs import Orthophoto
from .params import Params
from .progress import Progress
from .registry import load_cards, select_model
from .session import inspect_session, load_session, verify_checksum

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

# GDAL's block cache defaults to 5% of RAM, which can be most of the sandbox's
# address-space budget on a large host; the tiled reads here need far less.
GDAL_CACHE_MB = 128

# Tiles with fewer valid pixels than this are outside the survey (alpha/nodata)
# and are skipped: faster, and no phantom boxes on the nodata collar.
MIN_VALID_FRACTION = 0.02


def run(request: dict, progress: Progress | None = None, models_dir: str | None = None) -> dict:
    with rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_MB, GDAL_NUM_THREADS=1):
        return _run(request, progress or Progress(), models_dir or MODELS_DIR)


def _run(request: dict, progress: Progress, models_dir: str) -> dict:
    params = Params.from_request(request.get("params"))
    if params.unknown:
        progress.warn(f"ignoring unknown parameters: {', '.join(sorted(params.unknown))}")
    output_path = request["output_path"]
    inputs = request.get("inputs") or {}
    ortho_path = inputs.get("orthophoto") or inputs.get("raster")
    if not ortho_path:
        raise InputError("no orthophoto was selected; object detection needs the task's orthophoto")

    ortho = None
    try:
        with progress.stage("open orthophoto", 2):
            ortho = Orthophoto(ortho_path)
            s = ortho.summary()
            progress.log(f"orthophoto: {s['width']}x{s['height']} px, {s['bands']} band(s), "
                         f"{s['dtype']}, {s['gsd_m']:.4g} m/px, {s['crs']}")
            if not ortho.is_8bit:
                progress.warn(f"orthophoto is {s['dtype']}, not 8-bit; values are clipped to 0-255 "
                              f"and detections may suffer")

        with progress.stage("load model", 5):
            cards = load_cards(models_dir)
            for mid, reason in cards.unavailable.items():
                progress.log(f"model '{mid}' not packaged: {reason}")
            card = select_model(cards, params.model)
            progress.log(f"model: {card.id} ({card.label}); licence: {card.license}")
            verify_checksum(card)
            session = load_session(card)
            spec = inspect_session(session, card)
            progress.log(f"family: {spec.family}; input {spec.input_name}{list(spec.input_size)}; "
                         f"{len(card.classes)} classes")
            eff = params.effective(card, spec.input_size)
            keep_names = params.class_filter_names()
            unknown = [n for n in keep_names if n not in card.classes]
            if unknown:
                raise ParameterError(
                    f"class_filter names not in model '{card.id}': {', '.join(unknown)} "
                    f"(classes: {', '.join(card.classes)})"
                )
            allowed = set(keep_names) or None

        tile, overlap = tiling.resolve_tiling(ortho.gsd_m, eff)
        n_tiles = tiling.count_windows(ortho.width, ortho.height, tile, overlap)
        raw: list[dict] = []
        skipped = 0
        dropped_unknown = 0
        with progress.stage(f"detect on {n_tiles} tiles of {tile} px (overlap {overlap})", 8):
            report_every = max(1, n_tiles // 25)
            for i, window in enumerate(tiling.windows(ortho.width, ortho.height, tile, overlap), 1):
                image, valid = ortho.read_tile(window)
                if valid < MIN_VALID_FRACTION:
                    skipped += 1
                else:
                    if spec.family == "torchvision":
                        tensor, scale, pad_x, pad_y = torchvision_det.preprocess(
                            image, spec.input_size, batch=spec.has_batch_dim, normalize=spec.normalize)
                    else:
                        tensor, scale, pad_x, pad_y = yolo.preprocess(
                            image, spec.input_size, batch=spec.has_batch_dim)
                    valid_w = round(image.shape[1] * scale)
                    valid_h = round(image.shape[0] * scale)

                    outputs = session.run(spec.output_names, {spec.input_name: tensor})
                    if spec.family == "torchvision":
                        decoded = torchvision_det.decode(
                            dict(zip(("boxes", "scores", "labels"), outputs)),
                            eff["confidence"], label_offset=card.label_offset)
                    else:
                        decoded = yolo.decode(outputs[0], eff["confidence"])

                    for det in decoded:
                        name = card.class_name(det["class_id"])
                        if name is None:
                            dropped_unknown += 1
                            continue
                        if allowed is not None and name not in allowed:
                            continue
                        box = tiling.map_detection(det, scale, pad_x, pad_y, valid_w, valid_h,
                                                   window, ortho.width, ortho.height)
                        if box is None:
                            continue
                        raw.append({"x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3],
                                    "class_id": det["class_id"], "confidence": det["confidence"]})
                if i % report_every == 0 or i == n_tiles:
                    progress.report(8 + 85 * i / n_tiles, f"detecting: tile {i}/{n_tiles}, {len(raw)} candidates")
                    progress.log(f"  {i}/{n_tiles} tiles, {len(raw)} candidate boxes")
            if dropped_unknown:
                progress.warn(
                    f"{dropped_unknown} detections had a class id outside the card's {len(card.classes)} "
                    f"classes and were dropped; check 'classes'/'label_offset' in the card"
                )
            if skipped:
                progress.log(f"skipped {skipped} tiles outside the orthophoto's valid area")

        with progress.stage("merge and write", 95):
            merged = yolo.nms(raw, eff["iou"])
            if len(merged) > eff["max_detections"]:
                progress.warn(f"{len(merged)} detections exceed max_detections={eff['max_detections']}; "
                              f"keeping the most confident")
                merged = merged[: eff["max_detections"]]
            px_area_m2 = ortho.gsd_m ** 2
            for det in merged:
                det["area_m2"] = round((det["x2"] - det["x1"]) * (det["y2"] - det["y1"]) * px_area_m2, 3)
            collection, counts = output.build_collection(merged, card, ortho.ds)
            output.write_collection(output_path, collection)
            progress.log(f"{len(merged)} detections: " + (
                ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"))

        metadata = {
            "model": card.summary(),
            "model_id": card.id,
            "model_label": card.label,
            "backend": spec.describe(),
            "orthophoto": ortho.summary(),
            "tiles": {"count": n_tiles, "size": tile, "overlap": overlap, "skipped_empty": skipped},
            "parameters": {**{k: v for k, v in params.__dict__.items() if k != "unknown"}, **eff},
            "counts": counts,
            "total": len(merged),
            "candidates": len(raw),
            "legend": card.legend(),
        }
        metadata["progress"] = progress.summary()
        progress.report(100, "finished")
        return metadata
    finally:
        if ortho is not None:
            ortho.close()
