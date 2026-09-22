# Semantic Segmentation — WebODM user plugin

Classifies a task's orthophoto, DSM and/or DTM into land-cover, height or
landform classes with pluggable models, and writes a class mask (GeoTIFF) or
class polygons (GeoJSON). Full documentation, run examples for every input
combination, model references and the model-card format:
[`docs/plugins/semantic-segmentation.md`](../../docs/plugins/semantic-segmentation.md).

```
plugin.json            manifest (mask package)         main.py    entrypoint
plugin-polygons.json   manifest (polygons package)     segplugin/ pipeline, grid alignment, tiling, fusion, backends
models/                model cards (*.json) + ONNX weights
tools/build.sh         build both zips                 tools/fetch_models.py  fetch/export the FLAIR weights
tests/                 pytest (numpy, rasterio, shapely, onnxruntime)
```

```bash
# tests (same libraries the sandbox provides)
python3 -m venv venv && . venv/bin/activate && pip install numpy rasterio shapely onnxruntime pytest
python -m pytest -q

# models + packages
/tmp/exportvenv/bin/python tools/fetch_models.py     # see docs; needs torch once
tools/build.sh                                       # -> ../semantic-segmentation-1.0.0.zip, ../semantic-segmentation-polygons-1.0.0.zip

# run like the sandbox does
cd ../../services/plugin-runner && python -m app.cli ../../plugins/semantic-segmentation \
    --input orthophoto=ortho.tif --input dsm=dsm.tif --input dtm=dtm.tif --output /tmp/mask.tif
```
