# WebODM Frontend — Session Log

## Phase 1: Frappe + Vite + frappe-ui SPA Integration

### What was built
- Vue 3 SPA with Vue Router (createWebHistory), auth guard, Login page
- Vite dev server at `http://localhost:8081/assets/webodm_frontend/frontend/` proxying `/api` and `/private` → Frappe backend
- `FrappeUI` plugin with `socketio: false`
- `FrappeUIProvider` wrapping the app for toast infrastructure
- Dashboard page using frappe-ui components: `Button`, `Card`, `FeatherIcon`, `Badge`, `Dialog`, `FormControl`

### Key config details
- **vite.config.js**: `frappe-ui/vite` plugin, proxy `/api` and `/private` → `http://127.0.0.1:8080` with `changeOrigin: true` and `Host: webodm.local`, `optimizeDeps.exclude: ['frappe-ui']`, `optimizeDeps.include: ['feather-icons', 'debug']`, server port `8081`
- **Frappe backend**: webodm.local:8080 (Administrator/admin)
- **Router base**: `/assets/webodm_frontend/frontend/`
- **Login**: POST `/api/method/login` with `usr`/`pwd`, redirects via query param; CSRF token fetched after login from `/api/method/webodm_core.api.csrf.get_token` and stored in `window.csrf_token`
- **CSRF**: Sent as `X-Frappe-CSRF-Token` header on all POST/PUT/DELETE fetches. Fetched on app mount and after login.
- **Tailwind**: `presets: [frappeUIPreset]` + content scan of `node_modules/frappe-ui/src/**/*.{vue,ts,tsx}`

### CJS/ESM issues (solved)
- `feather-icons` (CJS) → added to `optimizeDeps.include`
- `debug` (transitive CJS dep) → added to `optimizeDeps.include`
- `highlight.js/lib/core` → handled by `frappe-ui/vite` plugin's `optimizeDeps.include`
- `~icons/lucide/*` virtual imports → handled by `frappe-ui/vite` plugin (custom resolver)

## Phase 2: Frappe DocTypes

### Created DocTypes
- **WebODM Project** (`webodm_project/`) — Core project entity
  - Fields: title (Data), description (Text), status (Select), processing_options (JSON)
  - Naming: field:title (name auto-generated from title)
- **WebODM Task** (`webodm_task/`) — Processing task within a project
  - Fields: project (Link→WebODM Project), title (Data), status (Select), progress (Percent), resolution (Float), processing_options (JSON), images (Table→WebODM Task Image), orthophoto/dsm/dtm/point_cloud (Attach)
  - Naming: hash (auto-generated)
- **WebODM Task Image** (`webodm_task_image/`) — Child table for task images
  - Fields: image (Attach), filename (Data), file_size (Int), latitude (Float, allow_null), longitude (Float, allow_null)
  - istable: 1 (child table)

### DocType file locations
- `/home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/<name>/`
- Each has: `__init__.py`, `<name>.json` (schema), `<name>.py` (controller)
- Note: all DocTypes live in `webodm_core`; `webodm_frontend` has no DocTypes.

### Infrastructure
- PostgreSQL: Docker container `webodm-db` (postgis/postgis:16-3.4) on port 5432
- DB: `webodm` (user: `webodm`, password: `webodm`)
- Redis: cache (13000), queue (11000)
- `common_site_config.json`: `root_login: postgres`, `root_password: postgres`
- `site_config.json`: 3 apps installed (`frappe`, `webodm_core`, `webodm_frontend`), `webserver_port: 8080`, `max_file_size: 10737418240` (10GB)

## Phase 3: Frontend Pages + Upload Pipeline + GPS Fix

### Pages built
- **Login.vue** — frappe-ui `Button`, `FormControl`, `Alert`, `FeatherIcon`; fetches CSRF after login
- **AppLayout.vue** — fixed sidebar (logo, nav link, logout), top header with page title, main content slot
- **Dashboard.vue** — project cards grid with status badge, date, edit (pencil → PUT), delete (trash → confirmation → delete linked tasks first → DELETE project). Uses `JSON.stringify()` + `encodeURIComponent()` for SQL injection safe filters.
- **MapView.vue** — Leaflet map, sidebar with task list (status badge, image count, progress bar), selected task shows Console/3D Model/Start Processing buttons. Fetches task details including `images` child table. **GPS markers** plotted with popups (thumbnail + filename). Filter: `lat===0 && lng===0` skipped (Frappe ORM defaults null Float to 0.0).
- **Upload modal** — plain Tailwind modal, file input, POSTs FormData to `/api/method/webodm_core.api.task.upload_images`.
- **Console.vue** — task detail page, status, progress, resolution, image count, simulated log, Refresh button.
- **ModelView.vue** — placeholder 3D viewer page.
- **NotFound.vue** — 404 page + catch-all route `/:pathMatch(.*)*`.

### Backend API (`webodm_core/api/`)
- **`task.py`**: `upload_images` — accepts multipart files + `project_id`, saves each as Frappe File doc, extracts GPS coordinates from EXIF via Pillow (before Frappe strips EXIF on save), creates WebODM Task with image child records.
- **`csrf.py`**: `get_token` — returns `frappe.sessions.get_csrf_token()`.

### GPS Extraction Details
- **Pillow 12 API**: `getexif()` returns `Exif` object; GPS sub-IFD accessed via `get_ifd(34853)` (not iterating top-level keys).
- **EXIF Stripping**: Frappe v16's `strip_exif_metadata_from_uploaded_images` system setting (default `1`) strips all EXIF during `File.save()`. Our API reads EXIF from raw bytes **before** saving, so extraction works. Stored files on disk have no EXIF — re-upload needed for old files to get GPS markers.
- **DB columns**: `latitude`/`longitude` are nullable Float (manually `ALTER COLUMN DROP NOT NULL, DROP DEFAULT` since `bench migrate` didn't alter existing cols).

### Phase 2 Extended DocTypes (added 2026-07-13)
All DocTypes below live in `webodm_core` (the `webodm_frontend` app has no DocTypes).
- **WebODM Project Tag** — child table for project tags
- **WebODM Processing Node** — NodeODM/MicMac/ODX/LGT node registry, hostname/port/engine/queue/max_images
- **WebODM Preset** — named processing option presets, system/user scope
- **WebODM Settings** — Single DocType, global config (basemap, limits, notifications)
- **WebODM Theme** — Light/Dark/System themes with name + type selector
- **WebODM Basemap** — TMS/WMS tile sources with URL, zoom, attribution
- **WebODM Plugin** — plugin registry with name/version/enabled/settings
- **WebODM Redirect** — cluster routing path/URL mappings
- **Dataset Config** — removed 2026-08-15 along with the `webodm_geospatial` Frappe app;
  task dataset metadata now lives on `WebODM Task` (epsg/wkt/extent fields) and the
  standalone `services/geospatial` service

### Theme Switcher (frontend)
- `composables/useTheme.js` — persists to localStorage, cycles Light→Dark→System, `dark` class on `<html>`, listens to `prefers-color-scheme`
- `tailwind.config.js` — `darkMode: 'class'`
- `AppLayout.vue` — theme toggle button in sidebar, dark mode Tailwind variants throughout
- `index.css` — dark mode CSS variables

### Thumbnail Limit
- MapView task card grid capped at 9 thumbnails + `+N` badge for performance

### Known Issues
- Existing task images (uploaded before GPS fix) have no EXIF in storage and `lat=0, lng=0`. Re-upload originals to get GPS markers.
- Frappe ORM converts `None` Float values to `0.0` on insert; frontend filters `lat===0 && lng===0` to compensate.

## Phase 3: Processing Pipeline

### NodeODM Setup
- **Docker container**: `webodm-nodeodm` (opendronemap/nodeodm:latest) on port 3000
- **Engine**: ODM 3.5.6 / NodeODM 2.2.4
- **Node registered**: `Local NodeODM` WebODM Processing Node in Frappe (127.0.0.1:3000)
- **Redis**: cache on 13000, queue on 11000 (must be running for RQ workers)

### Pipeline Flow
1. User uploads images → `upload_images` API creates WebODM Task (status=Pending)
2. Frontend "Start Processing" → `process_task` API → enqueues `process_task` on `long` queue
3. `process_pending_tasks` (scheduled cron `*/1 * * * *`) also enqueues any lingering Pending tasks
4. `process_task` job: reads images from Frappe Files → sends to NodeODM via `create_task` → stores `_node_task_id` in `processing_options` JSON → sets status=Running
5. `update_running_tasks` (cron `*/1 * * * *`) enqueues `poll_task` for each Running task
6. `poll_task`: queries NodeODM `/task/<uuid>/info` → updates progress → when code=30, downloads assets (orthophoto.tif, dsm.tif, dtm.tif, georeferenced_model.laz) → saves as Frappe Files → sets orthophoto/dsm/dtm/point_cloud fields → sets status=Completed

### Fixes Applied (2026-07-13)
- `node_client.py task_info()`: fixed URL from `task/<id>` to `task/<id>/info` (NodeODM 2.x API)
- `node_client.py download_asset()`: wrapped in try/except, raises `NodeODMError` on HTTP error
- `task_runner.py poll_task()`: handles `status` being a dict `{"code": N, "errorMessage": "..."}` in NodeODM 2.x
- `api/task.py process_task()`: fixed bytes decode before `frappe.parse_json`

### How to test
```bash
# Upload images + start processing via API
curl -c /tmp/pc.txt -X POST -H "Host: webodm.local" -F "usr=Administrator&pwd=admin" http://127.0.0.1:8080/api/method/login
CSRF=$(curl -b /tmp/pc.txt -s -H "Host: webodm.local" 'http://127.0.0.1:8080/api/method/webodm_core.api.csrf.get_token' | python3 -c "import json,sys;print(json.load(sys.stdin)['message'])")
TASK=$(curl -b /tmp/pc.txt -s -X POST -H "Host: webodm.local" -H "X-Frappe-CSRF-Token: $CSRF" -F "files=@/tmp/test.jpg" -F "project_id=<project_name>" http://127.0.0.1:8080/api/method/webodm_core.api.task.upload_images | python3 -c "import json,sys;print(json.load(sys.stdin)['message']['name'])")
curl -b /tmp/pc.txt -s -X POST -H "Host: webodm.local" -H "Content-Type: application/json" -H "X-Frappe-CSRF-Token: $CSRF" -d "{\"task_name\":\"$TASK\"}" http://127.0.0.1:8080/api/method/webodm_core.api.task.process_task
```

### Services to start (full stack)
```bash
docker start webodm-db  # PostgreSQL + PostGIS on 5432
docker start webodm-nodeodm  # NodeODM on 3000
redis-server --port 13000 --daemonize yes
redis-server --port 11000 --daemonize yes
# Frappe web server
cd /home/ridwan/workspace/g20-daas/frappe-bench/sites && gunicorn -b 127.0.0.1:8080 -w 4 frappe.app:application
# Vite dev server
cd /home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend && npm run dev
```

## How to start services

```bash
cd /home/ridwan/workspace/g20-daas/frappe-bench
source env/bin/activate

# PostgreSQL (Docker)
docker start webodm-db

# Redis
redis-server --port 13000 --daemonize yes
redis-server --port 11000 --daemonize yes

# Frappe web server (from sites/ dir)
cd sites && gunicorn -b 127.0.0.1:8080 -w 4 frappe.app:application

# Vite dev server (from frontend/ dir)
cd /home/ridwan/workspace/g20-daas/frappe-bench/apps/webodm_frontend/frontend
npm run dev
```

## Relevant Files

### Frontend
- `frappe-bench/apps/webodm_frontend/frontend/vite.config.js`
- `frappe-bench/apps/webodm_frontend/frontend/tailwind.config.js`
- `frappe-bench/apps/webodm_frontend/frontend/index.html`
- `frappe-bench/apps/webodm_frontend/frontend/src/main.js`
- `frappe-bench/apps/webodm_frontend/frontend/src/App.vue`
- `frappe-bench/apps/webodm_frontend/frontend/src/index.css`
- `frappe-bench/apps/webodm_frontend/frontend/src/pages/Login.vue`
- `frappe-bench/apps/webodm_frontend/frontend/src/pages/Dashboard.vue`
- `frappe-bench/apps/webodm_frontend/frontend/src/pages/MapView.vue`
- `frappe-bench/apps/webodm_frontend/frontend/src/pages/Console.vue`
- `frappe-bench/apps/webodm_frontend/frontend/src/pages/ModelView.vue`
- `frappe-bench/apps/webodm_frontend/frontend/src/pages/NotFound.vue`
- `frappe-bench/apps/webodm_frontend/frontend/src/components/AppLayout.vue`

### Frontend (theme)
- `frappe-bench/apps/webodm_frontend/frontend/src/composables/useTheme.js` — Theme switcher composable
- `frappe-bench/apps/webodm_frontend/frontend/src/index.css` — Dark mode CSS vars
- `frappe-bench/apps/webodm_frontend/frontend/tailwind.config.js` — `darkMode: 'class'`

### Backend
- `frappe-bench/apps/webodm_core/webodm_core/api/task.py` — `upload_images` + `_extract_gps`
- `frappe-bench/apps/webodm_core/webodm_core/api/csrf.py` — CSRF token endpoint
- `frappe-bench/sites/webodm.local/site_config.json`
- `frappe-bench/sites/common_site_config.json`

### DocTypes
All DocTypes live in `webodm_core`:
- `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_project/`
- `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_task/`
- `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_task_image/`
- `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_project_tag/`
- `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_preset/`
- `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_settings/`
- `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_theme/`
- `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_basemap/`
- `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_processing_node/`
- `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_plugin/`
- `frappe-bench/apps/webodm_core/webodm_core/webodm_core/doctype/webodm_redirect/`

## Phase 4: Docker Compose Stack Fixes (2026-08-27)

### Problems Fixed
1. **Frappe-init blocked startup** — `profiles: ["init"]` prevented dependent services from starting.
2. **Frappe Desk UI 404s** — `app_include_js/css` in `webodm_frontend/hooks.py` referenced non-existent desk assets.
3. **Empty presets table** — `seed_system_presets` patch wasn't running; manually executed to create 10 system presets.
4. **File upload HTTP 413** — `max_file_size` default too low; added `10737418240` (10GB) to `site_config.json`.
5. **CSP blocking external resources** — Caddy CSP didn't allow `https:` images or `https://unpkg.com` styles.
6. **Orthophoto tiles not rendering** — Three issues chained:
   - `geospatial_url` missing from `common_site_config.json` (added `http://geospatial:5000`).
   - `frappe_sites` volume not mounted into `geospatial` container (raster files inaccessible).
   - `FRAPPE_BENCH_ROOT` env var unset in runtime (resolved to `/`, breaking file paths).
7. **3D model viewer failing** — Frappe private files require session cookies, but Three.js `GLTFLoader` re-fetches URLs without cookies. CSP also blocked `blob:` in `connect-src` and `worker-src`.

### Fixes Applied
- **`docker-compose.yml`**: Removed `profiles: ["init"]`, added `FRAPPE_BENCH_ROOT: /workspace/frappe-bench` to `x-frappe-env-base`, added `frappe_sites` mount to `geospatial` service.
- **`webodm_frontend/hooks.py`**: Commented out `app_include_js/css`.
- **`site_config.json`**: Added `max_file_size: 10737418240`.
- **`common_site_config.json`**: Added `geospatial_url: http://geospatial:5000`.
- **`infra/caddy/Caddyfile`**: Added `blob:` to `connect-src` and `worker-src 'self' blob:` to CSP.
- **`ModelView.vue`**: Added `credentials: 'include'` to `fetch(modelUrl)`, then created blob URL (`URL.createObjectURL(blob)`) and passed it to `GLTFLoader.load()` so Three.js never re-fetches the private file.
- **`Dockerfile`**: Added `npm install && npm run build` step for the Vue SPA frontend (separate from Frappe desk `bench build --production`), so SPA assets are baked into the Docker image and survive container pruning.

### Verified After Fresh Container Restart
- Rebuilt `webodm-frappe:16.26.3` image with SPA assets baked in.
- Stopped all Frappe containers, started fresh from new image.
- 3D model viewer renders the photogrammetry model successfully (no CSP errors, canvas present).

### Files Changed
- `docker-compose.yml`
- `frappe-bench/apps/webodm_frontend/webodm_frontend/hooks.py`
- `frappe-bench/sites/webodm.local/site_config.json`
- `frappe-bench/sites/common_site_config.json`
- `infra/caddy/Caddyfile`
- `frappe-bench/apps/webodm_frontend/frontend/src/pages/ModelView.vue`
- `frappe-bench/apps/Dockerfile`

## Phase 5: Account Menu Restructure + Profile/Password Pages (2026-08-28)

### Changes
- Moved **Settings** and **Invoices** (renamed **Billing**) from primary nav into Account dropdown submenu.
- Added **Profile** page (`/account/profile`): displays organization info (read-only) and editable fields (full name, mobile number).
- Added **Change Password** page (`/account/password`): form with old/new/confirm password fields, validates client-side, calls Frappe's `update_password` API.
- Created custom backend API (`webodm_core/api/user.py`) for self-service profile updates (`get_profile`, `update_profile`) — bypasses Frappe's restrictive User doctype permissions safely.
- Created `DropdownMenuSeparator` and `DropdownMenuLabel` UI components for dropdown grouping.

### Files Changed
- `frappe-bench/apps/webodm_frontend/frontend/src/lib/nav.js`
- `frappe-bench/apps/webodm_frontend/frontend/src/components/AppLayout.vue`
- `frappe-bench/apps/webodm_frontend/frontend/src/components/ui/index.js`
- `frappe-bench/apps/webodm_frontend/frontend/src/components/ui/dropdown-menu/DropdownMenuSeparator.vue` (new)
- `frappe-bench/apps/webodm_frontend/frontend/src/components/ui/dropdown-menu/DropdownMenuLabel.vue` (new)
- `frappe-bench/apps/webodm_frontend/frontend/src/main.js`
- `frappe-bench/apps/webodm_frontend/frontend/src/lib/user.js` (new)
- `frappe-bench/apps/webodm_frontend/frontend/src/pages/Profile.vue` (new)
- `frappe-bench/apps/webodm_frontend/frontend/src/pages/ChangePassword.vue` (new)
- `frappe-bench/apps/webodm_core/webodm_core/api/user.py` (new)

## Phase 6: Frappe Desk on Admin Subdomain (2026-08-28)

### Problems Fixed
1. **CSS MIME type errors on `/desk`** — Frappe's cached `assets_json` in Redis referenced old asset hashes after image rebuild. The actual files on disk had new hashes.
2. **SocketIO "Invalid origin"** — Caddy didn't forward the original `Host` header to the SocketIO server. Frappe's auth compares `Host` with `Origin`.
3. **SocketIO "Invalid namespace" on `admin.webodm.local`** — The SocketIO namespace (`webodm.local`) didn't match the site name resolved from the request (`admin.webodm.local`).

### Fixes Applied
- **`infra/caddy/Caddyfile`**: 
  - Added `admin.{$SITE_DOMAIN}` block with root redirect to `/desk`.
  - Added `header_up Host webodm.local` on all reverse proxies to the web app so Frappe serves the correct site.
  - Added `header_up Host {host}` on SocketIO proxy so origin check passes.
  - Added `header_up X-Frappe-Site-Name webodm.local` on admin subdomain's SocketIO proxy so namespace validation passes.
- **`/etc/hosts`**: Added `admin.webodm.local` entry.
- **Symlinks**: Created symlinks from old CSS hashes → new hashes in `sites/assets/frappe/dist/css/` and `css-rtl/` to handle stale Redis cache.
- **`Dockerfile`**: Added symlink creation step so they persist in rebuilt images.
- **Cache clearing**: Cleared Frappe's `client_cache` (`assets_json`) and page caches.

### Files Changed
- `infra/caddy/Caddyfile`
- `frappe-bench/apps/Dockerfile`
- `frappe-bench/sites/assets/assets.json`
- `/etc/hosts`

## Deployment Documentation

- **`docs/deployment/production-deployment-guide.md`** — Complete deployment guide for Byteplus cloud VM with:
  - Multi-tenant S3 security (restricted mounts)
  - IP whitelisting for admin subdomain
  - Let's Encrypt TLS via Caddy
  - Gmail SMTP setup
  - AWS S3 integration with security isolation
  - Backup & disaster recovery procedures

## Phase 7: Monorepo Migration (2026-09-20)

### What changed
- `webodm_core` and `webodm_frontend` are no longer gitlinks. Their full histories were imported as tracked directories under `frappe-bench/apps/` via `git filter-repo --to-subdirectory-filter`, so per-file `git log`/`git blame` map to the original commits.
- `webodm-geospatial` was imported from the sibling repo `../webodm-geospatial` into `services/geospatial/` the same way. The standalone service is **not** a Frappe app, so it lives under `services/`, not `frappe-bench/apps/`.
- `frappe` is the only external app and is now declared in `.gitmodules` (upstream `frappe/frappe`, pinned at v16.26.3).
- CI (`build-and-push.yml`) no longer clones the private app repos: the Frappe app context is assembled from the monorepo checkout, and the geospatial image builds from `./services/geospatial`.
- `docker-compose.yml` geospatial build context is now `./services/geospatial`; the Procfile geospatial command runs from `../services/geospatial`.
- Added `services/geospatial/.dockerignore` so the local ~600 MB `venv/` never enters a build context.

### Gotchas
- filter-repo rewrote the app commit SHAs; the old GitHub repos (`webodm-core`, `webodm-frontend`, `webodm-geospatial`) hold the pre-migration histories and can be archived.
- `bench update`'s per-app `git pull` model no longer applies — update by pulling the monorepo and rebuilding the image.
- Run geospatial tests with `./venv/bin/python -m pytest` from `services/geospatial/`.
