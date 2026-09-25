# WebODM Rework — Task Tracker

## Phase 1: Foundation ✅

- [x] Install prerequisites (python3-pip, venv, redis, dev libs)
- [x] Create workspace directories (frappe-webodm, webodm-geospatial)
- [x] Install Frappe Bench (v5.31.0) + uv
- [x] Initialize Bench with Frappe v16.26.3
- [x] Start PostgreSQL 16 + PostGIS 3.4 Docker container
- [x] Enable PostGIS extension
- [x] Install psql client
- [x] Create Frappe site `webodm.local` with PostgreSQL
- [x] Create custom apps: `webodm_core`, `webodm_frontend`, `webodm_geospatial`
- [x] Install all 3 apps on the site
- [x] Scaffold Vue 3 + Vite + frappe-ui frontend
- [x] Configure Leaflet + @vue-leaflet/vue-leaflet (Leaflet via CDN instead)
- [x] Create page stubs: Dashboard, MapView, ModelView, Console
- [x] Build frontend to correct Frappe asset path
- [x] Create Procfile (web on port 8080)
- [x] Verify Frappe login at http://webodm.local:8080
- [x] Create geospatial service skeleton (FastAPI)
- [x] Verify geospatial service health endpoint
- [x] Set up Docker Compose (full stack)
- [x] Create documentation: README, SPEC, TRD, geospatial README
- [x] Create TODO.md
- [x] Fix Vite proxy to forward `/private` for file serving
- [x] Add `/private` proxy to vite.config.js

## Phase 2: Backend — DocTypes ✅ (Core), ⬜ (Extended)

### WebODM Project ✅
- [x] Create `WebODM Project` DocType
- [x] Define fields: title, description, status, processing_options
- [x] Autoname from title field
- [x] Basic DocType permissions

### WebODM Task ✅
- [x] Create `WebODM Task` DocType
- [x] Define fields: project (Link→Project), title, status, progress, resolution, processing_options (JSON)
- [x] Create `WebODM Task Image` child table (image, filename, file_size, latitude, longitude)
- [x] Add output asset fields: orthophoto, dsm, dtm, point_cloud (Attach)
- [x] Autoname via hash
- [x] Add status options: Pending, Running, Completed, Failed, Canceled

### WebODM Task Image ✅
- [x] Create child table with image, filename, file_size
- [x] Add nullable latitude/longitude Float fields for GPS
- [x] Allow null values for GPS fields

### Extended DocTypes ✅
- [x] Create Tag child table for Project
- [x] Create `WebODM Processing Node` DocType (node_name, hostname, port, api_version, token, engine)
- [x] Create `WebODM Preset` DocType (preset_name, owner, options JSON)
- [x] Create `WebODM Settings` (Single DocType)
- [x] Create `WebODM Theme` DocType (light/dark/system selector)
- [x] Create `WebODM Basemap` DocType (TMS/WMS support)
- [x] Create `WebODM Plugin` DocType
- [x] Create `WebODM Redirect` DocType
- [x] Create `Dataset Config` DocType (webodm_geospatial)

## Phase 3: Backend — API & Business Logic ✅ (Core), ⬜ (Extended)

### Core API ✅
- [x] Implement task upload_images endpoint (multipart + GPS extraction)
- [x] Implement CSRF token endpoint
- [x] GPS extraction from EXIF via Pillow (Pillow 12: get_ifd(34853))
- [x] Preserve original image bytes on upload so EXIF GPS survives for ODM
      (`_save_task_image_file`; see TROUBLESHOOTING-orthophoto-exif.md)
- [x] Implement real task console endpoint (`get_task_console`, incremental
      NodeODM output polling)

### Extended API ✅ (partial), ⬜ (rest)
- [x] Implement task process/trigger endpoint (`process_task`)
- [x] Implement task cancel endpoint (`cancel_task`)
- [ ] Implement task duplicate endpoint
- [ ] Implement task restart endpoint
- [ ] Implement task download_asset endpoint
- [ ] Implement task download_backup endpoint
- [ ] Implement task import (ZIP) endpoint
- [ ] Implement task thumbnail endpoint
- [ ] Implement node list/find_best endpoint
- [ ] Implement aggregated options endpoint
- [ ] Implement node add/remove admin UI
- [ ] Implement JWT token endpoint for tiles
- [ ] Implement public sharing auth flow

### Task Processing Pipeline ✅ (core), ⬜ (extended)
- [x] Implement `process_pending_tasks` scheduler job (cron, 1 min interval)
- [x] Implement `update_running_tasks` polling job
- [x] Implement NodeODM communication (`node_client.py`)
- [x] Implement image upload to processing node
- [x] Implement task status polling
- [x] Implement all.zip download and extraction
- [x] Implement asset registration (orthophoto/dsm/dtm/point_cloud/model → File)
- [x] Convert output rasters to COG + persist epsg/wkt/extent (via geospatial svc)
- [ ] Drop poll interval toward 5s (currently 1 min cron)
- [ ] Implement pending action processing (CANCEL, REMOVE, RESTART, etc.)
- [ ] Implement task locking with Redis

### Periodic Jobs ⬜
- [ ] Implement `update_nodes_info` (30s)
- [ ] Implement `cleanup_projects` (60s)
- [ ] Implement `cleanup_tasks` (3600s)
- [ ] Implement `cleanup_tmp_directory` (3600s)
- [ ] Implement `cleanup_cache_directory` (21600s)
- [ ] Implement `check_quotas` (3600s)

### User Profile & Quota ⬜
- [ ] Extend Frappe User with profile fields (quota, cluster_id)
- [ ] Implement quota enforcement middleware
- [ ] Implement admin user management endpoints

### Permissions ⬜
- [ ] Map django-guardian perms to Frappe roles
- [ ] Create roles: Project Owner, Project Viewer, Task Manager
- [ ] Implement permission hooks per DocType

## Phase 4: Geospatial Service ✅ (tiles + COG), ⬜ (exports)

### Tile Serving ✅ (core), ⬜ (extended)
- [x] Implement raster tile endpoint (rio-tiler, `/tiles/tile/{z}/{x}/{y}.png`)
- [x] Implement raster info endpoint (`/tiles/info`: bounds, zoom, band stats)
- [x] Terrain colormap for single-band DSM/DTM; RGB(A) for orthophoto
- [x] Transparent PNG for out-of-bounds tiles
- [x] Implement COG conversion endpoint (`/export/cogify`) + validation (`is_cog`)
- [x] Frappe-side session-authed tile proxy (`api/tiles.py`)
- [ ] Implement TileJSON endpoint
- [ ] Support colormap selection, hillshade, HSV
- [ ] Implement tile caching (Redis)

### Raster Export ⬜
- [ ] Implement GeoTIFF export
- [ ] Implement PNG/JPEG export with colormap
- [ ] Implement KMZ export
- [ ] Implement MBTiles export

### Point Cloud ⬜
- [ ] Implement LAS/LAZ/Ply export
- [ ] Implement Potree format conversion
- [ ] Implement point cloud bounds/crs extraction

### Raster Processing ⬜
- [ ] Implement hillshade generation
- [ ] Implement custom colormap application
- [ ] Implement band formula computation (NDVI, etc.)
- [ ] Implement HSV blending

### Storage ✅ (object storage, 2026-09-25)
- [x] Object storage as canonical store (`webodm_core/storage`, S3/MinIO, org-namespaced keys)
- [x] Host serving cache with idle/LRU eviction; cache-first, S3-second serving
- [x] Geospatial reads `s3://` rasters via `/vsis3/`; COG conversion S3 → S3
- [x] Backfill of legacy host-only blobs
- [ ] Presigned download URLs (signer identity and TTL exist; not wired to the UI)

### On-demand compute ✅ (2026-09-25) — see `docs/on-demand-processing/`
- [x] Provisioner service with one provider interface; AWS EC2 (CPU) implemented
- [x] Provisioning task state, readiness polling, release on terminal state, sweep, caps
- [x] Static node fallback unchanged
- [ ] vast.ai GPU provider (next phase: new provider module + config only)
- [ ] Warm pool / autoscaling, spot pricing (explicitly later)

## Phase 5: Vue Frontend ✅ (Core), ⬜ (Extended)

### Dashboard Page ✅
- [x] Full project list with cards
- [x] Create/edit project dialog
- [x] Project deletion with confirmation (deletes linked tasks first)
- [x] Status badge, date display
- [x] SQL injection safe filters (JSON.stringify + encodeURIComponent)

### Dashboard Extended ⬜
- [ ] Search, sort, pagination
- [ ] User avatar + profile menu
- [ ] Empty state when no projects

### MapView Page ✅
- [x] Leaflet map with tile overlays (OpenStreetMap)
- [x] Task list sidebar with status badges
- [x] Image markers plotted from GPS coordinates
- [x] Marker popups with thumbnail + filename
- [x] Upload images panel (file input, CSRF-guarded POST)
- [x] Console / 3D Model navigation buttons
- [x] Start Processing button (PUT status update)
- [x] GPS marker filtering (skip 0,0 for no-GPS images)
- [x] Zoom To Fit button

### MapView Extended ✅ (partial), ⬜ (rest)
- [ ] Upload panel: drag-drop, chunked, progress bar
- [ ] Edit task dialog (processing options, presets)
- [ ] Import task panel (ZIP, external URL)
- [ ] Export asset dialog (format selection)
- [ ] Share popup (public link, iframe embed)
- [ ] Permissions panel (user/group management)
- [ ] GCP upload popup
- [ ] Image/PDF popup viewers
- [x] Thumbnail grid (capped at 9 + "+N" badge)
- [x] Raster tile overlays (orthophoto/DSM/DTM) via Frappe tile proxy
- [x] Layers panel with per-dataset visibility toggle + fit-to-extent
- [ ] Crop polygon editor
- [ ] Layer controls (opacity, reorder)
- [ ] Colormap selector
- [ ] Hillshade toggle
- [ ] HSV blending controls
- [ ] Band formula input
- [ ] Basemap switcher
- [ ] Unit selector (metric/imperial)
- [ ] Progress bars (upload, processing, resize) — basic done
- [ ] Histogram display
- [ ] Error message display

### ModelView Page ✅ (textured model viewer), ⬜ (extended)
- [x] Placeholder page
- [x] GLTF/GLB textured model viewer (Three.js + Draco; Z-up correction, auto-framing)
- [x] Toolbar: Rotate / Pan / Zoom modes, zoom, reset, preset views, grid, help
- [x] Mouse, touch and keyboard navigation; double-click to focus
- [x] Loading / processing / empty / error / WebGL-unavailable states
- [x] Large-model handling (streamed download, texture memory budget, render on demand)
- [x] Fullscreen toggle
- [x] Asset download button
- [x] Dataset switcher (other tasks in the project with a model)
- [ ] Point cloud (LAZ) viewer
- [ ] Measurement tools (distance, area, volume)
- [ ] Camera view save/restore
- [ ] Unit selector
- [ ] Share button

### Console Page ✅ (real logs), ⬜ (extended)
- [x] Task detail display (status, progress, resolution, image count)
- [x] Real NodeODM console output (incremental polling via `get_task_console`)
- [x] Refresh button (re-reads from line 0)
- [x] Auto-scroll to bottom (stick-to-bottom)
- [ ] Real-time log streaming via SocketIO (currently polling)
- [ ] ANSI-colored output rendering
- [ ] Search/filter log lines
- [ ] Copy to clipboard

### Common Components
- [x] Basic confirm dialog (Dashboard delete)
- [x] Basic upload modal (MapView)
- [x] Toast notifications (frappe-ui)
- [ ] Paginator component
- [ ] FileUploader with progress
- [ ] Tags field component
- [ ] UnitSelector component
- [ ] AssetDownloadButton component
- [ ] ErrorMessage component
- [ ] FormDialog component (shared)
- [ ] ConfirmDialog component (shared)
- [ ] SwitchModeButton (2D/3D toggle)

### Composables ⬜
- [ ] `useTask()` — task CRUD + state
- [ ] `useProject()` — project CRUD + state
- [ ] `useGeospatial()` — tile loading + metadata
- [ ] `useWebSocket()` — real-time updates
- [ ] `usePermissions()` — role/permission checks
- [ ] `useFileUpload()` — chunked upload with progress

### Frontend Infrastructure
- [x] Router configuration with auth guard
- [x] 404 catch-all route
- [x] App layout with sidebar + header
- [x] Login page with CSRF handling
- [x] Loading bar on route change
- [x] CSRF token management (window.csrf_token)
- [ ] Frappe API client utility
- [ ] Error handling middleware
- [ ] Responsive layout
- [x] Dark mode support (light/dark/system switcher)

## Phase 6: Plugin System ⬜

- [ ] Map original WebODM plugin hooks to Frappe hooks
- [ ] Create Plugin DocType for enable/disable
- [ ] Implement plugin JS/CSS injection via hooks
- [ ] Implement plugin API endpoints
- [ ] Implement plugin signals (task_failed, task_duplicated, etc.)
- [ ] Plugin upload/install from ZIP
- [ ] Plugin admin management UI

## Phase 7: Core Plugins ⬜

- [ ] **tasknotification** — email/Slack notifications on task events
- [ ] **contours** — contour line generation overlay
- [ ] **lightning** — Leaflet layer controls
- [ ] **dronedb** — drone database integration
- [ ] **cesiumion** — Cesium Ion integration
- [ ] **measure** — Leaflet measurement tools
- [ ] **shortlinks** — short URL generation
- [ ] **fullscreen** — fullscreen map toggle
- [ ] **align-service** — GCP alignment service
- [ ] **snapshot** — map screenshot export
- [ ] **split-merge** — task split/merge utilities
- [ ] **potree-annotations** — 3D annotations
- [ ] **rollify-thumbnails** — 360 thumbnail generation
- [ ] **minesviewer** — mining-specific analytics
- [ ] **zoom-to-layer** — auto-zoom to active layer
- [ ] **auto-exposure** — image auto-exposure correction

## Phase 8: Deployment & DevOps ⬜

### Docker
- [ ] Verify full Docker Compose stack
- [ ] Add SSL support (Let's Encrypt)
- [ ] Add NodeODM processing node compose profile
- [ ] Resource limits for worker containers
- [ ] Health check endpoints for all services

### Nginx
- [ ] Reverse proxy configuration
- [ ] Static file serving (Frappe assets)
- [ ] TLS termination
- [ ] Rate limiting
- [ ] Large file upload settings

### Monitoring
- [ ] Prometheus metrics endpoint (geospatial service)
- [ ] Frappe logging setup
- [ ] RQ job monitoring dashboard
- [ ] PostgreSQL query monitoring

### CI/CD
- [ ] GitHub Actions for frontend build
- [ ] GitHub Actions for backend tests
- [ ] Docker image build and push
- [ ] Automated migration on deploy

### Documentation
- [x] SPEC / TRD / README / geospatial README (kept in sync with implementation)
- [x] Troubleshooting: tiny orthophoto / EXIF GPS (TROUBLESHOOTING-orthophoto-exif.md)
- [ ] API reference (OpenAPI)
- [ ] Admin guide
- [ ] User guide
- [ ] Developer contribution guide
- [ ] Translation/i18n setup

## Legend

| Symbol | Meaning |
|---|---|
| ✅ | Complete |
| 🔄 | In progress |
| ⬜ | Not started |
