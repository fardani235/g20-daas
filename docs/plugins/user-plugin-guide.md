# User Plugin Guide

WebODM runs **analysis plugins** on the outputs of a completed task
(orthophoto, DSM, DTM, point cloud, 3D model) and shows the result as a new map
layer that can also be downloaded. There are two kinds:

| | System plugins | User plugins |
|---|---|---|
| Who provides them | The platform (`services/geospatial/app/analysis/ops`) | Your organization, as an uploaded package |
| Who can see them | Every organization | Only the organization that uploaded them |
| Where they run | Inside the geospatial service | In an isolated sandbox (`services/plugin-runner`) |
| Examples | Hillshade, Contours, Object detection, Segmentation | Anything you write in Python — e.g. the [Semantic Segmentation plugin](semantic-segmentation.md) |

Both kinds appear in the same **Plugins** page and run from the same task
panel; the core processing pipeline knows nothing about individual plugins.
This guide is about writing, testing, packaging and managing **user plugins**.

A complete starter plugin lives in
[`docs/plugins/examples/elevation-mask/`](examples/elevation-mask/). Copy it
and change the pieces you need.

---

## 1. How a plugin runs

1. A member picks your plugin on a completed task, fills in the parameter form
   (generated from your `params_schema`) and clicks *Run*.
2. WebODM checks that the plugin is enabled for the organization, that the
   task has the inputs your manifest asks for, validates the parameters and
   creates a **run** record (`Queued`).
3. A worker copies your package and the input files into a fresh, private
   directory in the sandbox and asks the plugin runner to execute it.
4. The runner unpacks your package and starts your `entrypoint` as a separate
   process with one argument: the path to a `request.json`.
5. Your script reads `request.json`, writes its result to `output_path` and
   exits with status `0`. While it runs it may publish `{"percent", "message"}`
   to `progress_path`; the worker polls that file and shows it on the run
   (`Running · 45% · decimating`).
6. The runner reads georeferencing from your output, the worker moves it into
   the task's private files and the run becomes `Completed` (raster/vector
   results appear as map layers, model results open in the 3D viewer). If anything went
   wrong — a crash, a timeout, no output — the run becomes `Failed` and the
   error (with the tail of your stderr) is shown in the run panel.

Only that one run is affected by a broken plugin: the platform, other runs
and other organizations never are.

---

## 2. Create a custom plugin

Start from the example:

```bash
cp -r docs/plugins/examples/elevation-mask my-plugin
cd my-plugin
```

You now have:

```
my-plugin/
├── plugin.json      # manifest: what the plugin is, needs, accepts, produces
├── main.py          # entrypoint: reads request.json, writes the output
├── test_main.py     # unit tests (pytest)
└── README.md
```

Edit `plugin.json` (at least `id`, `label`, `description`) and put your
algorithm in `main.py`. You can add more `.py` files next to `main.py` and
import them normally; the plugin directory is the working directory when your
script runs.

### The request your script receives

```json
{
  "inputs":        {"raster": "/sandbox/runs/3f9a.../inputs/raster.tif"},
  "params":        {"threshold": 120.0, "mode": "above"},
  "context":       {"task": {"name": "p5nfvcmuif", "title": "Site A", "epsg": 32613, "wkt": "PROJCS[...]",
                             "resolution": 5.0, "processing_options": [{"name": "dsm", "value": true}]}},
  "output_path":   "/sandbox/runs/3f9a.../output.tif",
  "result_path":   "/sandbox/runs/3f9a.../result.json",
  "progress_path": "/sandbox/runs/3f9a.../progress.json",
  "work_dir":      "/sandbox/runs/3f9a.../work"
}
```

| Key | Meaning |
|---|---|
| `inputs` | One absolute path per input declared in the manifest, keyed by the input's `name`. Optional inputs the user left out (or the task lacks) are absent. These are private copies; you may read them freely. |
| `params` | The parameters, already validated against your `params_schema`, with the organization's saved defaults applied. |
| `context` | Read-only facts about the task: `name`, `title`, `epsg`/`wkt` (its CRS, when known), `resolution` and the ODM `processing_options` it was run with (a list of `{name, value}`). Use it to adapt defaults; never required. |
| `output_path` | Where you **must** write your result. The extension is `.tif` for `raster` plugins, `.geojson` for `vector` plugins and `.glb` for `model` plugins. |
| `result_path` | Optional. Write `{"metadata": {...}}` here to report numbers/strings alongside the output. |
| `progress_path` | Optional. Write `{"percent": 0-100, "message": "short status"}` here whenever your progress changes (write to a temp file and `os.replace` it so readers never see a half-written file). The platform polls it every couple of seconds and shows it on the run while it is running. |
| `work_dir` | Scratch space you may use for temporary files. Everything in it is deleted after the run. |

A minimal `main.py`:

```python
import json, sys
import rasterio

request = json.load(open(sys.argv[1]))
with rasterio.open(request["inputs"]["raster"]) as src:
    data = src.read(1)
    profile = src.profile

# ... compute something from `data` and request["params"] ...

with rasterio.open(request["output_path"], "w", **profile) as dst:
    dst.write(data, 1)
```

### What the output must look like

- **`output_kind: raster`** — a GeoTIFF with a CRS. Single-band outputs are
  rendered with a terrain colour ramp (`render_kind: dem`); 3/4-band outputs
  as imagery (`render_kind: orthophoto`). Use a `nodata` value for cells you
  want transparent. Any dtype rasterio can write is fine.
- **`output_kind: vector`** — a GeoJSON `FeatureCollection` in **EPSG:4326**
  (longitude/latitude). Feature `properties` are kept and downloadable. Very
  large collections (>5000 features) are still downloadable but not drawn on
  the map.
- **`output_kind: model`** — a binary glTF 2.0 (`.glb`) opened in the 3D
  viewer (*View 3D* on the run row) rather than drawn on the map. Follow the
  conventions of ODM's own model so the viewer treats both alike: Z-up
  coordinates in the task's projected CRS, stored **relative to an origin**
  (put it in the `CESIUM_RTC` extension), unlit materials, JPEG textures.
  Draco-compressed geometry (`KHR_draco_mesh_compression`) is supported;
  KTX2/meshopt are not. Since glTF carries no CRS, record the georeference in
  `asset.extras.webodm_georef`: `{"epsg": 32632, "origin": [x, y, z],
  "bounds": [minx, miny, maxx, maxy]}` (absolute, in that CRS) — the runner
  validates it and derives the run's map extent from it; without it the model
  is still accepted but has no extent. See `plugins/3d-reconstruction/recon/gltf.py`
  for a complete writer.

You do **not** need to compute extents or bounds for rasters and vectors: the
runner reads them from the file.

### What you can use

The sandbox interpreter has **numpy**, **rasterio** (GDAL), **shapely**,
**Pillow**, **onnxruntime** (CPU), **laspy** (+ **lazrs**, LAS/LAZ point
clouds), **fast-simplification** (mesh decimation), **DracoPy** (Draco
encode/decode) and the Python standard library. It cannot install
packages and has no network access, so pure-Python helpers — and any model
files — must ship inside your package. ONNX is the supported way to run a
machine-learning model: export it once, put the `.onnx` file in the package
and load it with `onnxruntime.InferenceSession`.

### Limits

Defaults; platform operators may raise them.

| Limit | Default |
|---|---|
| Wall-clock time (`timeout_seconds` in the manifest) | 300 s, max 3600 s |
| Memory (address space) | 2 GB |
| Output file size | 4 GB |
| Package size | 256 MB zipped, 1 GB unpacked, 500 files |

Process large rasters block by block (see the example's use of
`src.block_windows`) rather than reading them whole, so you stay under the
memory limit on real datasets.

---

## 3. Structure the manifest (`plugin.json`)

```json
{
  "id": "elevation-mask",
  "label": "Elevation Mask",
  "version": "1.0.0",
  "description": "Mark every DSM/DTM cell above (or below) an elevation threshold.",
  "entrypoint": "main.py",
  "inputs": [
    { "name": "raster", "datasets": ["dsm", "dtm"] }
  ],
  "params_schema": {
    "type": "object",
    "properties": {
      "threshold": { "type": "number", "title": "Threshold (m)", "default": 100.0 },
      "mode":      { "type": "string", "enum": ["above", "below"], "default": "above" }
    },
    "required": ["threshold"]
  },
  "output_kind": "raster",
  "render_kind": "dem",
  "timeout_seconds": 300
}
```

| Field | Required | Notes |
|---|---|---|
| `id` | yes | 2–64 lowercase letters, digits, hyphens. Unique within your organization; installed as `<org-slug>.<id>`. Re-uploading the same `id` **upgrades** the plugin. |
| `label` | no | Shown in the UI. Defaults to `id`. |
| `version` | yes | Dotted version, e.g. `1.2.0`. Shown in the UI; bump it on every upload. |
| `description` | no | One or two sentences, shown under the label. |
| `entrypoint` | no | Relative path to the script to run. Default `main.py`. |
| `inputs` | yes | List of `{name, datasets, optional?, label?}`. `name` is a slug (lowercase letters, digits, `-`, `_`). `datasets` are task fields: `orthophoto`, `dsm`, `dtm`, `point_cloud`, `model`. The run dialog lets the user pick which of them feeds the input (first available by default). A required input blocks the run when the task has none of its datasets; an input with `"optional": true` is simply left out of `request.json` instead — check `"name" in request["inputs"]`. API clients pass `inputs: {name: dataset}` to `run_plugin`; that selection is complete (optional inputs it does not name are left out), while omitting `inputs` altogether takes the first available dataset for every input. At least one input must resolve. `label` is shown in the run dialog. |
| `params_schema` | no | JSON Schema describing a flat object of scalars. Supported: `type` (`number`, `integer`, `string`, `boolean`), `enum`, `default`, `minimum`/`maximum`, `exclusiveMinimum`/`exclusiveMaximum`, `minLength`/`maxLength`, `title`, `description`, `required`. The form and server-side validation are both generated from it. |
| `output_kind` | no | `raster` (default), `vector` or `model`. |
| `render_kind` | no | Raster: `dem` (default) or `orthophoto`. Vector: any short string, default `vector`. Model: `model`. |
| `timeout_seconds` | no | 10–3600, default 300. |

Uploads with an invalid manifest are rejected with the reason; nothing is
installed.

### Multi-source plugins

Declare one input per dataset and mark the ones the plugin can do without as
optional; the user then chooses in the run dialog which datasets to use:

```json
"inputs": [
  { "name": "orthophoto", "label": "Orthophoto", "datasets": ["orthophoto"], "optional": true },
  { "name": "dsm",        "label": "DSM",        "datasets": ["dsm"],        "optional": true },
  { "name": "dtm",        "label": "DTM",        "datasets": ["dtm"],        "optional": true }
]
```

The inputs need not share a grid: reproject/resample them onto one reference
grid yourself (rasterio's `WarpedVRT` does this lazily). The Semantic
Segmentation plugin (`plugins/semantic-segmentation/`) is a complete example
of this pattern, including model selection by available inputs.

---

## 4. Test the plugin

### Unit tests

The example ships `test_main.py`, which builds a tiny DEM, runs the plugin's
functions directly and checks the output. Run it with the same libraries the
sandbox provides:

```bash
python3 -m venv venv && source venv/bin/activate
pip install numpy rasterio shapely pytest
python -m pytest -q
```

### Run it exactly like the sandbox does

The plugin runner has a CLI that performs the real package → subprocess →
output flow on your machine, without WebODM:

```bash
cd services/plugin-runner
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt

python -m app.cli /path/to/my-plugin \
    --input raster=/path/to/some/dsm.tif \
    --param threshold=120 --param mode=above \
    --output /tmp/out.tif
```

It prints the metadata the run panel would show (yours plus the
georeferencing) or, on failure, the same error message the UI would show.
Pass a `.zip` instead of a directory to test the exact package you are about
to upload.

### Against a running stack

If you have the full stack up (`docker compose up`), upload the package (next
sections), open a completed task and run the plugin from the task panel. Failed
runs show the error and the tail of your script's stderr, so `print(...,
file=sys.stderr)` is a useful debugging tool.

---

## 5. Package it

A package is a zip with `plugin.json` **at its root** (not inside a folder):

```bash
cd my-plugin
zip -r ../my-plugin-1.0.0.zip . -x '*.pyc' -x '__pycache__/*' -x 'venv/*' -x '.git/*'
unzip -l ../my-plugin-1.0.0.zip     # plugin.json and main.py must be top-level entries
```

Rules enforced at upload: no absolute paths or `..` in member names, no
symlinks, at most 256 MB zipped / 1 GB unpacked / 500 files. Tests and
READMEs can stay in the package; they are simply ignored.

---

## 6. Upload and install

You must be an **organization owner** (or a platform admin who is a member of
the organization).

**In the app:** *Plugins → Upload plugin*, choose the zip. The plugin appears
in the list with the badge **Custom**, already **Enabled** for your
organization. Uploading a package with the same `id` again replaces it and
keeps your organization's enablement and saved defaults.

**From the command line:**

```bash
BASE=https://your.webodm.host
curl -c cj.txt -X POST "$BASE/api/method/login" -F usr=you@example.com -F pwd=secret
CSRF=$(curl -b cj.txt -s "$BASE/api/method/webodm_core.api.csrf.get_token" | python3 -c 'import json,sys;print(json.load(sys.stdin)["message"])')

curl -b cj.txt -H "X-Frappe-CSRF-Token: $CSRF" \
     -F "file=@my-plugin-1.0.0.zip" \
     "$BASE/api/method/webodm_core.api.plugins.upload_plugin"
```

The response is the installed catalog entry (`name`, `version`, `enabled`,
`runnable`, …) plus `created: true|false` (install vs. upgrade).

---

## 7. Enable, disable or remove

| Action | In the app (Plugins page) | API (`/api/method/webodm_core.api.plugins.…`) |
|---|---|---|
| Disable / enable for your organization | *Disable* / *Enable* button | `save_plugin_setting` with `{"plugin": "<org-slug>.<id>", "enabled": false}` |
| Change default parameters | gear icon | `save_plugin_setting` with `{"plugin": …, "settings": {…}}` |
| Upgrade | *Upload plugin* with the same `id` | `upload_plugin` |
| Remove | trash icon → confirm | `remove_plugin` with `{"plugin": "<org-slug>.<id>"}` |

Disabling keeps the plugin, its settings and its run history; members simply
cannot start new runs. **Removing** deletes the plugin, its package, its
settings **and all of its runs and their outputs** — it cannot be undone.

Platform admins additionally have a kill switch (`Platform Enabled` on the
*WebODM Plugin* record) that blocks a plugin for everyone; the list then shows
*Disabled by platform*.

---

## 8. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Upload rejected: *Invalid plugin package: package has no plugin.json at its root* | You zipped the folder instead of its contents. Run `zip` from inside the plugin directory. |
| Upload rejected: *entrypoint 'main.py' not found* | The file named in `entrypoint` is not in the zip. |
| Run refused: *Task is missing the required input 'raster'* | The task has none of the `datasets` your input lists. Add `orthophoto`/`dsm`/… as appropriate. |
| Run `Failed`: *plugin exited with status 1: …* | Your script raised; the traceback tail is in the error. Reproduce with `python -m app.cli`. |
| Run `Failed`: *plugin timed out after 300s* | Raise `timeout_seconds` (max 3600) or process in blocks. |
| Run `Failed`: *plugin exited successfully but wrote no output* | You did not write to `request["output_path"]`. |
| Run `Failed`: *output is not a readable raster* | The file at `output_path` is not a GeoTIFF rasterio can open (e.g. missing CRS). |
| Run `Failed`: *plugin runner unreachable* | The sandbox service is down — contact the platform operator. |
| `MemoryError` / killed silently | You exceeded the 2 GB address-space limit; process in blocks. |

---

## 9. What the sandbox does and does not allow

Your code runs in a separate container as an unprivileged user with a
read-only filesystem, no network, capped CPU time, memory, output size and
process count, and only its own run directory to read and write. It cannot
reach the database, other organizations' data, the internet or the platform's
own services. A plugin that misbehaves fails its own run and nothing else.

This also means a plugin cannot download models or call external APIs; ship
everything it needs inside the package (within the size limits).
