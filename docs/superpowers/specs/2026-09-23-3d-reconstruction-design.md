# 3D Reconstruction Plugin — Design Spec

**Date:** 2026-09-23
**Goal:** A plugin that turns whatever ODM outputs a task has (orthophoto, DSM, DTM, LAZ, textured mesh) into one web-ready, geospatially consistent GLB that opens in the existing 3D viewer — with clear job status, progress and errors — while keeping the platform changes small and generic.
**Approach:** Implement it as a **user plugin** running in the existing sandbox (`services/plugin-runner`), add a third output kind (`model`) to the plugin framework, and reuse everything else (upload, org scoping, run records, private files, polling UI, three.js viewer with Draco).

User guide: [`docs/plugins/3d-reconstruction.md`](../../plugins/3d-reconstruction.md). Spec: `openspec/specs/3d-reconstruction/spec.md`.

---

## What the codebase offered (inspection)

| Area | Finding | Consequence |
|---|---|---|
| Plugin framework | Manifest-driven user plugins (zip + `plugin.json`), five task datasets as inputs (`orthophoto`, `dsm`, `dtm`, `point_cloud`, `model`), optional inputs, JSON-schema params; **outputs limited to `raster`/`vector`** in `plugins/package.py`, `plugins/runner.py`, the `WebODM Plugin` select, the runner's `/run` and the map UI | Add `model` at those five places; nothing else in the framework needs to know |
| ODM integration | `task_runner` stores `odm_orthophoto.tif`, `dsm.tif`, `dtm.tif`, `odm_georeferenced_model.laz`, `odm_textured_model_geo.glb` (or an OBJ zip fallback); `epsg`/`wkt` on the task; ODM options as `[{name, value}]` (double-encoded JSON) | All five are usable inputs; the CRS and options are worth handing to plugins |
| ODM GLB | Draco-compressed (`KHR_draco_mesh_compression` **required**), `CESIUM_RTC` centre, `KHR_materials_unlit`, 41–53 JPEG atlases up to 8192² | Reading it needs a Draco decoder in Python; output should follow the same conventions |
| LAZ | LAS 1.2 point format 3, EPSG in GeoKey 3072, 8-bit RGB in 16-bit fields, sometimes classified (2/3/6) | laspy + lazrs read it; detect 8-bit colours; classes are a useful filter |
| Storage / jobs | Private files via `save_private_file_from_path`; RQ `long` queue; run doc has `progress` but it only ever went 0 → 100; `/run` is synchronous; worker and runner share the `plugin_sandbox` volume | Progress can flow through a file on the shared volume |
| Viewer | three.js 0.185 + `DRACOLoader` (decoder vendored), Z-up handling, large-coordinate recentring, texture budget; loads only `task.model` | Draco output is free; the viewer needs a way to load a run's file |
| Sandbox | Python 3.12, `RLIMIT_AS` 2 GB, single-threaded BLAS/GDAL, scrubbed env, no network; numpy/rasterio/shapely/onnxruntime | Everything must be pip wheels; memory is the binding constraint |

## Technology research

Constraints: Python 3.12 manylinux wheels, permissive licence, 2 GB address space, no network, output decodable by the existing viewer.

| Need | Options considered | Decision |
|---|---|---|
| Read LAS/LAZ | PDAL (BSD; needs libpdal, no pip wheel), Open3D (no LAZ), **laspy + lazrs** (BSD-2 / Apache-2.0-MIT; 0.7 MB; chunked iteration; VLR access for CRS) | laspy + lazrs |
| Surface reconstruction | Poisson / ball pivoting via Open3D (MIT, 100–400 MB, needs normals, dense output), PyMeshLab (**GPL**), CGAL (GPL/LGPL, heavy), **2.5D heightfield** in numpy (what Cesium terrain and ODM's own `odm_25dmesh` do for nadir surveys; deterministic; streams) | Heightfield. True 3D comes from ODM's mesh, which we reuse and optimise |
| Decimation | **fast-simplification** (MIT, 1.7 MB, quadric edge collapse, numpy API), pyfqmr (MIT, similar, less maintained), Open3D (size), meshoptimizer (no maintained wheel) | fast-simplification; UVs are planar so they are recomputed after collapse |
| Draco | **DracoPy** (Apache-2.0, 5 MB; decode + encode), gltf-transform/draco CLI (Node, not in sandbox) | DracoPy. Caveat found: it does not quantise `tex_coord` → UVs are written as a generic uint16 attribute (glTF-native `normalized`), and the attribute-id map is read back from the blob rather than assumed; verified against the vendored JS decoder |
| glTF I/O | pygltflib (MIT, thin dataclasses), trimesh (MIT, scene rewrite + optional deps), **own ~300-line writer/reader** | Own module: one buffer, extras, extensions, exact control |
| Textures | **Pillow** + rasterio `WarpedVRT`; OpenCV (large); GDAL JPEG (CreateCopy only) | Pillow |
| Compression alternatives | KTX2/Basis (viewer has no transcoder), meshopt (no decoder), KHR_mesh_quantization (fewer bytes but no decoder needed) | Draco default, `compression: none` fallback |

Added to `services/plugin-runner/requirements.txt`: `Pillow`, `laspy[lazrs]`, `fast-simplification`, `DracoPy` (~16 MB of wheels).

## Architecture

```
plugins/3d-reconstruction/
  main.py                 entrypoint: request.json → recon.pipeline.run → output.glb + result.json (+ progress.json)
  recon/pipeline.py       detect inputs → choose workflow → terrain | point-cloud | points | (optimize.mesh)
  recon/heightfield.py    Grid, coverage-aware resolution, overview-level WarpedVRT reads, donor + sieve-limited hole filling
  recon/pointcloud.py     laspy chunks → z max/mean/min + RGB per cell; strided subsample; GeoKey/WKT CRS
  recon/mesh.py           vectorised heightfield triangulation (diagonal by height), quadric decimation, planar UVs
  recon/texture.py        shaded relief base, strip-wise orthophoto painting, point colours, atlas shrinking
  recon/gltf.py           GLB writer/reader, Draco (generic uint16 UVs), CESIUM_RTC + webodm_georef extras
  recon/optimize.py       mesh workflow: ODM GLB / glTF zip / OBJ zip → textures within budget, re-encoded, georef
  recon/params.py         15 parameters, 3 quality profiles, ODM-option heuristics for quality:auto
  recon/progress.py       stderr stages with duration + peak RSS, progress file, summary for metadata
```

### Data flow (terrain)

1. `raster_info` → CRS (must be projected), bounds, native res; `valid_fraction` from a 512² overview read.
2. `auto_resolution`: cells = 0.65 × triangle budget ÷ coverage → ~1.3× budget triangles before decimation (the collapse step is the memory peak; 0.75 gave 2.26 M tris / 1.1 GB at high-detail, 0.65 gives 1.96 M / 0.8 GB).
3. `read_on_grid` picks the overview level ≤ target resolution and warps from it (level 0 warping read the whole 700 MB DSM: 4 s / 800 MB → 0.5 s / 100 MB).
4. Holes: DTM donor → point-cloud donor (one `rasterize` pass, only if > 0.1 % cells missing or colours needed) → `fillnodata` limited to connected holes ≤ 100 m² (`sieve`).
5. `triangulate` (two triangles per full cell, one per 3-valid-corner cell, CCW from +Z) → `simplify` → `planar_uvs` over the texture grid's bounds.
6. Texture: relief resampled once as the writable base; point colours composited; orthophoto painted in 1024-row strips with `np.copyto(where=valid)` from the overview ≤ 1.35× the texel size, bilinear. 8192² went from 121 s / +840 MB to 7 s / +410 MB.
7. GLB: positions − origin as float32, uint16 UVs, one unlit material, Draco (14-bit positions, level 7), `CESIUM_RTC`, extras.

### Georeference contract

`asset.extras.webodm_georef = {version, epsg, wkt, origin, bounds, z_range, up_axis: "Z", units: "metre", source, ...}`, origin = `CESIUM_RTC.center`. The runner (trusted) validates the block, computes `bounds_4326`/`extent` with PROJ and counts triangles/points/images — mirroring how it derives georef for rasters and vectors, so plugins never compute map extents themselves.

### Platform changes (generic, all covered by tests)

| Layer | Change |
|---|---|
| `plugins/package.py` | `OUTPUT_KINDS += model`, `render_kind` default/validation for model, input names may contain `_` |
| `plugins/runner.py` | `.glb` extension; `task_context(task)`; progress writer (`db_set` + own commit) |
| `plugins/sandbox.py` | `context` in the `/run` payload; request runs in a helper thread while the Frappe thread polls `progress.json` on the shared volume |
| DocTypes | `WebODM Plugin.output_kind` option `model`; `WebODM Plugin Run.progress_message` |
| `api/plugins.py` | `progress_message` in `list_runs` / `get_run` |
| `services/plugin-runner` | `output_kind: model` accepted; `model_georef`; `context` and `progress_path` in `request.json`; CLI `--context`; 4 wheels |
| Frontend | `isModelRun`/`runModelRoute`/`runProgressText`; run rows: *View 3D* + live progress, no layer for models; viewer: `modelSourceFor`/`modelChoices`, `?run=` support, switcher with reconstructions |

## Validation

* Plugin: 21 pytest tests on synthetic data (all workflows, hole rules, CRS refusal, entrypoint exit codes, Draco layout vs the vendored JS decoder).
* Runner: 26 tests incl. the real plugin through `run_package` and the model georef derivation.
* Frappe: 9 new tests (manifest, context, progress polling, model run lifecycle) + existing plugin suites, run inside the Frappe image against a fresh site.
* Frontend: 180 vitest tests, production build.
* Real data, inside the sandbox image (`RLIMIT_AS` 2 GB, single thread): 10.5 M-point LAZ + 170 MP orthophoto + 14716×11640 DSM at `high-detail` → 1.5 M triangles, 8192×6479 texture, 8.7 MB GLB in 22 s, peak 1.22 GB; ODM's 53-atlas Draco GLB → `web-lite` 4.8 MB (50.9 → 13.1 MP of texture) in 3 s. Previews rendered with `tools/preview.py` confirmed orientation and texture alignment.

## Out of scope / follow-ups

* Streaming LOD (3D Tiles / quantized-mesh) for very large sites; a single GLB is produced.
* Decimating ODM's atlas-textured mesh (would need UV-aware collapse or re-baking).
* KTX2 textures (no transcoder in the viewer), vertex normals / lit materials.
* Cancelling a run mid-execution in the sandbox (the runner has no cancel endpoint; the worker still discards the output on cancel).
* `download_run_output` still reads the whole artifact into memory; the viewer streams the private file URL directly.
