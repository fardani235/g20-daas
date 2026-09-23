# 3D Reconstruction plugin

A **user plugin** (see the [user plugin guide](user-plugin-guide.md)) that turns
the outputs of a completed task into a compact, georeferenced, **web-ready 3D
model** — a single GLB the built-in 3D viewer opens directly. It detects which
outputs the task has, chooses a reconstruction workflow, reuses what ODM already
computed, combines sources where that helps, keeps the model anchored in the
task's coordinate system and sizes it for the browser. Source:
[`plugins/3d-reconstruction/`](../../plugins/3d-reconstruction/).

| | |
|---|---|
| Inputs | `dsm`, `dtm`, `orthophoto`, `point_cloud` (LAS/LAZ), `model` (ODM textured GLB) — all optional, any subset |
| Output | `model` (GLB): `KHR_mesh_quantization` + `KHR_materials_unlit` + `CESIUM_RTC`, georef in `extras.webodm_georef` |
| Workflows | **terrain** — textured heightfield mesh; **optimize-model** — web re-encoding of the ODM mesh |
| Runs in | the plugin sandbox (`services/plugin-runner`): numpy, rasterio (GDAL), shapely, laspy + lazrs |
| Typical run | 400 m × 400 m survey, DSM 0.2 m + orthophoto 0.1 m → 380 k triangles, 7.4 MB GLB, ~11 s, ~310 MB RAM |

---

## 1. What it produces and how it decides

```
inputs present                  auto workflow        surface      texture
──────────────────────────────  ───────────────────  ───────────  ─────────────────────────
model (+ anything)              optimize-model       ODM mesh     ODM textures, capped
dsm [+ dtm] [+ orthophoto]      terrain              dsm          orthophoto | hillshade
point_cloud [+ dtm/orthophoto]  terrain              point cloud  orthophoto | point colours
dtm only                        terrain              dtm          hillshade
nothing usable                  error: "no usable input: select a DSM, DTM, point cloud or existing 3D model"
```

**terrain** builds a 2.5D surface mesh: the heights come from the DSM (or the
point cloud binned to a grid, or the DTM), the texture from the orthophoto (or
the cloud's RGB, or a shaded relief coloured by elevation). Sources are combined
where beneficial: DSM no-data cells take the DTM height (or the point-cloud
height), interior holes are interpolated, the model is clipped to the
orthophoto's coverage so it has no untextured fringe, and the texture is split
into tiles when the orthophoto is sharper than one texture could carry.

**optimize-model** keeps ODM's true 3D mesh (facades and all — ODM's default
`mesh-size` of 200 k faces is already web-sized) and fixes what makes it heavy
in a browser: it re-encodes every texture above the cap (a real survey ships
five 8192² + ten 4096² atlases, > 2 GB decoded), stores positions as 16-bit
integers, and records the georeferencing. Everything else in the file is copied
byte for byte.

`workflow` forces either path: `terrain` when the task has an ODM model but you
want the lighter heightfield model, `optimize-model` to re-encode only.

### Geospatial consistency

Every output is in a **local metric frame**: X east, Y north, Z up (ODM's
convention, which the viewer already handles), in the units of the working
projected CRS, relative to an origin. The origin is the centre of the model's
footprint at height 0 for terrain models and ODM's `CESIUM_RTC` centre for
optimized models. Three things carry the georeferencing:

- `extensions.CESIUM_RTC.center` — the origin in the projected CRS, exactly as
  ODM writes it, so tools that understand ODM's GLBs keep working;
- `extras.webodm_georef` — the authoritative record: `crs`/`epsg`/`wkt`,
  `origin`, `bounds` (projected), `bounds_4326`, `up_axis`, `units`, plus mesh
  statistics;
- the run's `output_extent` / `output_metadata` in WebODM, read by the plugin
  runner from that record, which the map uses to draw the model's footprint.

Inputs in a geographic CRS are projected to the UTM zone of their centroid so
the mesh is in metres; inputs on different grids or CRSs are resampled onto one
node grid with rasterio's `WarpedVRT` (reading from overviews when
downsampling). A point cloud without a CRS in its header takes the task's CRS.

---

## 2. Install

```bash
cd plugins/3d-reconstruction
tools/build.sh                 # -> plugins/3d-reconstruction-1.0.0.zip
```

Upload the zip on the **Plugins** page (organization owner) or with the API:

```bash
BASE=https://your.webodm.host
curl -c cj.txt -X POST "$BASE/api/method/login" -F usr=you@example.com -F pwd=secret
CSRF=$(curl -b cj.txt -s "$BASE/api/method/webodm_core.api.csrf.get_token" | python3 -c 'import json,sys;print(json.load(sys.stdin)["message"])')
curl -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -F "file=@../3d-reconstruction-1.0.0.zip" \
     "$BASE/api/method/webodm_core.api.plugins.upload_plugin"
```

It is installed as `<org-slug>.3d-reconstruction` and enabled for your
organization. Re-uploading a higher `version` upgrades it in place.

The plugin runner image must include `laspy[lazrs]` (added to
`services/plugin-runner/requirements.txt` with this plugin) for point-cloud
inputs; without it the point cloud is reported as unusable and the other inputs
still work.

---

## 3. Run it

Open a completed task on the project map and click **3D Reconstruction** in
the *Analysis* panel. The run dialog shows one selector per input (DSM, DTM,
Orthophoto, Point cloud, Existing 3D model) listing what the task has — leave
the ones you do not want on *None* — plus the parameters. With everything on
`auto` the plugin does the right thing for the inputs you selected.

The run goes Queued → Running → Completed/Failed in the panel (typically
10–90 s). When it completes:

- **Open 3D** in the run row opens the model in the 3D viewer. The viewer's
  *Model source* selector switches between ODM's textured model and any
  reconstruction; the URL carries `?run=<run id>` so it can be shared.
- The model's **footprint** appears as a dashed layer in *Layers*; clicking it
  opens the viewer too.
- **Download** fetches the GLB (it loads in Blender, CesiumJS, Babylon.js,
  Three.js, glTF viewers; Draco is not used so no decoder is needed).

A failed run shows the reason (bad inputs, an incompatible choice) or, for a
bug, the tail of the plugin's log.

### From the command line

```bash
run() {  # $1 = plugin id, $2 = inputs JSON, $3 = params JSON
  curl -s -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
       -d "{\"plugin\":\"$1\",\"task\":\"$TASK\",\"inputs\":$2,\"params\":$3}" \
       "$BASE/api/method/webodm_core.api.plugins.run_plugin"
}
P=acme.3d-reconstruction

# textured terrain from DSM + orthophoto, DTM fills DSM gaps
run $P '{"dsm":"dsm","dtm":"dtm","orthophoto":"orthophoto","point_cloud":null,"model":null}' '{}'

# lighter model for phones
run $P '{"dsm":"dsm","orthophoto":"orthophoto"}' '{"quality":"web-light"}'

# point cloud only (surface and colours from the cloud)
run $P '{"point_cloud":"point_cloud"}' '{"point_cloud_stat":"max"}'

# terrain-only relief from the DTM
run $P '{"dtm":"dtm"}' '{"texture_source":"hillshade"}'

# web version of ODM's textured mesh, 2048 px textures
run $P '{"model":"model","dsm":"dsm"}' '{"workflow":"optimize-model","texture_size":2048}'

# poll, then open  <BASE>/assets/webodm_frontend/frontend/project/<PROJECT>/task/<TASK>/model?run=<RUN>
curl -s -b cj.txt "$BASE/api/method/webodm_core.api.plugins.get_run?name=<RUN>"
```

`inputs` names which task dataset feeds each plugin input; omitting the
`inputs` object altogether takes every dataset the task has.

---

## 4. Parameters

Quality presets bundle the size/detail knobs, the way ODM presets bundle
processing options; any value set explicitly overrides the preset.

| Preset | Triangle budget | Texture size | Max texture tiles / side | JPEG quality |
|---|---|---|---|---|
| `web-light` | 150 k | 2048 px | 1 | 80 |
| `balanced` (default) | 500 k | 4096 px | 2 | 85 |
| `high-detail` | 1.5 M | 4096 px | 4 | 90 |

| Parameter | Default | Meaning |
|---|---|---|
| `workflow` | `auto` | `auto` / `terrain` / `optimize-model` (see §1) |
| `quality` | `balanced` | preset above |
| `surface_source` | `auto` | `dsm` / `dtm` / `point_cloud`; auto prefers DSM, then point cloud, then DTM |
| `texture_source` | `auto` | `orthophoto` / `point_cloud` (cloud RGB) / `hillshade`; auto in that order |
| `mesh_method` | `adaptive` | `adaptive`: error-driven RTIN simplification; `grid`: regular grid sized to the budget |
| `resolution_m` | 0 | node spacing of the heightfield; 0 = source resolution, capped at 2049 nodes per side |
| `max_triangles` | 0 | 0 = preset |
| `max_error_m` | 0 | adaptive: allowed vertical deviation; 0 = smallest error that fits the budget |
| `texture_size` | 0 | texture tile side (terrain) or per-texture cap (optimize-model); 0 = preset |
| `texture_tiles` | 0 | 1 / 2 / 4 tiles per side; 0 = from the orthophoto resolution within the preset's limit |
| `texture_quality` | 0 | JPEG quality 1–100; 0 = preset |
| `quantize` | true | 16-bit positions (`KHR_mesh_quantization`); off for tools that lack it |
| `fill_holes` | true | interpolate interior no-data holes (after filling from DTM / point cloud) |
| `clip_to_texture` | true | drop surface outside the orthophoto's coverage |
| `point_cloud_cell_m` | 0 | node spacing when the surface comes from the cloud; 0 ≈ two points per node |
| `point_cloud_stat` | `max` | per-node height statistic: `max` (top surface), `mean`, `min` (rough ground) |

The task's ODM options (`context.task.processing_options`: e.g. `dsm`, `dtm`,
`mesh-size`, `pc-quality`, `dem-resolution`, `skip-3dmodel`) are recorded in the
run metadata as `odm_options`, so a model can always be traced back to how its
inputs were produced. To get the best inputs for this plugin, process the task
with a preset that enables **DSM and DTM** (`dsm: true`, `dtm: true`, a
`dem-resolution` close to the orthophoto's) and, for `optimize-model`, without
`skip-3dmodel`.

### Run metadata

Shown in the run panel and stored on the run (`output_metadata`): `workflow`,
`surface_source`, `texture_source`, `epsg`, `origin`, `bounds`, `bounds_4326`,
`resolution_m`, `grid_size`, `max_error_m`, `triangles`, `vertices`, `tiles`,
`texture_size`, `fills` (how many nodes came from the DTM / interpolation),
`height_range_m`, `odm_options`, per-stage timings (`stages`), `warnings`, and
for optimize-model the texture sizes/bytes before and after.

---

## 5. Technology choice

The requirement was to choose tools that fit the architecture rather than
assume one. The decisive constraint is **where plugins run**: an unprivileged,
network-less sandbox whose interpreter has numpy, rasterio/GDAL, shapely and
onnxruntime and cannot install anything — a plugin ships pure-Python code only.
The second is the consumer: the existing Three.js viewer (GLTFLoader with
Draco, `KHR_mesh_quantization`/`KHR_materials_unlit` support, Z-up ODM handling,
a texture-memory budget).

| Need | Candidates considered | Chosen | Why |
|---|---|---|---|
| Terrain meshing | pydelatin (C++ `hmm`, MIT), pymartini (Cython, ISC), Open3D / trimesh / PyMeshLab decimation, uniform grid | **numpy RTIN** (Martini algorithm re-formulated level-by-level in numpy, `recon3d/rtin.py`) | Same error-bounded result as pymartini with no compiled code (a 2049² grid meshes in ~5 s); Delatin would give ~40 % fewer triangles for the same error but needs a compiled wheel. Open3D/PyMeshLab are 100+ MB binaries unusable in the sandbox. |
| Point clouds | PDAL, laspy + lazrs, laspy + laszip, pure-Python LAZ | **laspy + lazrs** (BSD-2 / Apache-2.0-MIT), added to the sandbox image | Small wheels, chunked streaming reads, LAZ 1.2–1.4; PDAL is a heavyweight native stack and there is no pure-Python LAZ decoder. Binning to a heightfield (2.5D) is the right reconstruction for nadir survey data and reuses the terrain path; Poisson/ball-pivoting meshing would need Open3D. |
| Textures | Pillow, imagecodecs, GDAL | **GDAL via rasterio** (already present) | JPEG/PNG/WebP encode, decode-at-reduced-scale (an 8192² atlas never sits in RAM at full size), `WarpedVRT` for reprojection/tiling from overviews. |
| Mesh compression | Draco (C++), meshoptimizer/gltfpack (C++/Node), gltf-transform (Node), KHR_mesh_quantization | **KHR_mesh_quantization + unlit materials + JPEG textures**, own GLB writer (`recon3d/gltf.py`) | Halves geometry bytes with zero decoder cost and is supported natively by three.js (r111+), Babylon, Cesium and Blender; Draco/meshopt need native encoders the sandbox lacks. Unlit = no normals (photogrammetry textures carry the lighting) and the viewer already maps them to non-tone-mapped MeshBasicMaterial. |
| Output format | GLB, 3D Tiles, Potree, OBJ/MTL, PLY | **GLB** | The viewer's native format; one file; carries georef in extensions/extras. 3D Tiles / Potree would need a new viewer and multi-file storage; OBJ has no compact texture story. |
| Georeferencing | glTF extras, CESIUM_RTC, EXT_mesh_features, custom sidecar | **CESIUM_RTC + `extras.webodm_georef`** | RTC is what ODM emits (compatibility); the extras record adds the CRS the RTC centre lacks and is read by the plugin runner without any extra dependency. |

Licensing: everything used is permissive (ISC for the Martini algorithm,
BSD-2 laspy, Apache-2.0/MIT lazrs, BSD rasterio, MIT/BSD GDAL); the plugin
itself is MIT like the platform.

### What the framework needed

Three small, generic additions were made to the plugin framework (no
plugin-specific code in core):

1. `output_kind: "model"` (with `render_kind: "glb"`) — validated at upload,
   stored as `.glb`, structure-checked by the runner (glTF 2.0 binary with
   meshes) which reads `extras.webodm_georef` into the run's extent/metadata.
   The map shows a footprint and an **Open 3D** action; the viewer accepts
   `?run=<name>`.
2. `request.json["context"]` — the task's name, title, EPSG and ODM
   processing options, the run and plugin ids. Frappe fills it, the runner
   forwards it, plugins may ignore it.
3. `laspy[lazrs]` in the sandbox image, and `RAYON_NUM_THREADS=1` in the
   plugin environment (lazrs otherwise tries to start one thread per core,
   which the sandbox's limits refuse).

---

## 6. Limits, performance and troubleshooting

Measured through the sandbox CLI (which applies the real limits) on a
synthetic 400 m × 400 m survey, single core:

| Case | Grid | Result | Time | Peak RAM |
|---|---|---|---|---|
| DSM 0.2 m + DTM + orthophoto 0.1 m, balanced | 2049² | 382 k triangles, 1 × 4096² texture, 7.4 MB | 11 s | 313 MB |
| DSM + orthophoto, high-detail | 2049² | 397 k triangles (the synthetic surface needs no more), 7.8 MB | 11 s | 314 MB |
| DSM + orthophoto, `mesh_method: grid`, web-light | 513² | 149 k triangles, 1 × 2048², 2.8 MB | 9 s | 192 MB |
| DTM only, web-light (hillshade texture) | 2049² | 28 k triangles, 1 × 2048², 0.4 MB | 9 s | 281 MB |
| LAZ, 4 M coloured points only, balanced | 2049² at 0.28 m | 494 k triangles, point-colour texture, 9.3 MB | 22 s | 783 MB |
| LAZ surface + orthophoto texture + DTM gap fill | 2049² | 475 k triangles, 2 × 2 × 4096² textures, 9.1 MB | 20 s | 794 MB |

The sandbox allows 2 GB of address space and (by default) 300–3600 s; the
plugin declares `timeout_seconds: 3600`. The heightfield is capped at 2049
nodes per side (about 4.2 M cells); larger extents are meshed at a coarser
`resolution_m` (a warning says so). Adaptive meshing keeps detail where the
surface changes, so a 500 k-triangle budget normally covers a 1 km survey with
sub-decimetre vertical error on buildings.

| Symptom | Cause / fix |
|---|---|
| *no usable input: select a DSM, DTM, point cloud or existing 3D model* | Every selected input was *None* or missing on the task. |
| *point_cloud unusable: …* warning, cloud ignored | The runner image lacks `laspy` or the LAZ is corrupt; rebuild the plugin-runner image. |
| *the orthophoto does not overlap the surface data* | Different areas/CRSs; check the task's outputs, or set `clip_to_texture: false`. |
| *point cloud has no CRS; assuming …* warning | LAS without georeferencing keys; the task's CRS was used. Add a DSM input to be safe. |
| Model looks flat / buildings missing | Surface came from the DTM (no DSM on the task): re-process with `dsm: true`, or use the point cloud. |
| *max_error_m raised to …* warning | Your error is too fine for the triangle budget; raise `max_triangles` or accept. |
| Textures look soft | Raise `texture_size` (up to 8192) or `texture_tiles`; the viewer may still downscale on low-memory devices (its texture chip shows the cap). |
| *no CRS available for the model* (optimize-model) | Add the DSM or orthophoto as an input so the plugin knows the CRS; the model still opens in the viewer, only the map footprint is missing. |
| Out of memory | Pick `web-light`, a larger `resolution_m`, or a smaller `texture_size`. |

The viewer wraps Z-up models in a −90° X rotation and recentres large
coordinates, so terrain models and ODM models look the same and can be switched
without re-framing.

---

## 7. Development

```bash
# unit tests (numpy / rasterio / laspy from the plugin-runner venv)
cd plugins/3d-reconstruction
../../services/plugin-runner/venv/bin/python -m pytest -q          # 38 tests

# run through the sandbox code path with your own files
cd services/plugin-runner
python -m app.cli ../../plugins/3d-reconstruction \
    --input dsm=/data/dsm.tif --input orthophoto=/data/orthophoto.tif \
    --param quality=web-light --output /tmp/model.glb \
    --context '{"task": {"epsg": 32633}}'
```

Module map (`recon3d/`): `sources` (detect/describe inputs, CRS choice) →
`grid` (node lattice) → `heightfield` (warp, fuse, fill) / `pointcloud` (LAS
binning) → `rtin` + `mesher` (error map, tile-aware extraction, local frame,
UVs) → `texture` (orthophoto tiles, point colours, hillshade; JPEG/PNG via
GDAL) → `gltf` (GLB writer) or `glb_optimize` (existing model) → `workflow`
(decisions, metadata). `params` holds the presets; `progress` the stage log
that becomes the run metadata.
