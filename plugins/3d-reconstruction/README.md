# 3D Reconstruction plugin

A WebODM **user plugin** that turns a completed task's outputs into a compact,
georeferenced, web-ready 3D model (GLB) for the built-in 3D viewer.

- **Inputs** (all optional, any subset): DSM, DTM, orthophoto, LAS/LAZ point
  cloud, existing ODM textured model. The plugin detects what it was given and
  picks the workflow.
- **Workflows**: `terrain` (textured heightfield mesh from DSM / point cloud /
  DTM, textured with the orthophoto, point colours or a shaded relief) and
  `optimize-model` (the ODM textured mesh re-encoded with capped textures and
  16-bit positions).
- **Output**: one `.glb` — `KHR_mesh_quantization`, `KHR_materials_unlit`,
  `CESIUM_RTC` centre and an `extras.webodm_georef` record (CRS, origin,
  bounds) so the model stays anchored in the task's coordinate system.

Full documentation, technology choices and the user guide:
[`docs/plugins/3d-reconstruction.md`](../../docs/plugins/3d-reconstruction.md).

```bash
# tests (same libraries as the sandbox: numpy, rasterio, shapely, laspy)
../../services/plugin-runner/venv/bin/python -m pytest -q

# run locally exactly like the sandbox does
cd ../../services/plugin-runner
python -m app.cli ../../plugins/3d-reconstruction \
    --input dsm=/data/dsm.tif --input orthophoto=/data/orthophoto.tif \
    --param quality=balanced --output /tmp/model.glb \
    --context '{"task": {"epsg": 32633}}'

# package for upload -> plugins/3d-reconstruction-<version>.zip
tools/build.sh
```
