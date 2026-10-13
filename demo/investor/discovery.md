# Discovery — what the deployed platform actually does

Explored on 2026-10-13 against the local Kubernetes deployment, signed in as an
organization owner. Everything below was checked in the running application
(browser and read-only API calls); nothing was taken from documentation alone.

Each feature is marked:

- **Shown** — verified working and used in the video.
- **Verified** — works, left out of the video (reason given).
- **Excluded** — present in the UI but not a real capability or result; must not be presented as one.
- **Not available** — looked for and not found in this deployment.

## How the application is reached

| | |
|---|---|
| Cluster | minikube, namespace `webodm` (Helm chart `infra/helm/webodm`) |
| Entry point | `caddy` Service, NodePort **32443** (HTTPS) / 32080 (HTTP) |
| URL | `https://webodm.local:32443` — `webodm.local` resolves to the minikube node through `/etc/hosts` |
| TLS | Locally issued certificate; the recorder ignores certificate errors for this host |
| Single-page app | `/assets/webodm_frontend/frontend/` (the site root redirects there) |
| Sign-in | Frappe session cookie (`sid`) from `POST /api/method/login` |

No port-forward is needed. Workloads running during discovery:

| Workload | Image | Role |
|---|---|---|
| `caddy` | `webodm-caddy:2` | TLS termination, routing |
| `frappe-web`, `-worker`, `-scheduler`, `-socketio` | `webodm-frappe:16.34.0` | API, business logic, background jobs |
| `geospatial` | `webodm-geospatial:1` | Map tiles, raster metadata, volume and profile computation, system plugins |
| `plugin-runner` | `webodm-plugin-runner:1` | Sandbox for marketplace / user analysis plugins |
| `nodeodm` | `opendronemap/nodeodm` | Photogrammetry engine (ODM 3.6.2 produced the outputs on file) |
| `provisioner` | `webodm-provisioner:1` | On-demand compute for processing |
| `postgres` (PostGIS 16), `redis-cache`, `redis-queue`, `minio` | | State, queues, object storage |

Architecture in one line: a Frappe v16 backend with a Vue 3 single-page frontend
(Leaflet for maps, Three.js and Potree for 3D), a FastAPI geospatial service for
rasters and point clouds, and a sandboxed runner for analysis plugins.

## Data available for the demo

Organization **G20 Tech**, 2 projects, 3 tasks, all completed. No processing was
started for the video.

| Project | Task | Source | Outputs on file |
|---|---|---|---|
| Urban | Urban - Task 1 | dataset *Cadastre*, 68 images | Orthophoto 14,740 × 11,593 px at 5.00 cm/px (EPSG:32632, 291 MB COG), DSM, DTM, point cloud of 10,498,703 points, textured model (307k triangles) |
| Beach | Beach - Task 1 | dataset *Brighton*, 18 images | Orthophoto 5 cm/px (EPSG:32615), DSM, DTM, point cloud (1.5 M points), model |
| Beach | Beach - Task 1 (copy) | dataset *Brighton*, 18 images | Same products from a second processing run of the same images |

Datasets: Brighton (18 images), Toledo (87), Eagle (44), Belleview (38),
Cadastre (68). Toledo, Eagle and Belleview have not been processed.

Analysis results on file:

| Plugin | Task | Result |
|---|---|---|
| Object Detection (model *VisDrone — vehicles & people, YOLO11s*) | Urban - Task 1 | 184 detections: 137 car, 20 pedestrian, 20 van, 5 truck, 1 bus, 1 motor; confidence 0.40–0.92 |
| Object Detection | Beach - Task 1, Beach - Task 1 (copy) | 11 detections on the copy (6 car, 4 pedestrian, 1 bicycle) |
| Change Detection | Beach - Task 1 (copy) vs Beach - Task 1 | 0.5 % of 8,806 m² flagged; fill 17.2 m³ ± 1.7, cut 4.89 m³ ± 2.69; reliability "medium" |

**Urban** is the hero dataset: it is the largest, the most detailed, and has
every product. **Beach** is used only for change detection, because it is the
only project with two surveys.

## Features

### Project and survey management

| Feature | Status | Notes |
|---|---|---|
| Dashboard: project and task counts by status, recent projects | **Shown** | |
| Projects list; create, rename, delete | **Verified** | Not exercised: creating or deleting would change business data. |
| Dataset library: reusable image sets, usage count, size | **Shown** | |
| Dataset detail: thumbnails, per-image GPS position, altitude and capture time | **Shown** | The "Created by" card prints the account e-mail; it is blurred in the recording. |
| Ground control (GCP file) on a dataset or at upload | **Verified** | Control visible in the task dialog; none of the datasets has a GCP file. |
| Processing presets (10 system presets) | **Verified** | The preset picker appears in the task dialog; the Presets page itself adds little on screen. |
| Organization settings (default preset, upload limit) | **Verified** | Admin form, not relevant to the story. |
| Multi-organization model (owner/member roles, invitations) | **Verified** | Only one organization exists, so isolation cannot be shown. The video says "per organization" and nothing stronger. |

### Imagery upload and photogrammetry

| Feature | Status | Notes |
|---|---|---|
| "Add task" dialog: existing dataset or drag-and-drop upload, GCP, preset, full ODM option list | **Shown** | Opened and cancelled. No task is created. |
| Completed tasks with status, image count, output summary | **Shown** | |
| Running a new ODM job | **Verified, not run** | The engine pod is up, but a run is slow on this machine and the brief says to reuse completed jobs. The video never claims a processing time. |
| Edit / duplicate a task | **Verified** | Buttons present; duplicating creates data. |
| Task console log | **Verified** | The stored tasks have no log retained ("No console output"), so the log is not featured. |

### 2D visualization

| Feature | Status | Notes |
|---|---|---|
| Orthophoto on a Leaflet map, opacity control | **Shown** | Crisp to zoom 21 (about native resolution). |
| DSM and DTM as colour-ramped layers, opacity control | **Shown** | |
| Basemaps: Streets (OSM), Satellite and Topographic (Esri) | **Shown** | Internet tiles. Basemaps stop at zoom 19; beyond that only the survey is drawn. |
| Camera position markers and flight path | **Shown** | |
| Raster details (size, bands, pixel size, CRS, layout) | **Shown** | On the task console page. |
| Measure distance, area | **Verified** | Same tool as volume; one measurement is enough for the story. |
| Measure volume against the DSM, five base-surface methods | **Shown** | A read-only computation by the geospatial service; nothing is stored. |

### 3D visualization

| Feature | Status | Notes |
|---|---|---|
| Textured model viewer: rotate, pan, zoom, preset views, grid, fullscreen | **Shown** | Three.js, GLB. |
| Point cloud viewer, streamed octree | **Shown** | Potree format, converted once per task; all three tasks are already converted. |
| Colour by RGB, elevation, classification; per-class toggles | **Shown** | Classes present: unclassified, ground, low vegetation, building. |
| Point cloud measurement: distance, area, volume | **Verified** | |
| Elevation profile along a line, stats, CSV export | **Shown** | Finishing with Enter is reliable; finishing a two-point line with a double-click is not, so the script uses Enter. |

### Geo-AI and analysis

| Feature | Status | Notes |
|---|---|---|
| Object detection on the orthophoto, pluggable ONNX detectors (VisDrone YOLO11s, DeepForest tree crowns) | **Shown** | The stored run is shown. The run dialog is opened to show the detector choice and cancelled. |
| Detection overlay with per-class legend and counts; GeoJSON download | **Shown** | Output includes some false positives (a "truck" on a roof edge); it is shown as produced. |
| Change detection between two surveys: cut/fill regions, volumes with uncertainty, reliability grade, per-region popups | **Shown** | **The only comparison on file is between two processing runs of the same flight.** The video says so on screen and in the narration, and presents the 0.5 % result as what it is. |
| Semantic segmentation (land cover) | **Verified to exist, not demonstrated** | A system plugin, disabled for this organization, with no results on file. Enabling it would change organization settings. The video lists it by name on the Plugins page and shows no segmentation output. |
| Contours, Hillshade | **Verified to exist, not demonstrated** | Same situation as segmentation. |
| 3D Reconstruction, Building Footprints plugins | **Not available** | In the repository, not published or installed in this deployment. Not mentioned in the video. |

### Business-oriented features

| Feature | Status | Notes |
|---|---|---|
| Downloads: orthophoto, DSM, DTM (cloud-optimized GeoTIFF), point cloud (LAZ), model (GLB) | **Shown** | Buttons are pointed at, not pressed. |
| Plugin management per organization (enable, configure, upload) | **Shown** | |
| Marketplace: catalog, categories, versions, licences, install per organization | **Shown** | Two products published. The install counter reads "1 install"; it is visible but not called out. |
| Reports (PDF or similar) | **Not available** | No report generator exists. The video does not mention reports. |
| Invoices / billing history | **Excluded** | The page renders four hard-coded sample rows from the frontend source. Showing it would imply billing activity that does not exist. |
| Public landing page | **Excluded** | Its animated "flight sim" shows illustrative figures (a stockpile volume, confidence percentages, a thermal mode) that do not come from real data. Only its headline is reused, as the title card's wording. |

## Workflows the recorder relies on

All selectors are role- or text-based and were exercised before recording.

1. **Sign in** — API login, then the `sid` cookie is placed in the browser. The sign-in form is never loaded with credentials.
2. **Open a task** — `/project/<name>`, click the task title; wait for the "Orthophoto" checkbox.
3. **Layers** — checkboxes "DSM", "DTM", "Object Detection", "Change Detection vs …", "Image markers", "Flight path"; radio "Satellite".
4. **Map navigation** — one wheel notch (50 px) per zoom level at the cursor; drag to pan. Screen positions for known coordinates are derived from the tiles on screen.
5. **Volume** — "Volume" button, click the outline, "Finish"; the result appears in the toolbar.
6. **3D model** — `/project/<name>/task/<id>/model`; wheel, left-drag, right-drag.
7. **Point cloud** — same route with `?source=pointcloud`; radios "Elevation", "Classification", "RGB"; "Measure elevation profile", two clicks, Enter.
8. **Analysis results** — "Results" toggle on a run; click a change region for its popup.

## Things found along the way

- The first API call of a freshly loaded page can fail with HTTP 400 right after sign-in, and the page then shows "Task not found". A reload fixes it. The recorder retries navigation.
- In the "Add task" dialog the *Existing dataset* picker renders blank until an option is chosen (the placeholder option does not display). The script moves to the *Upload new images* tab.
- The account used is a platform administrator and organization owner. No dedicated demo account exists; the scripts are read-only so that this is safe, and a demo account can be substituted in `.env.local`.
