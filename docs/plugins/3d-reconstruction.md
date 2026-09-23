# 3D Reconstruction plugin

A **user plugin** (see the [user plugin guide](user-plugin-guide.md)) that
turns a completed task's ODM outputs into **one web-ready, georeferenced 3D
model (GLB)** and opens it in the platform's 3D viewer. It detects which
outputs the task has — orthophoto, DSM, DTM, LAZ point cloud, ODM's own
textured mesh — picks a reconstruction workflow, combines the sources where
that helps (DTM under a DSM, point cloud where the DSM has gaps, orthophoto or
point colours as texture), keeps the survey's coordinates, and sizes the
result for a browser. Source: [`plugins/3d-reconstruction/`](../../plugins/3d-reconstruction/).

| | |
|---|---|
| Inputs | `orthophoto`, `dsm`, `dtm`, `point_cloud`, `model` — all optional; pick any subset in the run dialog |
| Output | one `.glb`: Z-up, coordinates local to a `CESIUM_RTC` origin, unlit textured material(s), Draco-compressed geometry (optional), `asset.extras.webodm_georef` with EPSG, origin and bounds |
| Where it shows | **View 3D** on the run row (map page) and the model switcher of the 3D viewer; downloadable; map extent recorded for the run |
| Runs in | the plugin sandbox (`services/plugin-runner`): numpy, rasterio, Pillow, laspy/lazrs, fast-simplification, DracoPy |
| Typical run | 5–40 s and < 1.3 GB for a 10 M-point / 170 MP-orthophoto survey at `high-detail` |

---

## 1. Install

```bash
cd plugins/3d-reconstruction
tools/build.sh            # -> plugins/3d-reconstruction-1.0.0.zip (~60 KB)
```

Upload the zip on the **Plugins** page (organization owner) or with the API:

```bash
BASE=https://your.webodm.host
curl -c cj.txt -X POST "$BASE/api/method/login" -F usr=you@example.com -F pwd=secret
CSRF=$(curl -b cj.txt -s "$BASE/api/method/webodm_core.api.csrf.get_token" | python3 -c 'import json,sys;print(json.load(sys.stdin)["message"])')
curl -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -F "file=@../3d-reconstruction-1.0.0.zip" \
     "$BASE/api/method/webodm_core.api.plugins.upload_plugin"
```

The plugin is installed as `<org-slug>.3d-reconstruction` and enabled for your
organization. The **Plugins** page lists it with output kind *Model*.

> The sandbox image must include the 3D libraries (`services/plugin-runner/requirements.txt`
> as of this change). Deployments running an older `webodm-plugin-runner`
> image fail the run with `ModuleNotFoundError: laspy` — rebuild/pull the image.

---

## 2. Run it

Open a completed task on the map and choose **3D Reconstruction** in the
analysis panel. The dialog shows one selector per input listing what the task
has (leave an input on *None* to exclude it) and the parameters. With
everything on `auto` the plugin chooses the workflow from the inputs you
selected and the quality from the ODM options the task was processed with.

While the run executes, the run row shows live progress
(`Running · 45% · decimating 753867 -> 500000 triangles`). When it completes,
**View 3D** opens the model in the viewer; the viewer's model switcher lists
the task's own ODM model and every completed reconstruction so you can
compare them. **Download** gives you the `.glb`.

From the command line (`inputs` maps plugin inputs to task datasets; omit or
`null` to exclude):

```bash
run() {  # $1 = inputs JSON, $2 = params JSON
  curl -s -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
       -d "{\"plugin\":\"acme.3d-reconstruction\",\"task\":\"$TASK\",\"inputs\":$1,\"params\":$2}" \
       "$BASE/api/method/webodm_core.api.plugins.run_plugin"
}
```

### Everything the task has (auto)

```bash
run '{"orthophoto":"orthophoto","dsm":"dsm","dtm":"dtm","point_cloud":"point_cloud","model":"model"}' '{}'
```

`auto` reuses ODM's textured mesh when the task has one (**mesh** workflow —
the most faithful 3D, made web-sized), otherwise builds a terrain mesh from the
DSM (**terrain**), otherwise from the point cloud (**point-cloud**).

### Textured terrain from DSM + orthophoto (+ DTM, + point cloud)

```bash
run '{"orthophoto":"orthophoto","dsm":"dsm","dtm":"dtm","point_cloud":"point_cloud","model":null}' \
    '{"workflow":"terrain","quality":"balanced"}'
```

The DSM is gridded at a resolution derived from the triangle budget and the
surveyed area, holes are filled from the DTM, then from the point cloud (only
when the DTM left gaps), then small enclosed gaps (≤ 100 m²) are interpolated
— larger holes (outside the flight polygon, water) stay open. The grid is
triangulated, decimated to the budget and draped with the orthophoto; where
the orthophoto is transparent the texture falls back to shaded relief.

### Bare-earth terrain

```bash
run '{"dtm":"dtm","orthophoto":"orthophoto"}' '{"workflow":"terrain","surface":"dtm"}'
```

### From the point cloud only

```bash
run '{"point_cloud":"point_cloud"}' '{"workflow":"point-cloud","point_statistic":"max"}'
run '{"point_cloud":"point_cloud"}' '{"workflow":"point-cloud","point_classes":"2","point_statistic":"min"}'  # ground only
```

Without an orthophoto the mesh is coloured from the points' RGB. `point_classes`
restricts the surface to LAS classes (`2` ground, `6` buildings, …) when the
cloud is classified (ODM's `pc-classify`).

### The raw point cloud, coloured

```bash
run '{"point_cloud":"point_cloud","orthophoto":"orthophoto"}' '{"workflow":"points","max_points":2000000}'
```

An evenly subsampled `POINTS` primitive with per-vertex colour (from the
points, or the orthophoto when they have none).

### Optimise ODM's own textured mesh

```bash
run '{"model":"model"}' '{"workflow":"mesh","texture_budget_mp":48,"texture_size":4096}'
```

Geometry is kept as ODM made it (its triangle count is already sensible);
every texture atlas is shrunk so its longest side fits `texture_size` **and**
the total decoded pixels fit `texture_budget_mp`; normals are dropped
(materials are unlit) and the geometry re-encoded. The `CESIUM_RTC` centre
becomes the origin and the task's EPSG the CRS. Also accepts the OBJ archive
the platform stores when NodeODM produced no GLB (that archive carries no
origin, so the result is a local-frame model without a map extent).

### Local, without the platform

```bash
cd services/plugin-runner && pip install -r requirements.txt
python -m app.cli ../../plugins/3d-reconstruction \
    --input dsm=/data/dsm.tif --input orthophoto=/data/orthophoto.tif \
    --param quality=balanced --context '{"task":{"epsg":32632}}' --output /tmp/out.glb
python ../../plugins/3d-reconstruction/tools/preview.py /tmp/out.glb /tmp/out.png   # quick oblique render
```

---

## 3. Which workflow for which inputs

| Task has | `auto` picks | What you get |
|---|---|---|
| ODM textured mesh (`model`) | **mesh** | the photogrammetric mesh, textures within a web budget, georeferenced |
| DSM (± DTM, orthophoto, point cloud) | **terrain** | 2.5D heightfield mesh draped with the orthophoto; DTM/point cloud fill gaps |
| DTM only | **terrain** (`surface` falls back to dtm) | bare-earth mesh with shaded-relief texture |
| point cloud (± orthophoto, DSM/DTM) | **point-cloud** | heightfield from the points (max/mean/min Z per cell), textured from the orthophoto or point RGB |
| point cloud, want the points themselves | *choose* **points** | subsampled coloured point primitive |
| orthophoto only | — | error: an elevation source or a model is required |

`terrain`, `point-cloud` and `points` need a **projected** CRS (ODM uses UTM);
geographic inputs are refused with a clear message.

---

## 4. Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `workflow` | `auto` | `auto` / `terrain` / `point-cloud` / `points` / `mesh` (see §3) |
| `quality` | `auto` | Budget profile: **web-lite** 150 k triangles · 2048 px texture · 0.5 M points · 16 MP atlas budget; **balanced** 500 k · 4096 · 2 M · 48 MP; **high-detail** 1.5 M · 8192 · 5 M · 128 MP. `auto` reads the task's ODM options: `pc-quality` high/ultra, `mesh-size` ≥ 300 000 or `mesh-octree-depth` ≥ 12 → high-detail; `fast-orthophoto` or `pc-quality` low/lowest → web-lite; else balanced |
| `surface` | `dsm` | terrain: `dsm` (buildings, trees) or `dtm` (bare earth); falls back to whichever exists |
| `resolution_m` | `0` | grid cell size for terrain/point-cloud; 0 derives it from the budget and the covered area |
| `max_triangles` | `0` | overrides the profile's triangle budget |
| `texture_size` | `0` | longest texture side (256–8192); never finer than the orthophoto |
| `texture_quality` | `85` | JPEG quality |
| `texture_source` | `auto` | `auto` (orthophoto → point colours → shaded relief), or force one |
| `texture_budget_mp` | `0` | mesh workflow: total decoded texture megapixels |
| `fill_holes` | `true` | fill from DTM / point cloud and interpolate enclosed gaps ≤ 100 m² |
| `point_classes` | `""` | LAS classification codes to use (`2,6`); empty = all |
| `point_statistic` | `max` | point-cloud workflow: `max` (top surface), `mean` (smoother), `min` (≈ ground) |
| `max_points` | `0` | points workflow: subsample cap |
| `compression` | `draco` | `draco` (5–10× smaller; the viewer decodes it) or `none` |

Organization defaults for any of these can be saved on the **Plugins** page;
the run dialog starts from them.

---

## 5. Results and how to access them

* **3D viewer** — *View 3D* on the run row, or pick the run in the viewer's
  model switcher (`/project/<p>/task/<t>/model?run=<run>`). The viewer treats
  the file exactly like ODM's model: Z-up, `CESIUM_RTC` ignored, positions
  already local, Draco decoded in the browser, textures capped to the device's
  budget.
* **Download** — the run row's *Download*, or the private file URL in
  `output_file` (`GET /api/method/webodm_core.api.plugins.get_run?name=<run>`).
  The GLB loads in any glTF 2.0 viewer/tool that supports Draco (three.js,
  Blender, CesiumJS, gltf-transform, QGIS 3D via Blender export, …).
* **Georeference** — `output_extent`/`output_metadata.bounds_4326` on the run
  (the map extent), and inside the file:

  ```json
  "asset": {"extras": {"webodm_georef": {
    "version": 1, "epsg": 32632, "wkt": "PROJCS[...]", "up_axis": "Z", "units": "metre",
    "origin": [663553.0, 5328131.0, 539.0],          // = CESIUM_RTC.center; add to every vertex
    "bounds": [663477.7, 5328054.4, 663628.6, 5328208.0],  // absolute, in the CRS
    "z_range": [538.98, 595.87], "source": "point_cloud", "resolution_m": 0.267 }}}
  ```

  *Absolute CRS coordinate = vertex + origin.* Elevations are ODM's (the DSM's
  vertical reference), unchanged.
* **Run metadata** (`output_metadata`) — workflow, inputs used, resolved
  parameters, mesh counts (before/after decimation), texture size/coverage per
  source, hole statistics, point counts and classes, stage timings with peak
  memory, warnings. The run log (stderr tail) is shown when a run fails.

---

## 6. How it works

```
inputs ─► detect ─► choose workflow ─► elevation grid ─► fill holes ─► triangulate ─► decimate
                                       (DSM/DTM or       (DTM, points,   (2 tris/cell,    (quadric,
                                        binned LAZ)        interpolate)    hole-aware)      UV-free)
        texture: orthophoto ▸ point RGB ▸ shaded relief (painted strip-wise into one JPEG)
        GLB: positions − origin (float32), uint16 UVs, unlit material, Draco, CESIUM_RTC + extras
```

* **Grid planning.** A full grid gives 2 triangles per cell, so the cell count
  is 0.65 × the triangle budget divided by the *covered* share of the bounding
  box (flight areas are rarely rectangles; a quick overview read estimates it),
  never finer than the source, hard-capped at 3 M cells.
* **Reads.** DSM/DTM/orthophoto are warped onto the grid with `WarpedVRT`
  from the **overview level** matching the target resolution (the warper alone
  would read level 0: 4 s / 800 MB → 0.5 s / 100 MB for a 14716×11640 DSM).
  Point clouds stream through `laspy` in 1 M-point chunks and are binned with
  `np.maximum.at`/`np.add.at`.
* **Holes.** Donors (DTM, point-cloud surface) fill any masked cell — they are
  measurements. Interpolation (`rasterio.fill.fillnodata`) is limited to
  connected holes ≤ 100 m² (`rasterio.features.sieve` finds them) so the
  outside of the survey is never bridged.
* **Triangulation.** Vectorised numpy: each cell with four valid corners
  becomes two triangles split along the diagonal with the smaller height
  difference (follows ridges/gutters), three valid corners one triangle;
  counter-clockwise from above. Decimation is quadric edge collapse
  (`fast-simplification`); texture coordinates are a planar function of X/Y
  and are recomputed afterwards, so nothing is lost.
* **Texture.** Shaded relief (hypsometric ramp × hillshade of the heightfield)
  is the base; point colours and then the orthophoto are painted over it in
  1024-row strips (`np.copyto(..., where=valid)`) so an 8192² texture costs a
  strip, not several full copies.
* **GLB.** A ~300-line writer/reader (`recon/gltf.py`). Draco encoding through
  DracoPy; because DracoPy cannot quantise `tex_coord`, UVs go in as a generic
  **uint16** attribute (glTF-native `normalized` `VEC2`) — half the geometry
  bytes of float UVs. The `attributes` id map is read back from the encoded
  blob, never assumed, and was cross-checked against the Draco JS decoder the
  viewer ships (`tests/test_gltf.py`).
* **Georeference.** Origin = rounded centre of the bounds and floor of the
  minimum elevation, so float32 vertices keep millimetre precision anywhere on
  Earth. The runner (trusted side) validates `extras.webodm_georef` and derives
  the map extent (`bounds_4326`) with PROJ.

### Technology choices

Researched before implementing; the constraint set was: run as an unprivileged
Python 3.12 process inside a 2 GB address space with no network, be
pip-installable as manylinux wheels, permissive licence, and produce what the
existing three.js viewer already decodes.

| Need | Chosen | Considered and why not |
|---|---|---|
| LAS/LAZ reading | **laspy + lazrs** (BSD-2 / Apache-2.0-MIT, 0.7 MB) — chunked, pure wheels, GeoKey/WKT CRS | PDAL (BSD, needs libpdal, no clean wheel); Open3D readers (no LAZ) |
| Surface from points/rasters | **heightfield (2.5D) in numpy** — the standard web-terrain approach (Cesium terrain, ODM's own `odm_25dmesh`), deterministic, streams | Poisson/ball-pivot via Open3D (MIT, 100–400 MB) or PyMeshLab (**GPL**) — heavy, needs normals, and ODM already ships the true-3D mesh we reuse |
| Decimation | **fast-simplification** (MIT, 1.7 MB) — Fast-Quadric-Mesh-Simplification, numpy in/out | pyfqmr (MIT, equivalent, less maintained); Open3D (size); meshoptimizer bindings (no maintained wheel) |
| Draco decode/encode | **DracoPy** (Apache-2.0, 5 MB) — required to read ODM's GLB (`KHR_draco_mesh_compression` is *required* there); the viewer already ships the decoder | gltf-transform / draco CLI (Node, not in the sandbox); meshopt/KTX2 (viewer has no decoders) |
| glTF I/O | **own writer/reader** — one buffer, extras, extensions, uint16 UVs | pygltflib (MIT, thin; would still need all the buffer code); trimesh (MIT, scene-graph rewrite, many optional deps) |
| Textures | **Pillow** (MIT-CMU) + rasterio `WarpedVRT` | OpenCV (large); GDAL JPEG driver (CreateCopy-only) |

Everything lives in the sandbox image (`services/plugin-runner/requirements.txt`),
adding ~16 MB of wheels.

### Assumptions and limits

* Terrain/point-cloud workflows are **2.5D**: one elevation per X/Y. Facades
  are stretched orthophoto (true 3D comes from the `mesh` workflow on ODM's
  model). Overhangs, bridges and tree canopies read as solid.
* One texture image per model (max 8192²), so ground texture resolution is
  the survey extent ÷ 8192 at best — ~6 cm on a 500 m site, coarser on large
  sites. Use `mesh` for ODM's per-face atlases.
* A single level of detail; the viewer's own texture budget handles memory
  on weaker devices.
* `mesh` does not re-decimate ODM's geometry (its atlas UVs can't be recomputed
  after collapse); `mesh-size` at processing time controls that.
* Memory: high-detail on a 170 MP orthophoto / 10 M-point cloud peaks at
  ~1.25 GB RSS. Very large sites are coarsened automatically (3 M-cell cap,
  warning in the log). Lower `quality` or `texture_size` if a run is killed
  for memory.
* Not produced: 3D Tiles / LOD streaming, KTX2 textures, vertex normals
  (materials are unlit).

---

## 7. Platform changes made for this plugin

Kept minimal and generic (any plugin can use them):

* **`output_kind: "model"`** — a third output kind next to raster/vector:
  package validation (`plugins/package.py`), `.glb` naming (`plugins/runner.py`),
  the `WebODM Plugin` select, the runner's `POST /run` (`model_georef` reads
  `extras.webodm_georef` and derives the map extent), the map page (model runs
  get *View 3D* instead of a layer) and the 3D viewer (`?run=` + switcher).
* **Run context** — `request.json` now carries `context.task`
  (`name`, `title`, `epsg`, `wkt`, `resolution`, `processing_options`), read-only
  facts a plugin may use (here: CRS for the mesh workflow, ODM options for
  `quality: auto`).
* **Live progress** — a plugin may write `{"percent", "message"}` to
  `request.json`'s `progress_path`; the worker polls it every 2 s through the
  shared sandbox volume and writes `progress` / `progress_message` on the run
  (`WebODM Plugin Run` gained `progress_message`); the map page shows it.
* **Sandbox image** — Pillow, laspy[lazrs], fast-simplification, DracoPy.

Specs: `openspec/specs/3d-reconstruction/spec.md`, and the model-kind /
progress additions in `openspec/specs/plugin-outputs/spec.md` and
`openspec/specs/plugin-execution/spec.md`.
