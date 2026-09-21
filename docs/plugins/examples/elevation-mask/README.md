# Elevation Mask — example user plugin

Starter plugin for WebODM's user plugin system. It takes a task's DSM (or DTM)
and writes a single-band GeoTIFF where `1` marks cells above (or below) a
threshold, `0` the rest and `255` nodata. The run panel shows how many cells
were masked and what fraction of the raster that is.

Files:

- `plugin.json` — manifest (id, inputs, parameter form, output kind)
- `main.py` — entrypoint invoked as `python main.py request.json`
- `test_main.py` — pytest unit tests

Try it:

```bash
# Unit tests
pip install numpy rasterio pytest && python -m pytest -q

# Exactly as the sandbox runs it (from services/plugin-runner with its venv)
python -m app.cli ../../docs/plugins/examples/elevation-mask \
    --input raster=/path/to/dsm.tif --param threshold=120 --output /tmp/mask.tif

# Package for upload (from inside this directory)
zip -r ../elevation-mask-1.0.0.zip . -x '__pycache__/*' -x '*.pyc'
```

Full walkthrough: [`docs/plugins/user-plugin-guide.md`](../../user-plugin-guide.md).
