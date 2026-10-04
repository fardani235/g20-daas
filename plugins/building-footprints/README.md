# Building Footprints — WebODM user plugin

Extracts **one polygon per building** from a task's orthophoto with the HOT
DINOv3 building model (mask + boundary + signed-distance heads) and a
watershed on the distance plane that separates touching buildings. Output is
a GeoJSON layer (EPSG:4326) with `class`, `score` and `area` per footprint.
Full documentation — install, parameters, how it works, model card format,
references: [`docs/plugins/building-footprints.md`](../../docs/plugins/building-footprints.md).

```
plugin.json          manifest (output_kind vector, render_kind segmentation)
main.py              entrypoint
bfplugin/            grid alignment, Gaussian-weighted tiling, ONNX inference,
                     watershed instance separation, vectorisation + regularisation
models/              model cards (*.json); weights are fetched, never committed
tools/fetch_models.py  download + sha-256 verify the weights the cards describe
tools/update_manifest.py  regenerate the manifest's model enum from the cards
tools/build.sh       update_manifest + zip  -> ../building-footprints-<version>.zip
tests/               pytest (numpy, scipy, scikit-image, rasterio, shapely, onnxruntime)
```

```bash
# tests (same libraries the sandbox provides; fixtures are tiny synthetic ONNX graphs)
python3 -m venv venv && . venv/bin/activate
pip install numpy scipy scikit-image rasterio shapely onnxruntime pytest
python -m pytest -q

# weights (220 MB, Apache-2.0) + package
python3 tools/fetch_models.py
tools/build.sh                     # -> ../building-footprints-1.0.0.zip

# run like the sandbox does
cd ../../services/plugin-runner && python -m app.cli ../../plugins/building-footprints \
    --input orthophoto=ortho.tif --output /tmp/footprints.geojson
```
