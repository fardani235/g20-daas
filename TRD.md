# WebODM Rework — Technical Requirements Document

## 1. System Requirements

### 1.1 Development Environment

| Requirement | Version | Notes |
|---|---|---|
| Python | >= 3.14 | Frappe v16 requires 3.14+ |
| Node.js | >= 20 | Vite 6 requires Node 20+ |
| npm | >= 10 | |
| PostgreSQL | >= 16 | With PostGIS 3.4 extension |
| Redis | >= 7 | Cache + RQ queue |
| GDAL | >= 3.8 | System package for raster operations |
| Docker | >= 24 | For containerized deployment |
| Docker Compose | >= 2.24 | |

### 1.2 Production Environment

| Component | Specification |
|---|---|
| CPU | 4+ cores (8+ recommended for heavy processing) |
| RAM | 8 GB minimum, 16 GB+ recommended |
| Disk | 100 GB+ SSD (depends on imagery volume) |
| Network | 100 Mbps+ internet connection |
| OS | Ubuntu 24.04 LTS (recommended) |

## 2. Dependencies

### 2.1 Python Dependencies (Frappe)

Managed by `bench` and `uv`. Core Frappe framework pulls:
- Flask (WSGI framework underneath Frappe)
- SQLAlchemy / psycopg2 (PostgreSQL driver)
- RQ (task queue)
- Redis client
- Jinja2 (templating)
- Werkzeug (WSGI utilities)
- cryptography, pyJWT (auth)

### 2.2 Python Dependencies (Geospatial Service)

Defined in `services/geospatial/requirements.txt` (min versions). Tested with the
installed versions shown, on Python 3.12:

| Package | Min Version | Tested | Purpose |
|---|---|---|---|
| fastapi | 0.115.0 | | Web framework |
| uvicorn[standard] | 0.34.0 | | ASGI server |
| pydantic | 2.10.0 | | Request/response models |
| rasterio | 1.4.0 | 1.5.0 | GeoTIFF I/O |
| rio-tiler | 6.0.0 | 9.4.0 | Raster tile server |
| rio-cogeo | 5.0.0 | 7.0.2 | COG creation/validation |
| numpy | 2.0.0 | 2.5.1 | Numerical computation |
| shapely | 2.0.0 | | Geometry operations |
| python-multipart | 0.0.12 | | File upload support |
| redis | 5.2.0 | | Cache layer |

### 2.3 System Dependencies (Geospatial Service)

GDAL ships bundled inside the rasterio / rio-tiler / rio-cogeo manylinux wheels,
so **no system GDAL install is required** for tile serving and COG conversion.
PDAL is only needed for the (still-stubbed) point cloud endpoints.

| Package | Purpose | Required now? |
|---|---|---|
| gdal-bin / libgdal-dev | System GDAL | No — bundled in wheels |
| pdal / libpdal-dev | Point cloud (LAS/LAZ) processing | Only for Phase 4 point cloud |

### 2.4 JavaScript Dependencies (Frontend)

Defined in `webodm_frontend/frontend/package.json`:

| Package | Version | Purpose |
|---|---|---|
| vue | ^3.5.0 | UI framework |
| vue-router | ^4.5.0 | Client-side routing |
| frappe-ui | ^0.1.270 | UI component library (TailwindCSS) |
| leaflet | ^1.9.4 | Interactive maps |
| @vue-leaflet/vue-leaflet | ^0.10.1 | Vue 3 Leaflet integration |
| vite | ^6.3.0 | Bundler/dev server |
| @vitejs/plugin-vue | ^5.2.0 | Vue 3 Vite plugin |
| tailwindcss | ^3.4.0 | Utility CSS framework |
| unplugin-icons | ^23.0.1 | Icon auto-import |
| @iconify-json/lucide | ^1.2.116 | Lucide icon set |

**Additional planned (Phase 5):**
| Package | Purpose |
|---|---|
| three.js | 3D model rendering (Potree) |
| potree-core | Point cloud viewer |
| proj4 | Coordinate reprojection |
| shpjs | Shapefile parsing |
| exifr | EXIF data extraction |
| file-saver | Client-side file download |

## 3. Docker Images

### 3.1 Frappe Image (`ghcr.io/fardani235/webodm-frappe:<version>`)
- Base: Ubuntu 24.04
- Python 3.14, Node.js, wkhtmltopdf (optional)
- Pre-installed bench, Frappe framework, `webodm_core` + `webodm_frontend` apps,
  and the built Vue SPA assets
- Multi-service image: web, worker, scheduler
- Built by CI from `frappe-bench/apps/Dockerfile` with the `frappe` submodule
  checkout; tagged with the submodule's `__version__`. Deploys pull it from GHCR
  pinned by digest in `docker-compose.yml`

### 3.2 Geospatial Image (Custom)
- Base: Ubuntu 24.04
- Python 3.12, GDAL, PDAL
- FastAPI application
- Port: 5000

### 3.3 Database Image (`postgis/postgis:16-3.4`)
- PostgreSQL 16 + PostGIS 3.4
- Port: 5432
- Health check: pg_isready

## 4. Frappe Site Configuration

### 4.1 Site Config (`site_config.json`)
```json
{
  "db_host": "127.0.0.1",
  "db_name": "webodm",
  "db_password": "webodm",
  "db_port": 5432,
  "db_type": "postgres",
  "db_user": "webodm",
  "installed_apps": [
    "frappe",
    "webodm_core",
    "webodm_frontend"
  ]
}
```

### 4.2 Environment Variables
| Variable | Default | Description |
|---|---|---|
| `DB_PASSWORD` | webodm | PostgreSQL password |
| `DB_NAME` | webodm | Database name |
| `DB_USER` | webodm | Database user |
| `SITE_NAME` | webodm.local | Frappe site name |
| `WO_PORT` | 8080 | Web server port |
| `ADMIN_PASSWORD` | admin | Frappe admin password |
| `REDIS_CACHE` | redis://redis-cache:13000 | Redis cache URL |
| `REDIS_QUEUE` | redis://redis-queue:11000 | Redis queue URL |

## 5. Task Processing Requirements

### 5.1 NodeODM Communication

The geospatial service does NOT directly process drone imagery. It handles the
*outputs* (GeoTIFFs, point clouds). Actual photogrammetry is handled by
**NodeODM / ODM** processing nodes.

Communication protocol:
- REST API to NodeODM (HTTP)
- Image upload via multipart/form-data
- Task status via polling
- Results downloaded as ZIP archive

### 5.2 Worker Concurrency

- RQ worker concurrency: 2-4 per container
- Task locking: Redis-based distributed locks (key: `task_lock_{task_id}`, TTL: 30s)
- Processing node assignment: round-robin with queue depth consideration

### 5.3 Asset Storage

Assets extracted from NodeODM's `all.zip` are stored as private Frappe **File**
records (under `sites/<site>/private/files/`) and linked from the `WebODM Task`
via `orthophoto` / `dsm` / `dtm` / `point_cloud` / `model` fields. The geospatial
service reads these files by absolute path (resolved and permission-checked by
Frappe's tile proxy).

| Asset Type | Task field | Format |
|---|---|---|
| Rasters | `orthophoto` / `dsm` / `dtm` | Cloud Optimized GeoTIFF |
| Point Clouds | `point_cloud` | LAS/LAZ |
| 3D Models | `model` | GLTF Binary (GLB) |
| Tiles | — | Rendered on demand by geospatial service (not pre-tiled) |

Each raster's georeferencing (`epsg`, `wkt`, `*_extent` GeoJSON Polygon in
EPSG:4326) is persisted on the task at download time via `/export/cogify`.

## 6. Performance Targets

### 6.1 API Response Times
| Endpoint | P50 | P95 |
|---|---|---|
| List projects | 200ms | 500ms |
| Create task | 300ms | 1000ms |
| Serve tile (256x256) | 100ms | 300ms |
| Login | 500ms | 1500ms |
| Get raster metadata | 200ms | 500ms |

### 6.2 Throughput
| Operation | Target |
|---|---|
| Tile requests | 100 req/s per dataset |
| Image upload | 50 MB/s sustained |
| Concurrent RQ jobs | 20+ |
| API requests (total) | 500 req/s |

### 6.3 Database
| Metric | Target |
|---|---|
| Connection pool | 20 connections |
| Query time (simple) | < 10ms |
| Query time (spatial) | < 100ms |
| PostGIS index build | < 5min for 10k tasks |

## 7. Security Requirements

| Requirement | Implementation |
|---|---|
| Authentication | Frappe session auth + API keys |
| JWT for tiles | Signed, 1h expiry, per-task scope |
| CSRF protection | Frappe built-in token system |
| File upload validation | MIME type, size limit (configurable) |
| Encryption at rest | Disk-level (LUKS) recommended |
| TLS | Caddy termination recommended |
| Rate limiting | Frappe built-in (login attempts) |
| Audit logging | Frappe Activity Log DocType |
| Backups | PostgreSQL pg_dump, asset volume snapshot |

## 8. Testing Requirements

### 8.1 Backend Testing
- Framework: Frappe's built-in test runner (unittest-based)
- Coverage target: 80%+
- Test types: unit, integration, API

### 8.2 Frontend Testing
- Framework: Vitest (Vite-native)
- Component tests for all Vue components
- E2E: Cypress (optional, Phase 8)

### 8.3 Geospatial Service Testing
- Framework: pytest + httpx (async)
- Integration tests with real GeoTIFFs
- Performance benchmarks for tile serving

## 9. Deployment Requirements

### 9.1 Docker Compose (Development)
Single-host deployment with all services on one machine.

### 9.2 Production (Future)
- Kubernetes or Docker Swarm for orchestration
- Separate Nginx reverse proxy with TLS
- Dedicated PostgreSQL with replication
- Geospatial service auto-scaling based on tile load
- Object storage (S3/MinIO) for assets
- CDN for tile delivery (optional)

## 10. Monitoring & Logging

| Component | Tool |
|---|---|
| Application logs | Frappe Log DocTypes, stdout |
| RQ job monitoring | Frappe RQ Dashboard |
| PostgreSQL | pg_stat_statements, pgBadger |
| Redis | Redis INFO/MONITOR |
| Geospatial service | FastAPI logging middleware |
| Uptime monitoring | Health check endpoints |

## 11. Implementation Phases

| Phase | Duration | Deliverables |
|---|---|---|
| 1. Foundation | 1 week | Bench setup, Docker, apps scaffold |
| 2. DocTypes | 2 weeks | All DocTypes, relationships |
| 3. Backend API | 4-6 weeks | API endpoints, task pipeline, permissions |
| 4. Geospatial Service | 3-4 weeks | FastAPI tile serving, exports |
| 5. Vue Frontend | 6-8 weeks | All 4 pages, components |
| 6. Plugin System | 2-3 weeks | Hook mapping, core plugin migration |
| 7. Core Plugins | 3-4 weeks | 15+ plugin migrations |
| 8. Deployment | 1-2 weeks | Docker, Nginx, SSL, CI/CD |
| **Total** | **22-30 weeks** | |

## 12. Risk Mitigation

| Risk | Impact | Mitigation |
|---|---|---|
| Frappe + PostGIS gap | Spatial queries require native geometry | Dedicated geospatial service handles all spatial ops |
| Task processing performance | Long-running Celery-style jobs | RQ scales horizontally; same Redis locking pattern |
| Large file uploads (1GB+ drone imagery) | Upload timeouts, memory pressure | Frappe supports chunked uploads; progress tracking |
| Plugin migration complexity | 20+ plugins need rework | Map one plugin at a time; Frappe hooks more mature |
| Potree/Three.js integration | Complex 3D library with Vue | Isolated component; no framework conflict |
| Leaflet + Frappe UI style conflicts | CSS collisions | Frappe UI uses Tailwind with prefix; scoped styles |
