"""Pick a reconstruction workflow from the available inputs and run it.

Two workflows:

- **terrain** — a textured 2.5D surface mesh from a heightfield (DSM, point
  cloud or DTM) with the orthophoto, point-cloud colours or a shaded relief as
  texture. This is what makes a web-ready model when ODM produced no textured
  mesh (``skip-3dmodel``, DEM-only presets) or when the ODM mesh is too heavy.
- **optimize-model** — the existing ODM textured mesh, re-encoded for the web
  (texture cap, 16-bit positions, georef record).

``auto`` prefers the existing mesh when the task has one (it is a true 3D
model with facades), otherwise builds the terrain model. Everything the run
did is returned as metadata for the run panel.
"""

import math
import os

import numpy as np

from . import gltf as gltf_mod
from . import heightfield as hf_mod
from . import sources as src_mod
from . import texture as tex_mod
from .errors import InputError
from .grid import Grid
from .mesher import build_terrain_mesh
from .params import MAX_GRID_SIZE, Params

_ODM_OPTIONS_OF_INTEREST = (
    "mesh-size", "mesh-octree-depth", "skip-3dmodel", "use-3dmesh", "texturing-single-material",
    "texturing-skip-global-seam-leveling", "pc-quality", "feature-quality", "dsm", "dtm",
    "dem-resolution", "orthophoto-resolution", "pc-classify", "smrf-threshold",
)


def run(request: dict, progress) -> dict:
    params = Params.from_request(request.get("params"))
    inputs = src_mod.detect(request.get("inputs") or {})
    for note in inputs.notes:
        progress.warn(note)
    context = request.get("context") or {}
    task_ctx = context.get("task") or {}

    workflow = choose_workflow(params, inputs)
    progress.log(f"workflow: {workflow} (requested: {params.workflow}); "
                 f"available inputs: {', '.join(inputs.available()) or 'none'}")

    if workflow == "optimize-model":
        meta = run_optimize(params, inputs, task_ctx, request["output_path"], progress)
    else:
        meta = run_terrain(params, inputs, task_ctx, request["output_path"], progress)

    meta["workflow"] = workflow
    meta["quality"] = params.quality
    meta["inputs_available"] = inputs.available()
    odm = odm_options(task_ctx)
    if odm:
        meta["odm_options"] = odm
    meta["output_bytes"] = os.path.getsize(request["output_path"])
    meta.update(progress.summary())
    return meta


def choose_workflow(params: Params, inputs: src_mod.Inputs) -> str:
    has_surface = any([inputs.dsm, inputs.dtm, inputs.point_cloud and not inputs.point_cloud.error])
    if params.workflow == "optimize-model":
        if inputs.model is None:
            raise InputError("workflow 'optimize-model' needs the task's existing 3D model input")
        return "optimize-model"
    if params.workflow == "terrain":
        if not has_surface:
            raise InputError("workflow 'terrain' needs a DSM, DTM or point cloud input")
        return "terrain"
    if inputs.model is not None:
        return "optimize-model"
    if has_surface:
        return "terrain"
    raise InputError("no usable input: select a DSM, DTM, point cloud or existing 3D model")


def choose_surface(params: Params, inputs: src_mod.Inputs) -> str:
    pc_ok = inputs.point_cloud is not None and not inputs.point_cloud.error
    if params.surface_source != "auto":
        wanted = params.surface_source
        have = {"dsm": inputs.dsm is not None, "dtm": inputs.dtm is not None, "point_cloud": pc_ok}[wanted]
        if not have:
            raise InputError(f"surface_source '{wanted}' selected but that input is not available")
        return wanted
    if inputs.dsm is not None:
        return "dsm"
    if pc_ok:
        return "point_cloud"
    if inputs.dtm is not None:
        return "dtm"
    raise InputError("no surface input (DSM, point cloud or DTM)")


def choose_texture(params: Params, inputs: src_mod.Inputs, surface: str) -> str:
    pc_rgb = inputs.point_cloud is not None and not inputs.point_cloud.error and inputs.point_cloud.has_rgb
    if params.texture_source != "auto":
        wanted = params.texture_source
        if wanted == "orthophoto" and inputs.orthophoto is None:
            raise InputError("texture_source 'orthophoto' selected but the task has no orthophoto")
        if wanted == "point_cloud" and not pc_rgb:
            raise InputError("texture_source 'point_cloud' selected but the point cloud has no colour")
        return wanted
    if inputs.orthophoto is not None:
        return "orthophoto"
    if pc_rgb:
        return "point_cloud"
    return "hillshade"


def odm_options(task_ctx: dict) -> dict:
    """The task's ODM options that influence which inputs exist and how good they are."""
    raw = task_ctx.get("processing_options")
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = ((o.get("name"), o.get("value")) for o in raw if isinstance(o, dict))
    else:
        return {}
    return {str(k): v for k, v in items if k in _ODM_OPTIONS_OF_INTEREST}


# --------------------------------------------------------------------------
# terrain
# --------------------------------------------------------------------------

def run_terrain(params: Params, inputs: src_mod.Inputs, task_ctx: dict, output_path: str, progress) -> dict:
    eff = params.effective()
    surface = choose_surface(params, inputs)
    texture = choose_texture(params, inputs, surface)
    progress.log(f"surface from {surface}, texture from {texture}")

    with progress.stage("resolving working grid"):
        crs = _working_crs(inputs, surface, task_ctx)
        surface_bounds, native_res = _surface_extent(inputs, surface, crs, task_ctx)
        if surface == "point_cloud" and params.point_cloud_cell_m > 0:
            native_res = params.point_cloud_cell_m
        bounds = surface_bounds
        if texture == "orthophoto" and params.clip_to_texture:
            ortho_bounds = inputs.orthophoto.bounds_in(crs)
            bounds = src_mod.intersection(surface_bounds, ortho_bounds)
            if bounds is None:
                raise InputError("the orthophoto does not overlap the surface data")
        extent = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
        res = params.resolution_m or max(native_res, extent / (MAX_GRID_SIZE - 1))
        if params.mesh_method == "grid":
            # A full grid has 2 triangles per cell: size the grid to the budget.
            res = max(res, extent / max(math.sqrt(eff["max_triangles"] / 2.0), 2.0))
        grid = Grid.square_for(crs, bounds, res, MAX_GRID_SIZE)
        if grid.res > res * 1.001:
            progress.warn(f"resolution coarsened to {grid.res:.3f} m so the grid fits {MAX_GRID_SIZE} nodes per side")
        progress.log(f"CRS EPSG:{src_mod.epsg_of(crs)}; grid {grid.width}x{grid.height} nodes at {grid.res:.3f} m; "
                     f"extent {extent:.1f} m")

    with progress.stage("building heightfield"):
        pc_source = inputs.point_cloud if (inputs.point_cloud and not inputs.point_cloud.error) else None
        if pc_source is not None and pc_source.crs is None:
            pc_source.crs = _point_cloud_crs(inputs, task_ctx, crs, progress)
        hf = hf_mod.build(grid, primary=surface, dsm=inputs.dsm, dtm=inputs.dtm, point_cloud=pc_source,
                          point_stat=params.point_cloud_stat, fill_holes=params.fill_holes, progress=progress)
        if texture == "orthophoto" and params.clip_to_texture:
            mask = hf_mod.warp_mask_to_grid(inputs.orthophoto, grid)
            dropped = hf.valid & ~mask
            if dropped.any():
                hf.z[dropped] = np.nan
                progress.log(f"clipped {int(dropped.sum()):,} nodes without orthophoto coverage")
        if texture == "point_cloud" and hf.rgb is None:
            from . import pointcloud
            _, hf.rgb = pointcloud.rasterize(pc_source, grid, stat=params.point_cloud_stat, progress=progress)
        valid_nodes = int(hf.valid.sum())
        if valid_nodes < 3:
            raise InputError("too little valid surface data inside the reconstruction area")
        zmin, zmax = hf.z_range()
        progress.log(f"valid nodes: {valid_nodes:,} / {grid.width * grid.height:,}; heights {zmin:.1f}–{zmax:.1f} m")

    tiles_per_side = _tiles_per_side(params, eff, inputs, texture, crs, grid, progress)

    with progress.stage("meshing"):
        mesh = build_terrain_mesh(
            grid, hf.z, max_triangles=eff["max_triangles"], tiles_per_side=tiles_per_side,
            max_error=params.max_error_m, method=params.mesh_method, progress=progress,
        )
        progress.log(f"{mesh.triangle_count:,} triangles, {mesh.vertex_count:,} vertices in "
                     f"{len(mesh.tiles)} tile(s); max vertical error {mesh.max_error:.3f} m")

    builder = gltf_mod.GlbBuilder()
    with progress.stage("texturing"):
        size = eff["texture_size"]
        for tile in mesh.tiles:
            if texture == "orthophoto":
                rgb, _ = tex_mod.orthophoto_tile(inputs.orthophoto, grid, tile.col0, tile.row0, tile.cells, size)
            elif texture == "point_cloud":
                rgb = tex_mod.grid_colour_tile(hf.rgb, tile.col0, tile.row0, tile.cells, size)
            else:
                rgb = tex_mod.hillshade_tile(hf.z, grid.res, tile.col0, tile.row0, tile.cells, size, (zmin, zmax))
            data, mime = tex_mod.encode(rgb, "jpeg", eff["texture_quality"])
            tex = builder.add_texture(data, mime)
            material = builder.add_unlit_material(tex, name=f"tile_{tile.tile_x}_{tile.tile_y}")
            builder.add_mesh(tile.positions, tile.indices, material, tile.uvs, quantize=params.quantize,
                             name=f"tile_{tile.tile_x}_{tile.tile_y}")
            del rgb

    with progress.stage("writing GLB"):
        rows, cols = np.nonzero(hf.valid)
        data_bounds = (grid.x0 + cols.min() * grid.res, grid.y0 - rows.max() * grid.res,
                       grid.x0 + cols.max() * grid.res, grid.y0 - rows.min() * grid.res)
        bounds_4326 = src_mod.bounds_to_4326(crs, data_bounds)
        extras = gltf_mod.georef_extras(
            crs, mesh.origin, data_bounds, bounds_4326, source="terrain",
            extra={"surface_source": surface, "texture_source": texture, "resolution_m": grid.res,
                   "max_error_m": mesh.max_error, "triangles": mesh.triangle_count,
                   "vertices": mesh.vertex_count, "tiles": len(mesh.tiles), "texture_size": size,
                   "height_range": [zmin, zmax]},
        )
        glb = builder.to_glb(extras=extras, rtc_center=mesh.origin)
        with open(output_path, "wb") as f:
            f.write(glb)

    return {
        "surface_source": surface,
        "texture_source": texture,
        "epsg": src_mod.epsg_of(crs),
        "origin": [float(v) for v in mesh.origin],
        "bounds": [float(v) for v in data_bounds],
        "bounds_4326": bounds_4326,
        "resolution_m": round(grid.res, 4),
        "grid_size": grid.width,
        "mesh_method": params.mesh_method,
        "max_error_m": round(mesh.max_error, 4),
        "triangles": mesh.triangle_count,
        "vertices": mesh.vertex_count,
        "tiles": len(mesh.tiles),
        "texture_size": size,
        "texture_quality": eff["texture_quality"],
        "quantized": bool(params.quantize),
        "height_range_m": [round(zmin, 2), round(zmax, 2)],
        "fills": hf.fills,
    }


def _working_crs(inputs, surface, task_ctx):
    candidates = []
    order = {"dsm": [inputs.dsm, inputs.orthophoto, inputs.dtm],
             "dtm": [inputs.dtm, inputs.dsm, inputs.orthophoto],
             "point_cloud": [inputs.dsm, inputs.orthophoto, inputs.dtm]}[surface]
    for r in order:
        if r is not None:
            candidates.append((r.crs, r.bounds))
    if surface == "point_cloud" and inputs.point_cloud.crs is not None:
        candidates.insert(0, (inputs.point_cloud.crs, inputs.point_cloud.bounds))
    crs_only = [c for c, _ in candidates]
    for crs in crs_only:
        if crs.is_projected:
            return crs
    fallback = task_ctx.get("epsg")
    if fallback:
        try:
            from rasterio.crs import CRS
            crs = CRS.from_epsg(int(fallback))
            if crs.is_projected:
                return crs
        except Exception:
            pass
    if candidates:
        crs, bounds = candidates[0]
        w, s, e, n = src_mod.bounds_to_4326(crs, bounds)
        return src_mod.utm_crs_for((w + e) / 2, (s + n) / 2)
    raise InputError("no input carries a coordinate reference system")


def _point_cloud_crs(inputs, task_ctx, working, progress):
    """CRS for a LAS without one: the task's, else the other inputs' (ODM shares one)."""
    if task_ctx.get("epsg"):
        try:
            from rasterio.crs import CRS
            crs = CRS.from_epsg(int(task_ctx["epsg"]))
            progress.warn(f"point cloud has no CRS; assuming the task's EPSG:{task_ctx['epsg']}")
            return crs
        except Exception:
            pass
    progress.warn(f"point cloud has no CRS; assuming EPSG:{src_mod.epsg_of(working)} like the other outputs")
    return working


def _surface_extent(inputs, surface, crs, task_ctx):
    if surface == "dsm":
        return inputs.dsm.bounds_in(crs), inputs.dsm.res_in(crs)
    if surface == "dtm":
        return inputs.dtm.bounds_in(crs), inputs.dtm.res_in(crs)
    pc = inputs.point_cloud
    pc_crs = pc.crs
    if pc_crs is None:
        # Assume the working CRS (validated/warned later in _point_cloud_crs).
        bounds = pc.bounds
    elif pc_crs == crs:
        bounds = pc.bounds
    else:
        from rasterio.warp import transform_bounds
        bounds = transform_bounds(pc_crs, crs, *pc.bounds, densify_pts=21)
    from . import pointcloud
    extent = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
    return tuple(bounds), pointcloud.suggested_cell(pc, extent, MAX_GRID_SIZE)


def _tiles_per_side(params, eff, inputs, texture, crs, grid, progress) -> int:
    """How many texture tiles per side: enough that the texture is not much coarser than its source."""
    limit = eff["max_texture_tiles"]
    if eff["texture_tiles_fixed"]:
        t = limit
    else:
        if texture == "orthophoto":
            source_gsd = inputs.orthophoto.res_in(crs)
        else:
            source_gsd = grid.res
        extent = (grid.width - 1) * grid.res
        t = 1
        while t < limit and extent / (t * eff["texture_size"]) > 1.25 * source_gsd:
            t *= 2
    while t > 1 and (grid.width - 1) % t:
        t //= 2
    progress.log(f"texture: {t}x{t} tile(s) of {eff['texture_size']} px "
                 f"({(grid.width - 1) * grid.res / (t * eff['texture_size']) * 100:.1f} cm/px)")
    return t


# --------------------------------------------------------------------------
# optimize-model
# --------------------------------------------------------------------------

def run_optimize(params: Params, inputs: src_mod.Inputs, task_ctx: dict, output_path: str, progress) -> dict:
    from . import glb_optimize
    eff = params.effective()
    with progress.stage("loading model"):
        doc = glb_optimize.load(inputs.model)
        progress.log(f"{inputs.model.format}: {doc.source_bytes / 1e6:.1f} MB, "
                     f"{len(doc.root.get('meshes', []))} mesh(es), {len(doc.root.get('images', []))} image(s)")
    crs = None
    rasters = inputs.rasters()
    if rasters:
        crs = rasters[0].crs
    elif task_ctx.get("epsg"):
        try:
            from rasterio.crs import CRS
            crs = CRS.from_epsg(int(task_ctx["epsg"]))
        except Exception:
            crs = None
    if crs is None:
        progress.warn("no CRS available for the model (add a DSM/orthophoto input or process the task "
                      "with georeferencing); the footprint cannot be placed on the map")
    with progress.stage("optimizing"):
        glb, stats = glb_optimize.optimize(
            doc, texture_cap=eff["texture_size"], quality=eff["texture_quality"],
            quantize=params.quantize, crs=crs, progress=progress,
        )
        with open(output_path, "wb") as f:
            f.write(glb)
        progress.log(f"{stats['bytes_before'] / 1e6:.1f} MB -> {stats['bytes_after'] / 1e6:.1f} MB; "
                     f"textures {stats['texture_max_side_before']} -> {stats['texture_max_side_after']} px")
    if stats["origin"] is None:
        progress.warn("model has no CESIUM_RTC centre; its position is recorded as unknown")
    return {
        "model_format": inputs.model.format,
        "epsg": src_mod.epsg_of(crs) if crs is not None else None,
        "origin": stats["origin"],
        "bounds": stats["bounds"],
        "bounds_4326": stats["bounds_4326"],
        "triangles": stats["triangles"],
        "vertices": stats["vertices"],
        "textures": stats["textures"],
        "textures_resized": stats["textures_resized"],
        "texture_max_side_before": stats["texture_max_side_before"],
        "texture_max_side_after": stats["texture_max_side_after"],
        "texture_bytes_before": stats["texture_bytes_before"],
        "texture_bytes_after": stats["texture_bytes_after"],
        "meshes_quantized": stats["meshes_quantized"],
        "quantized": bool(stats["meshes_quantized"]),
        "bytes_before": stats["bytes_before"],
        "texture_size": eff["texture_size"],
    }
