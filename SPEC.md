# WebODM Rework — Specification

## 1. Overview

WebODM is a web application for processing aerial drone imagery using photogrammetry.
The current version (3.2.6) is built on Django 2.2 + React 16. This specification
describes a complete rework using Frappe v16 (backend) and Vue 3 (frontend), with a
dedicated FastAPI geospatial microservice.

### Goals

- Modernize the tech stack (Python 3.14, Frappe v16, Vue 3, Vite)
- Leverage Frappe's mature admin interface (Desk) for management
- Separate geospatial processing into a dedicated, scalable microservice
- Improve developer experience with hot-reload, TypeScript-ready Vue, and Frappe's DocType system
- Preserve full feature parity with the original WebODM

## 2. System Architecture

### 2.1 Container Overview

```
┌─────────────────────────────────────────────────────────────┐
│                          nginx                               │
│               reverse proxy / static files                   │
└────┬──────────────┬──────────────────┬──────────────────────┘
     │              │                  │
┌────▼────────┐ ┌──▼───────────┐ ┌────▼────────────────┐
│  Frappe Web │ │  Vue 3 SPA  │ │  Geospatial Service  │
│  (gunicorn) │ │  (Frappe    │ │  (FastAPI + GDAL)    │
│             │ │   Desk page)│ │                       │
└────┬────────┘ └──────────────┘ └────┬─────────────────┘
     │                                │
┌────▼────────┐              ┌───────▼─────────────────┐
│  PostgreSQL │              │  Shared File Volume     │
│  + PostGIS  │              │  (GeoTIFFs, LAS, GLB)   │
└────┬────────┘              └─────────────────────────┘
     │
┌────▼────────┐
│   Redis     │
│ (RQ/Queue)  │
└─────────────┘
```

### 2.2 Service Responsibilities

| Service | Role | Tech |
|---|---|---|
| **Frappe Web** | REST API, DocType CRUD, auth, admin desk, business logic | Python 3.14, Frappe v16, Gunicorn |
| **Frappe Worker** | Background task processing (RQ) | Python 3.14, Frappe RQ |
| **Frappe Scheduler** | Periodic task dispatch | Python 3.14, Frappe RQ Beat |
| **Frappe SocketIO** | Real-time updates to desk | Node.js, Socket.IO |
| **Vue SPA** | Map view, 3D model viewer, task console | Vue 3, frappe-ui, Leaflet, Three.js |
| **Geospatial** | Raster tiles, COG processing, point cloud export | FastAPI, rio-tiler, GDAL, PDAL |
| **PostgreSQL + PostGIS** | All persistent data + spatial queries | PostgreSQL 16 + PostGIS 3.4 |
| **Redis** | RQ queue, cache, locks, pub/sub | Redis 7 |

## 3. Data Model (DocTypes)

### 3.1 webodm_core DocTypes

#### WebODM Project
```
- project_name: Data         (required)
- description: Text Editor   (optional)
- owner: Link → User         (auto-set)
- tags: Table (Tag)
- public: Check
- public_edit: Check
- public_id: Data            (auto-generated UUID)
- deleting: Check            (soft delete flag)
```

#### WebODM Task
```
Core DocType — represents a photogrammetry processing job.

- task_name: Data
- project: Link → WebODM Project (required)
- status: Select
  [queued, running, failed, completed, canceled]
- processing_node: Link → WebODM Processing Node
- auto_processing_node: Check
- options: JSON                (processing parameters)
- available_assets: Table (Task Asset)
- orthophoto_extent: JSON      (GeoJSON polygon)
- dsm_extent: JSON
- dtm_extent: JSON
- last_error: Text Editor
- created_at: Datetime
- pending_action: Int          (CANCEL=1, REMOVE=2, RESTART=3, etc.)
- import_url: Data
- images_count: Int
- partial: Check
- potree_scene: JSON
- epsg: Data
- wkt: Text
- tags: Table (Tag)
- crop: JSON                   (GeoJSON polygon)
- media: JSON
```

#### Task Asset (Child Table of WebODM Task)
```
- asset_type: Data             (orthophoto.tif, dsm.tif, textured_model.glb, etc.)
- file_path: Data
- file_size: Float
- mime_type: Data
```

#### Task Media (Child Table of WebODM Task)
```
- filename: Data
- file_type: Data              (image, video, panorama)
- file_size: Float
- geolocation: JSON            (GeoJSON Point)
```

#### WebODM Preset
```
- preset_name: Data            (required)
- owner: Link → User
- options: JSON                (processing node options)
- system: Check                (system-wide preset)
```

#### WebODM Processing Node
```
- node_name: Data
- hostname: Data
- port: Int
- api_version: Data
- token: Data
- engine_version: Data
- label: Data
- engine: Data
- queue_count: Int
- max_images: Int
- available_options: JSON
- last_refreshed: Datetime
```

#### WebODM Settings (Single)
```
- app_name: Data
- app_logo: Attach Image
- organization_name: Data
- organization_website: Data
- theme: Link → WebODM Theme
```

#### WebODM Theme
```
- theme_name: Data
- primary_color: Color
- secondary_color: Color
- border_color: Color
- highlight_color: Color
- dialog_warning_color: Color
- failed_color: Color
- success_color: Color
- css: Code
- html_before_header: HTML
- html_after_header: HTML
- html_after_body: HTML
- html_footer: HTML
```

#### WebODM Basemap
```
- basemap_name: Data
- type: Select [TMS, WMS]
- url: Data
- label: Data
- attribution: Data
- maxzoom: Int
- minzoom: Int
- subdomains: Data
- layers: Data
- styles: Data
- format: Data
- is_default: Check
```

### 3.2 webodm_geospatial DocTypes

#### Dataset Config
```
- task: Link → WebODM Task
- dataset_type: Select [orthophoto, dsm, dtm]
- file_path: Data
- crs: Data
- bounds: JSON                 (GeoJSON)
- resolution: Float
- band_count: Int
- no_data_value: Float
```

## 4. API Specification

### 4.1 Frappe Standard REST API
Frappe provides built-in CRUD for all DocTypes:

```
GET    /api/resource/WebODM%20Project          — List projects
POST   /api/resource/WebODM%20Project          — Create project
GET    /api/resource/WebODM%20Project/{name}   — Get project
PUT    /api/resource/WebODM%20Project/{name}   — Update project
DELETE /api/resource/WebODM%20Project/{name}   — Delete project
```

Same pattern for: `WebODM Task`, `WebODM Processing Node`, `WebODM Preset`, etc.

### 4.2 Custom API Endpoints (whitelisted methods)

#### Task Operations
| Method | Endpoint | Status | Purpose |
|---|---|---|---|
| POST | `webodm_core.api.task.upload_images` | ✅ | Upload images (+ EXIF GPS extraction, byte-preserving save) |
| POST | `webodm_core.api.task.process_task` | ✅ | Trigger processing (enqueue) |
| POST | `webodm_core.api.task.cancel_task` | ✅ | Cancel running/pending task |
| POST | `webodm_core.api.task.get_task_console` | ✅ | Incremental NodeODM console output |
| POST | `webodm_core.api.task.duplicate` | ⬜ | Duplicate task & assets |
| POST | `webodm_core.api.task.restart` | ⬜ | Reprocess task |
| GET | `webodm_core.api.task.download_asset` | ⬜ | Download individual asset |
| GET | `webodm_core.api.task.download_backup` | ⬜ | Download full task backup |
| POST | `webodm_core.api.task.import` | ⬜ | Import task from ZIP |
| GET | `webodm_core.api.task.thumbnail` | ⬜ | Get task thumbnail |

#### Processing Node Operations
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `webodm_core.api.node.list` | List available nodes |
| GET | `webodm_core.api.node.find_best` | Find best available node |
| GET | `webodm_core.api.node.options` | Aggregated processing options |

#### Tile Operations (proxied to Geospatial Service)
Same-origin, session-authed proxy so Leaflet `<img>` requests carry the session
cookie and the geospatial service needs no Frappe auth/storage knowledge.

| Method | Endpoint | Status | Purpose |
|---|---|---|---|
| GET | `webodm_core.api.tiles.serve` | ✅ | Proxy XYZ tile (task_name, dataset, z/x/y) |
| GET | `webodm_core.api.tiles.info` | ✅ | Proxy raster info (bounds, zoom, bands) |
| GET | `webodm_core.api.tiles.tilejson` | ⬜ | Proxy TileJSON requests |

#### Auth Operations
| Method | Endpoint | Purpose |
|---|---|---|
| POST | `webodm_core.api.auth.jwt_token` | Issue JWT for tile access |
| POST | `webodm_core.api.auth.login` | Login (delegates to Frappe) |

### 4.3 Geospatial Service API

The service is stateless and path-driven: callers pass an absolute raster path
(`?path=`), not a dataset id. See `webodm-geospatial/README.md` for details.

| Method | Endpoint | Status | Purpose |
|---|---|---|---|
| GET | `/health` | ✅ | Health check |
| GET | `/tiles/info?path=` | ✅ | Bounds (4326), zoom range, band stats |
| GET | `/tiles/tile/{z}/{x}/{y}.png?path=&kind=` | ✅ | Raster tile (`kind`=orthophoto\|dsm\|dtm) |
| POST | `/export/cogify` | ✅ | Convert raster → COG, return epsg/wkt/extent |
| POST | `/export/raster` | 🚧 | Export raster as GeoTIFF/PNG/KMZ |
| POST | `/export/hillshade` | 🚧 | Generate hillshade from DEM |
| POST | `/export/colormap` | 🚧 | Apply custom colormap |
| POST | `/export/formula` | 🚧 | Apply band formula (NDVI) |
| POST | `/pointcloud/export` | 🚧 | Export point cloud |
| POST | `/pointcloud/to-potree` | 🚧 | Convert to Potree format |

## 5. Task Processing Pipeline

### 5.1 Flow

```
[User] → upload_images → Create Task (Pending)
  → extract EXIF GPS to Task Image rows; save original image bytes intact*
  → Scheduler cron (1 min): process_pending_tasks
    → Pick processing node
    → Upload images via NodeODM API
    → Status: Running
  → Scheduler cron (1 min): update_running_tasks → poll_task
    → On COMPLETED:
      → Download all.zip → extract assets (orthophoto/dsm/dtm/pc/model)
      → For each raster: /export/cogify → persist epsg/wkt/*_extent
      → Status: Completed
    → On FAILED:
      → Log error → Status: Failed
```

\* **EXIF GPS must survive upload.** ODM georeferences from per-image EXIF GPS;
if it is stripped the reconstruction collapses to a tiny local model (tens of
pixels). Frappe's `strip_exif_metadata_from_uploaded_images` setting is disabled
and the uploader rewrites original bytes if altered. See
`TROUBLESHOOTING-orthophoto-exif.md`.

> Note: current dispatch/poll cadence is a 1-minute scheduler cron
> (`hooks.py`), not the 5s RQ target in §5.3 — tightening this is tracked in TODO.

### 5.2 Pending Actions
```
CANCEL  (1) — Cancel processing (sends cancel to NodeODM)
REMOVE  (2) — Delete task + free disk space
RESTART (3) — Reprocess with same options
IMPORT  (4) — Import task from external ZIP
COMPACT (5) — Remove intermediate files, keep only outputs
```

### 5.3 Periodic Jobs

| Job | Interval | Purpose |
|---|---|---|
| `process_pending_tasks` | 5s | Dispatch queued tasks |
| `update_nodes_info` | 30s | Refresh processing node status |
| `cleanup_projects` | 60s | Remove projects marked for deletion |
| `cleanup_tasks` | 3600s | Remove expired partial tasks |
| `cleanup_tmp_directory` | 3600s | Clean temp files > 24h |
| `cleanup_cache_directory` | 21600s | Clean cached assets > 30d |
| `check_quotas` | 3600s | Enforce disk quotas |

## 6. Plugin System

Frappe's hooks system replaces the original WebODM plugin mechanism.

| Plugin | Migration Strategy |
|---|---|
| tasknotification | Frappe notifications + email alerts |
| contours | Geospatial service endpoint + Vue layer |
| lightning | Leaflet layer control in Vue |
| dronedb | Frappe DocType + API integration |
| cesiumion | Frappe external service integration |
| measure | Vue component + Leaflet Draw |
| shortlinks | Frappe web page route |
| fullscreen | Vue component toggle |
| align-service | Frappe background job |
| snapshot | Vue + canvas/leaflet print |
| split-merge | Task processing pipeline extension |
| potree-annotations | Vue 3D component integration |

## 7. Frontend Pages

### 7.1 Dashboard (`/webodm`)
- Project list with search, sort, pagination
- Create/edit project dialogs
- User avatar + profile menu

### 7.2 MapView (`/webodm/project/{id}`)
- Leaflet map with tile overlays (orthophoto, DSM, DTM)
- Task list sidebar with status badges
- Upload images panel (drag-drop, progress bars)
- Edit task dialog (processing options, presets)
- Export asset dialog
- Share popup (public link, iframe embed)
- Permissions panel
- GCP upload popup
- Media gallery
- Crop polygon editor
- Layer controls (colormap, hillshade, HSV, opacity)

### 7.3 ModelView (`/webodm/project/{id}/task/{taskId}/model`)
- Three.js textured model viewer (ODM GLB, Draco-compressed; `model.zip` glTF fallback)
- Models are re-oriented from ODM's Z-up to Y-up and centred; camera auto-frames the whole model
- Toolbar: Rotate / Pan / Zoom mouse modes, zoom in/out, reset view, preset views (isometric, top, north, east), ground grid, fullscreen, help
- Mouse, touch and keyboard navigation (arrows/WASD pan, +/- zoom, R reset, 1-4 presets, G grid, F fullscreen, ? help); double-click focuses the orbit on the clicked point
- Large models: streamed download with progress, textures decoded two at a time and downsampled to a per-device memory budget, render-on-demand loop, adaptive pixel ratio
- States: loading (phase + progress), processing (polls until the model exists), no model, failed task, not found, download error with retry, WebGL unavailable
- Dataset switcher between the project's tasks that have a model; download and console links
- Measurement tools and camera view save/restore are not implemented yet

### 7.4 Console (`/webodm/project/{id}/task/{taskId}/console`)
- Real-time task log streaming via SocketIO
- Scrollable ANSI-colored output
- Search/filter log lines

## 8. Authentication & Permissions

### Built-in Frappe Auth
- Session auth (browser)
- API key / secret (programmatic)
- Role-based permissions per DocType

### Custom Auth
- JWT tokens for tile URL access (short-lived, signed)
- Public sharing via `public_id` token

### Roles (Frappe Role system)
| Role | Permissions |
|---|---|
| **Administrator** | Full access to all projects, tasks, nodes, settings |
| **Project Owner** | Full access to owned projects + tasks |
| **Project Viewer** | Read-only access to shared projects |
| **Task Manager** | Create/edit process tasks (assigned role) |

## 9. Security Considerations

- All API endpoints require authentication (except public share pages)
- JWT tokens for tile URLs are signed and time-limited (default 1h)
- File uploads validated for type and size
- Processing node tokens stored encrypted
- Frappe's built-in CSRF protection for all POST requests
- Rate limiting on login endpoint (Frappe built-in)
- SQL injection protection via Frappe ORM / parameterized queries
- File path traversal protection in asset download endpoints

## 10. Performance Requirements

| Metric | Target |
|---|---|
| Map tile load time (first) | < 500ms for 256x256 tile |
| Concurrent task processing | 10+ per processing node |
| Upload throughput | 100 Mbps sustained |
| Page load (Dashboard) | < 2s (with 100 projects) |
| Concurrent users | 50+ simultaneous |
| Raster export (1GB GeoTIFF) | < 60s |

## 11. Browser Support

- Chrome/Edge (latest 2 versions)
- Firefox (latest 2 versions)
- Safari (latest 2 versions)
- WebODM MapView: WebGL required for 3D model viewer
