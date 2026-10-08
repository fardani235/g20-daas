"""End-to-end run: request → common grid → alignment → detection → regions → output.

Stages (each logged, timed and reported as progress):

1. open the inputs; decide what can be compared (elevation pair, photo pair)
2. build the working grid (common CRS, overlap, the coarser resolution)
3. read both elevation surfaces onto it
4. horizontal co-registration: estimate the shift of the comparison survey,
   re-read it shifted when the estimate is trustworthy
5. vertical co-registration: offset (and optionally tilt) of unchanged
   ground, its noise, how constant it is across the site
6. detection: shift-tolerant difference against the level of detection
7. photos (optional, never fatal when elevation is available): vegetation
   mask, appearance-only change
8. regions: connected components, minimum area, areas / volumes / uncertainty
9. write the output (GeoJSON regions or the difference GeoTIFF) and metadata

The result metadata is written for two readers: ``summary`` and ``warnings``
are plain sentences for the run panel; everything else is the audit trail a
surveyor needs to check how each number was obtained.
"""

import dataclasses
import math

import numpy as np
import rasterio
from scipy import ndimage

from . import align, detect, output, regions as regions_mod, visual
from .errors import ChangeError, InputError
from .grid import DEFAULT_MAX_PIXELS, Raster, build_grid, read_rgb, read_surface, working_crs
from .params import Params
from .progress import Progress

# GDAL's block cache defaults to 5% of RAM, which can be most of the sandbox's
# address-space budget on a large host; the strip reads here need far less.
# In bytes: rasterio hands the value to GDALSetCacheMax64 as is, so a bare
# "128" would mean 128 bytes — no cache at all, and warps several times slower.
GDAL_CACHE_BYTES = 128 * 1024 * 1024

MIN_VALID_CELLS = 1000
MAX_TOLERANCE_CELLS = 10
APPEARANCE_MIN_AREA_M2 = 10.0

# Reliability thresholds (see _assess).
STABLE_LOW, STABLE_MEDIUM = 0.25, 0.50
AMBIGUITY_LOW, AMBIGUITY_MEDIUM = 0.5, 0.15
SET_ASIDE_MEDIUM = 0.25
NOISY_SIGMA_M = 0.15

INPUT_SURFACE = "surface"
INPUT_REFERENCE_SURFACE = "reference_surface"
INPUT_ORTHO = "orthophoto"
INPUT_REFERENCE_ORTHO = "reference_orthophoto"


def run(request: dict, progress: Progress | None = None, max_pixels: int | None = None) -> dict:
    with rasterio.Env(GDAL_CACHEMAX=GDAL_CACHE_BYTES, GDAL_NUM_THREADS=1):
        return _run(request, progress or Progress(), max_pixels or DEFAULT_MAX_PIXELS)


def _run(request: dict, progress: Progress, max_pixels: int) -> dict:
    params = Params.from_request(request.get("params"))
    if params.unknown:
        progress.warn(f"ignoring unknown parameters: {', '.join(sorted(params.unknown))}")
    output_path = request["output_path"]
    out_kind = output.kind_for(output_path)
    inputs = request.get("inputs") or {}
    context = request.get("context") or {}
    surveys = _surveys(context, params)

    rasters: dict[str, Raster] = {}
    try:
        with progress.stage("open inputs", 2):
            for name, what in ((INPUT_SURFACE, "elevation surface of this survey"),
                               (INPUT_REFERENCE_SURFACE, "elevation surface of the comparison survey")):
                if inputs.get(name):
                    rasters[name] = Raster(inputs[name], what)
                    progress.log(f"{name}: {rasters[name].describe()}")
            has_elevation = INPUT_SURFACE in rasters and INPUT_REFERENCE_SURFACE in rasters
            photo_error = None
            for name, what in ((INPUT_ORTHO, "orthophoto of this survey"),
                               (INPUT_REFERENCE_ORTHO, "orthophoto of the comparison survey")):
                if not inputs.get(name):
                    continue
                try:
                    rasters[name] = Raster(inputs[name], what, min_bands=3)
                    progress.log(f"{name}: {rasters[name].describe()}")
                except InputError as e:
                    # The photos are the secondary signal: with an elevation
                    # pair a bad orthophoto costs the photo checks, not the run.
                    photo_error = str(e)
                    if has_elevation:
                        progress.warn(f"{e}; continuing without it")
            has_photos = INPUT_ORTHO in rasters and INPUT_REFERENCE_ORTHO in rasters

            if not has_elevation and not has_photos:
                if photo_error:
                    raise InputError(photo_error)
                raise InputError(
                    "Nothing to compare: select the same kind of data for both surveys — an elevation "
                    "surface (DSM or DTM) for each, and/or an orthophoto for each."
                )
            if not has_elevation:
                if out_kind == "raster":
                    raise InputError("The elevation-difference raster needs an elevation surface (DSM or DTM) "
                                     "from both surveys.")
                if INPUT_SURFACE in rasters or INPUT_REFERENCE_SURFACE in rasters:
                    progress.warn("only one of the two surveys has an elevation surface selected, so heights "
                                  "cannot be compared")
                progress.warn("no elevation pair: only the orthophotos are compared. The result shows where the "
                              "site looks different, without volumes, and is sensitive to light and season")
            kinds = _surface_kinds(context)
            if has_elevation and kinds[0] and kinds[1] and kinds[0] != kinds[1]:
                progress.warn(f"this survey's {kinds[0].upper()} is compared with the comparison survey's "
                              f"{kinds[1].upper()}: buildings and vegetation that only one of them contains "
                              "will show up as change. Use the same kind of surface for both")

        with progress.stage("build common grid", 5):
            anchors = ((rasters[INPUT_SURFACE], rasters[INPUT_REFERENCE_SURFACE]) if has_elevation
                       else (rasters[INPUT_ORTHO], rasters[INPUT_REFERENCE_ORTHO]))
            crs = working_crs(list(anchors))
            grid = build_grid(anchors[0], anchors[1], crs, params.resolution_m,
                              max_pixels=max_pixels, warn=progress.warn)
            progress.log(f"grid: {grid.width}x{grid.height} cells at {grid.resolution_m:.3g} m, {crs.to_string()}")
            if abs(grid.meters_per_unit - 1.0) > 1e-6 and crs.is_projected:
                progress.warn("the coordinate system is not in metres; heights are assumed to be metres")
            edge_cells = grid.meters_to_cells(params.edge_buffer_m)
            base_cells = max(detect.MIN_REGION_CELLS, int(math.ceil(params.min_area_m2 / grid.cell_area_m2)))

        elev = None
        shift_cells = (0.0, 0.0)
        if has_elevation:
            elev = _compare_elevation(rasters, grid, params, progress, edge_cells)
            if elev["shift_applied"]:
                shift_cells = (elev["shift"].dx_cells, elev["shift"].dy_cells)
            valid = elev["valid"]

        # ---- photos: vegetation mask and appearance change -----------------
        vegetation = None
        appearance = None
        photo_info = {"used": False}
        photo_valid = None
        # Without an elevation pair the photo comparison is the whole result.
        do_appearance = has_photos and (params.appearance_change or not has_elevation)
        wants_photos = params.vegetation_filter or do_appearance
        if wants_photos and (INPUT_ORTHO in rasters or INPUT_REFERENCE_ORTHO in rasters):
            try:
                with progress.stage("read orthophotos", 62):
                    rgb = {}
                    if INPUT_ORTHO in rasters:
                        rgb[INPUT_ORTHO] = read_rgb(rasters[INPUT_ORTHO], grid)
                    if INPUT_REFERENCE_ORTHO in rasters:
                        rgb[INPUT_REFERENCE_ORTHO] = read_rgb(rasters[INPUT_REFERENCE_ORTHO], grid, shift_cells)
                    photo_info = {"used": True, "orthophotos": sorted(rgb)}
                    if params.vegetation_filter:
                        vegetation = np.zeros((grid.height, grid.width), dtype=bool)
                        for data, ok in rgb.values():
                            vegetation |= visual.vegetation_mask(data, ok)
                        # Canopy edges are ragged and sit a cell or two off
                        # between dates: grow the mask so their fringe is not
                        # left behind as slivers of "fill" and "cut".
                        grow = (elev["tolerance_cells"] if elev else 1) + 1
                        vegetation = ndimage.binary_dilation(vegetation, iterations=grow)
                    if has_photos:
                        photo_valid = rgb[INPUT_ORTHO][1] & rgb[INPUT_REFERENCE_ORTHO][1]
            except MemoryError:
                raise
            except Exception as e:
                if not has_elevation:
                    raise
                progress.warn(f"the orthophotos could not be used ({e}); vegetation is not separated and "
                              "appearance change is not reported")
                rgb, vegetation, photo_valid = {}, None, None
                photo_info = {"used": False, "error": str(e)}
        else:
            rgb = {}

        if not has_elevation:
            valid = detect.erode_valid(photo_valid, edge_cells)
            if int(valid.sum()) < MIN_VALID_CELLS:
                raise InputError("The two orthophotos share too little valid image to compare.")

        with progress.stage("detect change", 70):
            if has_elevation:
                classes = detect.classify(elev["tolerant"], elev["dz"], valid,
                                          elev["lod"]["level_of_detection_m"], vegetation,
                                          min_cells=base_cells,
                                          reach=elev["tolerance_cells"] + 1 if elev["tolerance_cells"] else 0)
                elev["tolerant"] = None
                if vegetation is None and params.vegetation_filter:
                    progress.warn("no orthophoto was available, so vegetation could not be separated: on a "
                                  "surface model (DSM) trees and crops that grew or were cut count as fill and cut")
            else:
                classes = np.zeros((grid.height, grid.width), dtype="uint8")

            if do_appearance and photo_valid is not None:
                try:
                    reference = valid & (classes == detect.NONE)
                    if vegetation is not None:
                        reference &= ~vegetation
                    flagged, info = visual.appearance_change(
                        rgb[_before_key(params)][0], rgb[_before_key(params)][1],
                        rgb[_after_key(params)][0], rgb[_after_key(params)][1], reference)
                    flagged &= reference
                    share = float(flagged.sum()) / max(1, int(valid.sum()))
                    info["flagged_share"] = round(share, 4)
                    if share > visual.MAX_APPEARANCE_SHARE:
                        progress.warn(f"the two orthophotos look different over {share:.0%} of the site "
                                      "(light, season or exposure), so appearance change is not reported")
                        info["dropped"] = True
                    else:
                        classes[flagged] = detect.APPEARANCE
                    appearance = info
                    del flagged
                except MemoryError:
                    raise
                except Exception as e:
                    if not has_elevation:
                        raise
                    progress.warn(f"appearance change could not be computed ({e})")
            rgb = None

        with progress.stage("measure regions", 80):
            appearance_cells = max(base_cells, int(math.ceil(
                max(params.min_area_m2, APPEARANCE_MIN_AREA_M2) / grid.cell_area_m2)))
            labels, regs = detect.label_regions(classes, {
                detect.FILL: base_cells, detect.CUT: base_cells,
                detect.VEGETATION: base_cells, detect.APPEARANCE: appearance_cells,
            })
            del classes
            valid_cells = int(valid.sum())
            sigma_noise = elev["sigma_noise"] if elev else 0.0
            sigma_sys = elev["sigma_sys"] if elev else 0.0
            cells_per_sample = elev["noise"]["cells_per_sample"] if elev else 1.0
            ring_cells = min(regions_mod.EDGE_RING_MAX_CELLS, grid.meters_to_cells(regions_mod.EDGE_RING_M))
            regs = regions_mod.measure_regions(labels, regs, elev["dz"] if elev else None, grid.cell_area_m2,
                                               sigma_noise, sigma_sys, cells_per_sample, ring_cells)
            tot = regions_mod.totals(regs, valid_cells, grid.cell_area_m2, sigma_sys)
            progress.log(f"{len(regs)} regions; changed {tot['changed_percent']:.1f}% of "
                         f"{tot['compared_area_m2']:.0f} m²; fill {tot['fill_volume_m3']:.1f} m³, "
                         f"cut {tot['cut_volume_m3']:.1f} m³")

        with progress.stage("write output", 92):
            if out_kind == "raster":
                if params.mask_insignificant:
                    measured = np.zeros(len(regs) + 1, dtype=bool)
                    measured[[r["id"] for r in regs if r["class"] != detect.APPEARANCE]] = True
                    keep = measured[labels]
                else:
                    keep = valid
                output.write_difference(output_path, elev["dz"], keep, grid, {
                    "vertical_offset_removed_m": round(elev["correction_offset"], 4),
                    "level_of_detection_m": round(elev["lod"]["level_of_detection_m"], 4),
                    "content": "significant change only" if params.mask_insignificant else "all compared cells",
                })
                vector_info = {"regions": len(regs)}
            else:
                collection, vector_info = regions_mod.feature_collection(labels, regs, grid)
                if vector_info["omitted_small_regions"]:
                    progress.warn(f"{vector_info['omitted_small_regions']} of the smallest regions are not drawn "
                                  f"(the layer is limited to {regions_mod.MAX_FEATURES}); the totals include them")
                output.write_geojson(output_path, collection)

        reliability = _assess(elev, params, kinds) if elev else {
            "grade": "low",
            "reasons": ["only the photos were compared; they cannot measure height or volume"],
        }
        metadata = {
            "mode": "elevation" if has_elevation else "appearance-only",
            "surveys": surveys,
            "inputs_used": sorted(rasters),
            "surface_kinds": {"this_survey": kinds[0], "comparison_survey": kinds[1]},
            "grid": grid.summary(),
            "change": _round(tot),
            "reliability": reliability,
            "alignment": _alignment_metadata(elev, params) if elev else None,
            "detection": _detection_metadata(elev, params, grid, base_cells) if elev else None,
            "photos": {**photo_info, "vegetation_separated": vegetation is not None, "appearance": appearance},
            "vector": vector_info,
            "legend": regions_mod.legend(),
            "parameters": params.raw(),
            "progress": progress.summary(),
        }
        metadata["warnings"] = list(progress.warnings)
        metadata["summary"] = _summary(metadata, elev)
        progress.report(100, "finished")
        return metadata
    finally:
        for r in rasters.values():
            r.close()


# --------------------------------------------------------------------------
# Elevation comparison
# --------------------------------------------------------------------------

def _compare_elevation(rasters, grid, params: Params, progress: Progress, edge_cells: int) -> dict:
    primary_r, reference_r = rasters[INPUT_SURFACE], rasters[INPUT_REFERENCE_SURFACE]
    res_m = grid.resolution_m

    with progress.stage("read elevation surfaces", 8):
        primary = read_surface(primary_r, grid)
        reference = read_surface(reference_r, grid)

    with progress.stage("horizontal alignment", 28):
        shift = align.estimate_horizontal_shift(primary, reference, res_m, params.max_shift_m)
        applied = False
        if shift.estimated:
            progress.log(f"comparison survey sits {shift.east_m:+.2f} m east, {shift.north_m:+.2f} m north "
                         f"(agreement gain x{shift.gain:.2f}; {shift.note or 'significant'})")
        else:
            progress.log(f"horizontal shift not estimated: {shift.note}")
        if shift.significant:
            if params.horizontal_alignment == "auto" and not shift.at_search_limit:
                reference = None
                reference = read_surface(reference_r, grid, shift=(shift.dx_cells, shift.dy_cells))
                applied = True
            elif shift.at_search_limit:
                progress.warn(f"the two surveys are shifted against each other by at least "
                              f"{shift.magnitude_m:.1f} m, more than the {shift.search_radius_m:.1f} m searched, "
                              "so the shift was not corrected. Edges of buildings and slopes may show as change; "
                              "raise max_shift_m or re-process with ground control points")
            else:
                progress.warn(f"the two surveys are shifted against each other by {shift.magnitude_m:.2f} m and "
                              "horizontal alignment is off; the registration tolerance was widened instead")

    before, after = (reference, primary) if params.reference_role == "before" else (primary, reference)
    del primary, reference

    with progress.stage("vertical alignment", 42):
        valid = detect.erode_valid(np.isfinite(before) & np.isfinite(after), edge_cells)
        if int(valid.sum()) < MIN_VALID_CELLS:
            raise InputError("The two elevation surfaces share too little valid ground to compare "
                             "(after trimming the edge buffer). Lower edge_buffer_m or pick surveys that overlap more.")
        dz = after - before
        dz[~valid] = np.nan
        if not np.nanmax(np.abs(dz)) > 0:
            # Two independent surveys never agree to the last bit. This is the
            # same raster twice — e.g. a platform that does not know comparison
            # inputs and fed both from one survey.
            raise InputError("The two elevation surfaces are identical: the same survey was given twice. "
                             "Choose a different survey to compare with.")
        try:
            fit = align.fit_stable_peak(dz[valid])
        except ValueError as e:
            raise InputError(f"The height offset between the surveys could not be estimated: {e}") from e
        progress.log(f"unchanged ground: after − before = {fit.offset:+.3f} m, noise {fit.sigma:.3f} m, "
                     f"{fit.stable_fraction:.0%} of the overlap")

        dz -= np.float32(fit.offset)                     # residual about the stable peak
        constant = fit.offset                            # what is subtracted everywhere
        tiles = align.analyse_tiles(dz, fit.sigma)
        residual_tiles = tiles
        tilt_removed = False
        centre_offset = fit.offset                       # offset at the site centre, for reporting
        if params.vertical_alignment == "tilt":
            if tiles.model == "plane":
                surface = align.plane_surface(tiles.plane, dz.shape)
                dz -= surface
                after -= surface
                del surface
                tilt_removed = True
                # With the tilt gone the peak is sharp again: measure it afresh.
                refit = align.fit_stable_peak(dz[valid])
                dz -= np.float32(refit.offset)
                constant += refit.offset
                centre_offset = constant + tiles.plane[0]
                # Report the refit in the same frame as the first fit (absolute after − before).
                second = None if refit.second_offset is None else refit.second_offset - refit.offset + centre_offset
                fit = dataclasses.replace(refit, offset=centre_offset, second_offset=second)
                residual_tiles = align.analyse_tiles(dz, refit.sigma)
                progress.log(f"tilt of {tiles.tilt_m:.3f} m across the site removed")
            elif tiles.valid_tiles < align.PLANE_MIN_TILES:
                progress.warn("tilt correction was requested but the site is too small to fit a tilt; "
                              "only the constant offset was removed")
            else:
                progress.log("no tilt found; only the constant offset was removed")
        # Noise inside a tile is not smeared by a tilt or dome; what varies
        # between tiles is carried separately as the systematic term.
        sigma_noise = residual_tiles.noise if residual_tiles.sigma_sys_measured else fit.sigma
        sigma_sys = residual_tiles.sigma_sys
        noise = align.block_noise(dz, sigma_noise)

        if params.vertical_alignment == "none":
            # The user vouches for both surveys' heights (shared ground
            # control): put the offset back. If most of the site then differs,
            # that is reported as change — and flagged, in case the surveys
            # are not on a common height datum after all.
            dz += np.float32(constant)
            correction_offset = 0.0
            if abs(fit.offset) > max(2.0 * sigma_noise, detect.MIN_AUTO_LOD_M):
                progress.warn(f"the largest part of the overlap differs by {fit.offset:+.2f} m between the "
                              "surveys. With heights trusted as they are, that whole area counts as change; "
                              "if the surveys are not tied to the same ground control, use automatic "
                              "height alignment instead")
        else:
            after -= np.float32(constant)
            correction_offset = centre_offset

        lod = detect.level_of_detection(params.k, sigma_noise, sigma_sys, params.min_height_change_m)
        progress.log(f"noise {sigma_noise:.3f} m, systematic uncertainty {sigma_sys:.3f} m; level of detection "
                     f"{lod['level_of_detection_m']:.3f} m ({params.confidence}% confidence)")
        tilt_significant = (not tilt_removed) and tiles.model == "plane" and \
            tiles.tilt_m > max(2.0 * tiles.noise, detect.MIN_AUTO_LOD_M)
        if tilt_significant:
            progress.warn(f"the height offset between the surveys is not constant: it drifts by about "
                          f"{tiles.tilt_m:.2f} m across the site (a tilt, typical without ground control). "
                          "The bar for counting a change was raised accordingly; set vertical_alignment "
                          "to tilt to remove it")

    with progress.stage("shift-tolerant difference", 52):
        tolerance_m = params.registration_tolerance_m
        if tolerance_m < 0:
            tolerance_m = res_m
            if shift.significant and not applied:
                tolerance_m = max(res_m, shift.magnitude_m)
        radius = min(MAX_TOLERANCE_CELLS, grid.meters_to_cells(tolerance_m)) if tolerance_m > 0 else 0
        tolerant = detect.tolerant_difference(before, after, radius)
    del before, after

    return {
        "dz": dz, "valid": valid, "tolerant": tolerant, "fit": fit, "tiles": tiles,
        "residual_tiles": residual_tiles, "tilt_removed": tilt_removed, "tilt_significant": tilt_significant,
        "correction_offset": float(correction_offset), "sigma_noise": float(sigma_noise),
        "sigma_sys": float(sigma_sys), "noise": noise,
        "lod": lod, "shift": shift, "shift_applied": applied,
        "tolerance_m": float(radius * res_m), "tolerance_cells": int(radius),
    }


def _assess(elev: dict, params: Params, kinds) -> dict:
    """Grade how far the numbers can be trusted, with the reasons in plain words."""
    fit, tiles, shift = elev["fit"], elev["residual_tiles"], elev["shift"]
    low, medium = [], []
    if fit.stable_fraction < STABLE_LOW:
        low.append(f"only {fit.stable_fraction:.0%} of the overlap looks unchanged, so a change that covers "
                   "most of the site cannot be told apart from a height offset between the surveys")
    elif fit.stable_fraction < STABLE_MEDIUM:
        medium.append(f"only {fit.stable_fraction:.0%} of the overlap looks unchanged, which leaves little "
                      "ground to align the surveys on")
    if fit.second_offset is not None and params.vertical_alignment != "none":
        gap = fit.second_offset - fit.offset
        if fit.ambiguity > AMBIGUITY_LOW:
            low.append(f"two height offsets fit the data almost equally well ({fit.offset:+.2f} m and "
                       f"{fit.second_offset:+.2f} m); the more common one was taken as unchanged ground, and "
                       f"if it is the wrong one the result is inverted and every volume is off by "
                       f"{abs(gap):.2f} m times the area")
        elif fit.ambiguity > AMBIGUITY_MEDIUM:
            medium.append(f"a second, smaller part of the site sits {gap:+.2f} m from the level taken as "
                          "unchanged ground; if that part is the ground that did not move, the result is inverted")
    if kinds[0] and kinds[1] and kinds[0] != kinds[1]:
        low.append("a surface model (DSM) is compared with a terrain model (DTM)")
    if not tiles.sigma_sys_measured:
        medium.append("too little unchanged ground is spread over the site to check that the offset is constant")
    elif tiles.set_aside_fraction > SET_ASIDE_MEDIUM:
        medium.append(f"about {tiles.set_aside_fraction:.0%} of the site sits at a different level than the "
                      "rest and is reported as change; a survey warped by missing ground control would look the same")
    if elev["tilt_significant"]:
        medium.append(f"the offset drifts by about {elev['tiles'].tilt_m:.2f} m across the site")
    if shift.significant and not elev["shift_applied"]:
        medium.append(f"a horizontal shift of about {shift.magnitude_m:.1f} m between the surveys was not corrected")
    if elev["sigma_noise"] > NOISY_SIGMA_M:
        medium.append(f"the surfaces are noisy ({elev['sigma_noise']:.2f} m on unchanged ground)")
    if params.vertical_alignment == "none" and abs(fit.offset) > max(2.0 * elev["sigma_noise"], detect.MIN_AUTO_LOD_M):
        medium.append(f"heights were taken as they are, and the largest part of the overlap differs by "
                      f"{fit.offset:+.2f} m: real only if both surveys share the same ground control")
    grade = "low" if low else "medium" if medium else "high"
    return {"grade": grade, "reasons": low + medium}


def _alignment_metadata(elev: dict, params: Params) -> dict:
    fit = elev["fit"]
    return {
        "vertical": {
            "method": {"auto": "constant offset", "tilt": "offset and tilt", "none": "none (heights trusted)"}[
                params.vertical_alignment],
            "offset_removed_m": round(elev["correction_offset"], 4),
            "offset_of_unchanged_ground_m": round(fit.offset, 4),
            "tilt_removed": elev["tilt_removed"],
            **fit.as_dict(),
            "noise_sigma_m": round(elev["sigma_noise"], 4),
            "systematic_sigma_m": round(elev["sigma_sys"], 4),
            "consistency": elev["tiles"].as_dict(),
            "after_correction": elev["residual_tiles"].as_dict() if elev["tilt_removed"] else None,
            "noise_correlation": {k: (round(v, 4) if isinstance(v, float) else v)
                                  for k, v in elev["noise"].items()},
        },
        "horizontal": {**elev["shift"].as_dict(), "applied": elev["shift_applied"],
                       "method": params.horizontal_alignment},
    }


def _detection_metadata(elev: dict, params: Params, grid, base_cells: int) -> dict:
    return {
        **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in elev["lod"].items()},
        "confidence_percent": int(params.confidence),
        "registration_tolerance_m": round(elev["tolerance_m"], 3),
        "registration_tolerance_cells": elev["tolerance_cells"],
        "min_region_area_m2": round(base_cells * grid.cell_area_m2, 3),
        "edge_buffer_m": params.edge_buffer_m,
    }


# --------------------------------------------------------------------------
# Context and presentation helpers
# --------------------------------------------------------------------------

def _surface_kinds(context: dict) -> tuple[str | None, str | None]:
    """Which dataset (dsm / dtm) feeds each surface input, when the platform says so."""
    info = context.get("inputs") if isinstance(context.get("inputs"), dict) else {}

    def kind(name):
        entry = info.get(name)
        return entry.get("dataset") if isinstance(entry, dict) else None
    return kind(INPUT_SURFACE), kind(INPUT_REFERENCE_SURFACE)


def _surveys(context: dict, params: Params) -> dict:
    def describe(key, fallback):
        task = context.get(key) if isinstance(context.get(key), dict) else {}
        return {"task": task.get("name"), "title": task.get("title") or task.get("name") or fallback}
    this = describe("task", "this survey")
    other = describe("reference_task", "comparison survey")
    before, after = (other, this) if params.reference_role == "before" else (this, other)
    return {"before": before, "after": after, "result_shown_on": this["task"]}


def _before_key(params: Params) -> str:
    return INPUT_REFERENCE_ORTHO if params.reference_role == "before" else INPUT_ORTHO


def _after_key(params: Params) -> str:
    return INPUT_ORTHO if params.reference_role == "before" else INPUT_REFERENCE_ORTHO


def _round(values: dict) -> dict:
    return {k: (round(v, 6 if k.endswith("_share") else 4) if isinstance(v, float) else v)
            for k, v in values.items()}


def _num(value: float, unit: str = "") -> str:
    digits = 0 if abs(value) >= 100 else 1 if abs(value) >= 10 else 2
    text = f"{value:,.{digits}f}"
    return f"{text} {unit}".strip()


def _pm(value: float, sigma: float, unit: str) -> str:
    digits = 0 if abs(value) >= 100 else 1 if abs(value) >= 10 else 2
    return f"{value:,.{digits}f} {unit} ± {sigma:,.{digits}f}"


def _summary(metadata: dict, elev: dict | None) -> list[dict]:
    """The handful of numbers a non-expert needs, as label/value rows for the run panel."""
    tot = metadata["change"]
    surveys = metadata["surveys"]
    rows = [{"label": "Compared", "value": f"{surveys['before']['title']} (before) → "
                                           f"{surveys['after']['title']} (after)"}]
    rows.append({"label": "Area both surveys cover", "value": _num(tot["compared_area_m2"], "m²")})
    if elev is None:
        rows.append({"label": "Looks different", "value":
                     f"{100 * tot['appearance_share']:.1f}% of that area ({_num(tot['appearance_area_m2'], 'm²')})"})
        rows.append({"label": "Volumes", "value": "not available without elevation surfaces"})
        rows.append({"label": "Reliability", "value": "Low — photos only"})
        return rows

    rows.append({"label": "Changed", "value":
                 f"{tot['changed_percent']:.1f}% of that area ({_num(tot['changed_area_m2'], 'm²')})"})
    rows.append({"label": "Fill", "value": _pm(tot["fill_volume_m3"], tot["fill_volume_sigma_m3"], "m³")})
    rows.append({"label": "Cut", "value": _pm(tot["cut_volume_m3"], tot["cut_volume_sigma_m3"], "m³")})
    net = tot["net_volume_m3"]
    direction = "balanced" if abs(net) <= tot["net_volume_sigma_m3"] else \
        "more fill than cut" if net > 0 else "more cut than fill"
    rows.append({"label": "Net", "value":
                 f"{'+' if net > 0 else ''}{_pm(net, tot['net_volume_sigma_m3'], 'm³')} ({direction})"})
    lod = metadata["detection"]
    rows.append({"label": "Smallest height change counted", "value":
                 f"{lod['level_of_detection_m']:.2f} m ({lod['confidence_percent']}% confidence)"})
    vertical = metadata["alignment"]["vertical"]
    if vertical["method"].startswith("none"):
        rows.append({"label": "Height offset between surveys", "value":
                     f"{vertical['offset_of_unchanged_ground_m']:+.2f} m over most of the overlap (not removed)"})
    else:
        rows.append({"label": "Height offset removed", "value":
                     f"{vertical['offset_removed_m']:+.2f} m"
                     + (" and a tilt" if vertical["tilt_removed"] else "")
                     + f" (noise ±{vertical['noise_sigma_m']:.2f} m)"})
    horizontal = metadata["alignment"]["horizontal"]
    if horizontal["applied"]:
        rows.append({"label": "Horizontal shift corrected", "value": f"{horizontal['magnitude_m']:.2f} m"})
    if tot["vegetation_area_m2"] > 0:
        rows.append({"label": "Vegetation (not counted)", "value": _num(tot["vegetation_area_m2"], "m²")})
    if tot["appearance_area_m2"] > 0:
        rows.append({"label": "Looks different, same height", "value": _num(tot["appearance_area_m2"], "m²")})
    reliability = metadata["reliability"]
    reason = f" — {reliability['reasons'][0]}" if reliability["reasons"] else ""
    rows.append({"label": "Reliability", "value": f"{reliability['grade'].capitalize()}{reason}"})
    return rows


__all__ = ["run", "ChangeError"]
