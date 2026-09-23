"""Workflow selection and the terrain / point-cloud / points workflows.

``run(request, progress)`` is the whole plugin: detect which task outputs
were supplied, pick a workflow, produce one GLB, return metadata.

Workflows
---------
``terrain``      DSM (or DTM) heightfield -> triangulated, decimated mesh,
                 draped with the orthophoto. Holes in the DSM are filled from
                 the DTM, then from the point cloud, then by interpolation.
``point-cloud``  LAZ -> heightfield (max/mean/min Z per cell) -> as above;
                 colour from the orthophoto or the points' own RGB.
``points``       LAZ -> evenly subsampled, coloured point primitive.
``mesh``         existing ODM textured mesh (GLB, or OBJ/glTF zip) -> textures
                 shrunk to a budget, geometry re-encoded (Draco), georef added.
``auto``         ``mesh`` if a model exists, else ``terrain`` if a DSM/DTM
                 exists, else ``point-cloud``.
"""

import os

import numpy as np
from rasterio.enums import Resampling

from . import gltf, mesh as meshing, pointcloud, texture
from .crs import Georef, local_origin, require_projected
from .errors import InputError
from .heightfield import (
    auto_resolution, bounds_in, fill_holes, intersect_bounds, plan_grid, raster_info, read_on_grid, slope_stats,
    valid_fraction,
)
from .params import Params, parse

INPUT_NAMES = ("orthophoto", "dsm", "dtm", "point_cloud", "model")

# Cells missing after the DTM donor above which reading the point cloud is worth it.
_POINT_FILL_MIN_FRACTION = 0.001
_FILL_SEARCH_PX = 64
# Interpolate only enclosed gaps up to this area; larger holes are the outside of
# the flight area or water and stay open rather than being bridged with invented terrain.
_MAX_HOLE_M2 = 100.0
_MAX_COLOUR_CELLS = 4096 * 4096
# Point colours are binned no finer than this multiple of the mean point spacing,
# so nearly every colour cell receives a point.
_COLOUR_SPACING_FACTOR = 1.5
_COLOUR_GAP_PX = 3


def detect_inputs(inputs: dict | None) -> dict[str, str]:
    found = {}
    for name in INPUT_NAMES:
        path = (inputs or {}).get(name)
        if path and os.path.isfile(path) and os.path.getsize(path) > 0:
            found[name] = path
    return found


def choose_workflow(requested: str, available: dict[str, str]) -> str:
    if requested == "auto":
        if "model" in available:
            return "mesh"
        if "dsm" in available or "dtm" in available:
            return "terrain"
        if "point_cloud" in available:
            return "point-cloud"
        raise InputError("select at least a DSM/DTM, a point cloud or a 3D model to reconstruct from")
    needs = {"terrain": ("dsm", "dtm"), "point-cloud": ("point_cloud",), "points": ("point_cloud",), "mesh": ("model",)}
    if not any(k in available for k in needs[requested]):
        raise InputError(f"workflow '{requested}' needs {' or '.join(needs[requested])} — select it in the run dialog")
    return requested


def run(request: dict, progress) -> dict:
    params = parse(request.get("params"), request.get("context"))
    available = detect_inputs(request.get("inputs"))
    workflow = choose_workflow(params.workflow, available)
    progress.log(f"inputs: {', '.join(sorted(available)) or 'none'}; workflow: {workflow}; "
                 f"quality: {params.quality} ({params.quality_reason})")
    progress.report(2, f"{workflow}: starting")

    if workflow == "mesh":
        from .optimize import optimize_model
        result = optimize_model(available["model"], params, request.get("context") or {}, progress)
    elif workflow == "points":
        result = points_workflow(available, params, progress)
    else:
        result = surface_workflow(workflow, available, params, progress)

    with progress.stage("writing GLB", 92):
        size = result["builder"].write(request["output_path"])
    progress.report(100, "finished")

    metadata = {
        "plugin": "3d-reconstruction",
        "workflow": workflow,
        "inputs_used": result["inputs_used"],
        "params": params.summary(),
        "georef": result["georef"].to_extras() if result.get("georef") else None,
        "output_bytes": size,
        **{k: v for k, v in result.items() if k not in ("builder", "inputs_used", "georef")},
        "progress": progress.summary(),
    }
    progress.log(f"wrote {size / 1e6:.1f} MB")
    return metadata


# --------------------------------------------------------------------------
# Surface workflows (terrain from rasters, or from the point cloud)
# --------------------------------------------------------------------------

def surface_workflow(workflow: str, available: dict[str, str], params: Params, progress) -> dict:
    inputs_used = {}
    points_info = None
    classes = pointcloud.parse_classes(params.point_classes)

    # 1. Footprint, CRS and grid come from the elevation source.
    if workflow == "terrain":
        surface_name = params.surface if params.surface in available else ("dsm" if "dsm" in available else "dtm")
        if surface_name != params.surface:
            progress.warn(f"{params.surface.upper()} not available, using the {surface_name.upper()}")
        src = raster_info(available[surface_name])
        crs = require_projected(src["crs"], surface_name.upper())
        bounds, native_res = src["bounds"], src["res"]
        coverage = valid_fraction(available[surface_name])
        inputs_used["surface"] = surface_name
    else:
        points_info = pointcloud.info(available["point_cloud"])
        crs = require_projected(points_info["crs"], "point cloud")
        bounds = points_info["bounds"]
        area = max(bounds[2] - bounds[0], 1e-9) * max(bounds[3] - bounds[1], 1e-9)
        # ~2x the mean point spacing keeps most cells populated.
        native_res = 2.0 * float(np.sqrt(area / max(points_info["count"], 1)))
        coverage = 1.0  # unknown until the cloud is gridded; the budget is a ceiling anyway
        inputs_used["surface"] = "point_cloud"
        surface_name = "point_cloud"

    res = params.resolution_m or auto_resolution(bounds, native_res, params.max_triangles, coverage)
    grid, coarsened = plan_grid(crs, bounds, res)
    if coarsened:
        progress.warn(f"grid coarsened to {grid.res:.2f} m to stay within the memory limit")
    progress.log(f"grid {grid.width}x{grid.height} @ {grid.res:.3f} m (native {native_res:.3f} m, "
                 f"coverage {coverage:.0%}), budget {params.max_triangles} triangles")

    # 2. Texture grid: finer than the mesh grid, capped by the profile and the orthophoto's own resolution.
    ortho = None
    if "orthophoto" in available and params.texture_source in ("auto", "orthophoto"):
        ortho = raster_info(available["orthophoto"])
        ob = bounds_in(ortho["crs"], ortho["bounds"], crs) if ortho["crs"] else None
        if ob is None or intersect_bounds(ob, grid.bounds) is None:
            progress.warn("orthophoto does not overlap the surface; ignoring it")
            ortho = None
    tex_grid = grid.texture_grid(params.texture_size, native_res=ortho["res"] if ortho else None)
    progress.log(f"texture {tex_grid.width}x{tex_grid.height} @ {tex_grid.res:.3f} m")
    # Point colours: bin no finer than the point spacing allows, and cap the grid
    # (float32 accumulators) so it stays a few hundred MB.
    colour_grid = None
    if "point_cloud" in available:
        pinfo = points_info or pointcloud.info(available["point_cloud"])
        parea = max(pinfo["bounds"][2] - pinfo["bounds"][0], 1e-9) * max(pinfo["bounds"][3] - pinfo["bounds"][1], 1e-9)
        spacing = float(np.sqrt(parea / max(pinfo["count"], 1)))
        colour_grid = grid.texture_grid(min(params.texture_size, 4096), native_res=_COLOUR_SPACING_FACTOR * spacing)
        if colour_grid.cells > _MAX_COLOUR_CELLS:
            colour_grid = grid.texture_grid(4096, native_res=_COLOUR_SPACING_FACTOR * spacing)

    # 3. Elevation.
    point_raster = None
    need_point_colour = params.texture_source == "point-cloud" or (params.texture_source == "auto" and ortho is None)
    if workflow == "terrain":
        with progress.stage(f"reading {surface_name.upper()}", 8):
            z = read_on_grid(available[surface_name], grid, resampling=Resampling.average)
        donors = []
        other = "dtm" if surface_name == "dsm" else "dsm"
        if params.fill_holes and other in available and np.ma.getmaskarray(z).any():
            with progress.stage(f"reading {other.upper()} to fill holes", 14):
                try:
                    donors.append(read_on_grid(available[other], grid, resampling=Resampling.average))
                    inputs_used["hole_donor"] = other
                except InputError as e:
                    progress.warn(str(e))
        z, fill_report = fill_holes(z, donors=donors, max_search_px=0)
        holes = int(np.ma.getmaskarray(z).sum())
        wants_points = "point_cloud" in available and (
            need_point_colour or (params.fill_holes and holes > _POINT_FILL_MIN_FRACTION * grid.cells))
        if wants_points:
            with progress.stage("gridding point cloud", 20):
                point_raster = pointcloud.rasterize(
                    available["point_cloud"], grid, statistic="max", classes=classes,
                    want_rgb=need_point_colour, color_grid=colour_grid if need_point_colour else None,
                    on_progress=lambda f: progress.report(20 + 18 * f))
            inputs_used["point_cloud"] = "holes" + (" + colour" if need_point_colour else "")
            if params.fill_holes and holes:
                z, r2 = fill_holes(z, donors=[point_raster["z"]], max_search_px=0)
                fill_report["from_donors"] += r2["from_donors"]
    else:
        with progress.stage("gridding point cloud", 8):
            point_raster = pointcloud.rasterize(
                available["point_cloud"], grid, statistic=params.point_statistic, classes=classes,
                want_rgb=need_point_colour, color_grid=colour_grid if need_point_colour else None,
                on_progress=lambda f: progress.report(8 + 30 * f))
        z = point_raster["z"]
        fill_report = {"holes": int(np.ma.getmaskarray(z).sum()), "from_donors": 0, "interpolated": 0}
        inputs_used["point_cloud"] = f"surface ({params.point_statistic} Z)"
        if point_raster["classes_seen"]:
            progress.log(f"classes present: {pointcloud.describe_classes(point_raster['classes_seen'])}"
                         + (f"; using {sorted(classes)}" if classes else ""))
        donors = []
        if params.fill_holes and np.ma.getmaskarray(z).any():
            for name in ("dtm", "dsm"):
                if name in available:
                    with progress.stage(f"reading {name.upper()} to fill holes", 40):
                        try:
                            donors.append(read_on_grid(available[name], grid, resampling=Resampling.average))
                            inputs_used["hole_donor"] = name
                            break
                        except InputError as e:
                            progress.warn(str(e))
            z, r2 = fill_holes(z, donors=donors, max_search_px=0)
            fill_report["from_donors"] += r2["from_donors"]

    if params.fill_holes and np.ma.getmaskarray(z).any():
        with progress.stage("interpolating small gaps", 42):
            max_hole = max(1, int(_MAX_HOLE_M2 / (grid.res * grid.res)))
            z, r3 = fill_holes(z, max_search_px=_FILL_SEARCH_PX, max_hole_cells=max_hole)
            fill_report["interpolated"] += r3["interpolated"]
    fill_report["left_open"] = int(np.ma.getmaskarray(z).sum())
    valid = ~np.ma.getmaskarray(z)
    if valid.sum() < 4:
        raise InputError("the surface has no valid elevation cells")
    zdata = np.ma.getdata(z)
    zmin, zmax = float(zdata[valid].min()), float(zdata[valid].max())

    # 4. Mesh.
    with progress.stage("triangulating", 46):
        vertices, faces = meshing.triangulate(z, grid)
    before = int(faces.shape[0])
    if before > params.max_triangles:
        with progress.stage(f"decimating {before} -> {params.max_triangles} triangles", 52):
            vertices, faces = meshing.simplify(vertices, faces, params.max_triangles)
    if faces.shape[0] == 0:
        raise InputError("triangulation produced no faces (surface too small or entirely nodata)")
    progress.log(f"mesh: {faces.shape[0]} triangles, {vertices.shape[0]} vertices")

    # 5. Texture: shaded relief as the base, point colours and then the orthophoto painted over it.
    with progress.stage("texturing", 66):
        size = (tex_grid.width, tex_grid.height)
        rgb, _ = texture.resample_rgb(texture.shaded_relief(z, grid.res), None, size)
        coverage = {}
        if point_raster is not None and point_raster.get("rgb") is not None and params.texture_source not in ("orthophoto", "shaded-relief"):
            prgb, pvalid = texture.fill_rgb_gaps(point_raster["rgb"], point_raster["rgb_valid"], _COLOUR_GAP_PX)
            if prgb.shape[:2] != (tex_grid.height, tex_grid.width):
                prgb, pvalid = texture.resample_rgb(prgb, pvalid, size)
            rgb, cov = texture.compose(rgb, [("point-cloud", prgb, pvalid)])
            coverage["point-cloud"] = cov["point-cloud"]
            point_raster["rgb"] = point_raster["rgb_valid"] = None
            del prgb, pvalid
        if ortho is not None and params.texture_source != "shaded-relief":
            try:
                coverage["orthophoto"] = round(texture.paint_orthophoto(
                    available["orthophoto"], tex_grid, rgb, on_progress=lambda f: progress.report(66 + 12 * f)), 4)
                inputs_used["orthophoto"] = "texture"
            except InputError as e:
                progress.warn(f"orthophoto unusable: {e}")
        coverage["shaded-relief"] = round(max(0.0, 1.0 - sum(coverage.values())), 4)
        jpeg = texture.encode_jpeg(rgb, params.texture_quality)
        del rgb
    progress.log(f"texture {tex_grid.width}x{tex_grid.height} JPEG {len(jpeg) / 1e6:.1f} MB; coverage {coverage}")

    # 6. GLB.
    origin = local_origin(grid.bounds, zmin)
    georef = Georef(crs=crs, origin=origin, bounds=grid.bounds, z_range=(zmin, zmax), source=surface_name,
                    extra={"resolution_m": grid.res})
    with progress.stage("encoding geometry", 80):
        builder = gltf.GlbBuilder()
        img = builder.add_image(jpeg, "image/jpeg")
        material = builder.add_material(builder.add_texture(img), name=surface_name)
        prim = builder.add_primitive(
            meshing.localize(vertices, origin), indices=faces, uvs=meshing.planar_uvs(vertices, tex_grid.bounds),
            material=material, draco={"bits": 14, "level": 7} if params.draco else None)
        builder.add_node(builder.add_mesh([prim], name="surface"), name="surface")
        builder.set_georef(georef.to_extras())

    return {
        "builder": builder, "inputs_used": inputs_used, "georef": georef,
        "mesh": {"triangles": int(faces.shape[0]), "vertices": int(vertices.shape[0]),
                 "triangles_before_decimation": before, "compression": params.compression},
        "surface": {"grid": [grid.width, grid.height], "resolution_m": round(grid.res, 4),
                    "z_range": [round(zmin, 3), round(zmax, 3)], "holes": fill_report,
                    **slope_stats(np.where(valid, zdata, np.nan), grid.res)},
        "texture": {"width": tex_grid.width, "height": tex_grid.height, "bytes": len(jpeg),
                    "resolution_m": round(tex_grid.res, 4), "coverage": coverage, "quality": params.texture_quality},
        **({"point_cloud": {"points_total": point_raster["points_total"], "points_used": point_raster["points_used"],
                            "classes_seen": point_raster["classes_seen"]}} if point_raster else {}),
    }


# --------------------------------------------------------------------------
# Points workflow (direct point cloud)
# --------------------------------------------------------------------------

def points_workflow(available: dict[str, str], params: Params, progress) -> dict:
    info = pointcloud.info(available["point_cloud"])
    crs = require_projected(info["crs"], "point cloud")
    classes = pointcloud.parse_classes(params.point_classes)
    with progress.stage(f"sampling up to {params.max_points} points", 8):
        sampled = pointcloud.sample(available["point_cloud"], params.max_points, classes=classes,
                                    on_progress=lambda f: progress.report(8 + 50 * f))
    xyz, rgb = sampled["xyz"], sampled["rgb"]
    inputs_used = {"point_cloud": f"{sampled['points_used']} of {sampled['points_total']} points"}
    progress.log(f"{sampled['points_used']} points kept (stride {sampled['stride']})")

    colour_source = "point-cloud" if rgb is not None else None
    if "orthophoto" in available and (params.texture_source == "orthophoto" or (params.texture_source == "auto" and rgb is None)):
        with progress.stage("colouring points from the orthophoto", 62):
            ortho = raster_info(available["orthophoto"])
            minx, miny = xyz[:, 0].min(), xyz[:, 1].min()
            maxx, maxy = xyz[:, 0].max(), xyz[:, 1].max()
            grid, _ = plan_grid(crs, (minx, miny, maxx, maxy), max(ortho["res"], (maxx - minx) / params.texture_size))
            try:
                img, img_valid = texture.orthophoto_texture(available["orthophoto"], grid)
                cols = np.clip(((xyz[:, 0] - grid.minx) / grid.res).astype(int), 0, grid.width - 1)
                rows = np.clip(((grid.maxy - xyz[:, 1]) / grid.res).astype(int), 0, grid.height - 1)
                sampled_rgb = img[rows, cols]
                missing = ~img_valid[rows, cols]
                if rgb is None:
                    rgb = sampled_rgb
                    rgb[missing] = 128
                else:
                    rgb = np.where(missing[:, None], rgb, sampled_rgb)
                colour_source = "orthophoto"
                inputs_used["orthophoto"] = "colour"
            except InputError as e:
                progress.warn(f"orthophoto unusable: {e}")
    if rgb is None:
        with progress.stage("colouring points by elevation", 62):
            z = xyz[:, 2]
            relief = texture.shaded_relief(np.ma.MaskedArray(z.reshape(1, -1)), 1.0)
            rgb = relief.reshape(-1, 3)
            colour_source = "elevation"

    zmin, zmax = float(xyz[:, 2].min()), float(xyz[:, 2].max())
    bounds = (float(xyz[:, 0].min()), float(xyz[:, 1].min()), float(xyz[:, 0].max()), float(xyz[:, 1].max()))
    origin = local_origin(bounds, zmin)
    georef = Georef(crs=crs, origin=origin, bounds=bounds, z_range=(zmin, zmax), source="point_cloud")
    with progress.stage("encoding points", 80):
        builder = gltf.GlbBuilder()
        material = builder.add_material(None, name="points")
        prim = builder.add_primitive(meshing.localize(xyz, origin), colors=rgb, material=material,
                                     mode=gltf.MODE_POINTS)
        builder.add_node(builder.add_mesh([prim], name="points"), name="points")
        builder.set_georef(georef.to_extras())
    return {
        "builder": builder, "inputs_used": inputs_used, "georef": georef,
        "mesh": {"points": int(xyz.shape[0]), "triangles": 0, "compression": "none"},
        "point_cloud": {"points_total": sampled["points_total"], "points_used": sampled["points_used"],
                        "stride": sampled["stride"], "colour": colour_source},
    }
