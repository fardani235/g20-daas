# Change Detection — WebODM user plugin

Compares **two surveys of the same site** — a *before* and an *after* — and
reports what changed and how much: cut and fill regions with area, volume and
uncertainty, the share of the site that changed, and (secondarily) places
that only look different. Classical raster analysis on the surveys' elevation
surfaces and orthophotos; no machine learning, nothing to download.

Full documentation — how the two surveys are chosen, every step of the
method, the numbers and how far to trust them, where it struggles:
[`docs/plugins/change-detection.md`](../../docs/plugins/change-detection.md).

```
plugin.json          manifest: change regions (output_kind vector, render_kind change)
plugin-raster.json   manifest: elevation difference GeoTIFF (output_kind raster)
main.py              entrypoint
cdplugin/            grid.py      common CRS / overlap / resolution, warped reads
                     align.py     height offset, tilt, noise, horizontal shift — and their uncertainty
                     detect.py    shift-tolerant difference, level of detection, minimum area
                     visual.py    vegetation mask, appearance-only change (orthophotos)
                     regions.py   areas, volumes, error propagation, GeoJSON
                     pipeline.py  the run, reliability grade, summary
tools/build.sh       -> ../change-detection-<version>.zip, ../change-detection-raster-<version>.zip
tests/               pytest on a synthetic site surveyed twice (numpy, scipy, rasterio, shapely)
```

Both packages need the platform's *comparison inputs* (a manifest input with
`"survey": "reference"`, see `docs/plugins/user-plugin-guide.md`): the run is
started on one survey and the user picks the second one from the same project.

```bash
# tests (same libraries the sandbox provides)
python3 -m venv venv && . venv/bin/activate
pip install numpy scipy rasterio shapely pytest
python -m pytest -q

# packages
tools/build.sh

# run like the sandbox does: this survey = October, comparison survey = September
cd ../../services/plugin-runner && python -m app.cli ../../plugins/change-detection \
    --input surface=oct_dsm.tif --input reference_surface=sep_dsm.tif \
    --input orthophoto=oct_ortho.tif --input reference_orthophoto=sep_ortho.tif \
    --param reference_role=before --output /tmp/change.geojson
```
