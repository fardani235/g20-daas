# 3D Reconstruction plugin

Builds one web-ready, georeferenced GLB from a task's ODM outputs and opens it
in the platform's 3D viewer. Full guide: `docs/plugins/3d-reconstruction.md`.

| Workflow      | Uses                                   | Produces                                   |
|---------------|----------------------------------------|--------------------------------------------|
| `terrain`     | DSM or DTM (+ orthophoto, DTM, LAZ)    | textured heightfield mesh                  |
| `point-cloud` | LAZ (+ orthophoto, DSM/DTM)            | textured heightfield mesh from the points  |
| `points`      | LAZ (+ orthophoto)                     | coloured point primitive                   |
| `mesh`        | existing ODM GLB / OBJ archive         | same mesh, textures shrunk, Draco, georef  |
| `auto`        | whatever the task has                  | `mesh` > `terrain` > `point-cloud`         |

Output conventions match ODM's GLB: Z-up, coordinates local to a
`CESIUM_RTC` origin, unlit materials; `asset.extras.webodm_georef` carries
EPSG, origin and absolute bounds so the platform can place it on the map.

## Layout

```
main.py            entrypoint (python main.py request.json)
plugin.json        manifest: 5 optional inputs, 15 parameters, output_kind "model"
recon/
  pipeline.py      input detection, workflow choice, terrain/point-cloud/points
  optimize.py      the mesh workflow (existing model -> web budget)
  heightfield.py   grids, overview-aware raster reads, hole filling
  mesh.py          triangulation, quadric decimation, planar UVs
  pointcloud.py    laspy/lazrs: header CRS, gridding, subsampling
  texture.py       orthophoto / point colour / shaded-relief textures, atlas shrinking
  gltf.py          GLB writer + reader (Draco via DracoPy), georef extras
  objloader.py     ODM OBJ/MTL archive fallback
  params.py        parameters, quality profiles, ODM-option heuristics
  progress.py      stderr log, progress file, stage timings
tests/             pytest suite on synthetic data (no real datasets needed)
tools/build.sh     package for upload;  tools/preview.py  render a GLB to PNG
```

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install numpy rasterio shapely Pillow "laspy[lazrs]" fast-simplification DracoPy pytest
python -m pytest -q tests
# run exactly like the sandbox does (from services/plugin-runner):
python -m app.cli ../../plugins/3d-reconstruction --input dsm=/data/dsm.tif \
    --input orthophoto=/data/orthophoto.tif --param quality=balanced --output /tmp/out.glb
python tools/preview.py /tmp/out.glb /tmp/out.png
tools/build.sh   # -> plugins/3d-reconstruction-<version>.zip
```

Dependencies beyond the sandbox's base stack (`numpy`, `rasterio`, `shapely`):
`laspy[lazrs]` (BSD-2 / Apache-2.0), `fast-simplification` (MIT), `DracoPy`
(Apache-2.0), `Pillow` (MIT-CMU) — all in `services/plugin-runner/requirements.txt`.
