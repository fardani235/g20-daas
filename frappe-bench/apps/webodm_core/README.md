### WebODM Core

Core DocTypes and business logic for the WebODM Frappe rework: projects, tasks,
processing nodes, the NodeODM processing pipeline, and the geospatial tile proxy.

### Architecture

```
Vue frontend ──▶ webodm_core API ──▶ NodeODM (photogrammetry)
                      │                    │
                      │   all.zip assets ◀─┘
                      ▼
              Geospatial service (COG + tiles)
```

- **DocTypes**: `WebODM Project`, `WebODM Task` (+ `WebODM Task Image` child),
  `WebODM Processing Node`, `WebODM Preset`, `WebODM Settings`, and others.
- **API** (`webodm_core/api/`):
  - `task.py` — `upload_images`, `process_task`, `cancel_task`, `get_task_console`.
  - `tiles.py` — session-authed, same-origin proxy (`info`, `serve`) that
    forwards to the geospatial service. Keeping the proxy in Frappe means Leaflet
    `<img>` tile requests carry the session cookie and the geospatial service
    never needs Frappe auth/storage knowledge.
- **Processing** (`webodm_core/processing/`):
  - `node_client.py` — thin NodeODM REST client.
  - `task_runner.py` — the pipeline (below), driven by scheduler cron.

### Processing pipeline

1. `upload_images` stores each image as a private File, extracts EXIF GPS into
   the `WebODM Task Image` row, and creates a Pending task.
2. Scheduler (`hooks.py` → `scheduler_events`, every 1 min) runs
   `process_pending_tasks` (dispatch Pending → NodeODM, status → Running) and
   `update_running_tasks` (poll Running tasks).
3. On completion, `_download_assets` pulls `all.zip`, extracts orthophoto / DSM /
   DTM / point cloud / model, and stores each as a File.
4. For each georeferenced raster it calls the geospatial service `/export/cogify`
   to convert to a Cloud Optimized GeoTIFF and persists the `epsg`, `wkt`, and
   `*_extent` (GeoJSON Polygon, EPSG:4326) fields used to place map overlays.

### ⚠️ EXIF GPS must survive upload (georeferencing depends on it)

ODM georeferences the reconstruction from each image's **EXIF GPS**. If the
geotags are missing, ODM falls back to a tiny arbitrary local model — the visible
symptom is a near-empty orthophoto only tens of pixels wide.

Frappe's File save strips EXIF from JPEGs when the system setting
`strip_exif_metadata_from_uploaded_images` is enabled, which silently removes the
geotags **after** `upload_images` has already read GPS into the DB (so the map
still shows markers while ODM output collapses).

Two defenses are in place:

- The setting is disabled on the site.
- `task.py::_save_task_image_file` writes the untouched original bytes back to
  disk if Frappe altered them, and repairs the File hash/size — so correctness
  no longer depends on the global setting.

See `../../../TROUBLESHOOTING-orthophoto-exif.md` for the full root-cause writeup
and how to re-run tasks whose images were uploaded before the fix.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch HEAD
bench install-app webodm_core
```

### Configuration

| Site config key | Default | Purpose |
|---|---|---|
| `geospatial_url` / `webodm_geospatial_url` | `http://127.0.0.1:5000` | Geospatial service base URL |

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/webodm_core
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
