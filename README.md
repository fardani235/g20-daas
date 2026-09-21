# WebODM — Frappe + Vue Rework

A complete rework of [WebODM](https://github.com/OpenDroneMap/WebODM) — the open-source drone
imagery processing platform — rebuilt on **Frappe v16** (backend) and **Vue 3 + frappe-ui**
(frontend), with a dedicated **FastAPI geospatial microservice** for raster/point-cloud
processing.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│            Caddy (:80, :443) — TLS termination            │
└────────┬──────────────┬────────────────┬────────────────┘
         │              │                │
┌────────▼───────┐ ┌────▼────────┐ ┌─────▼──────────────┐
│  Frappe Desk   │ │    Vue 3    │ │  GeoSpatial Service │
│  (backend API) │ │    SPA      │ │  (FastAPI + GDAL)   │
└────────┬───────┘ └─────────────┘ └─────┬──────────────┘
         │                               │
┌────────▼───────┐              ┌────────▼──────────────┐
│  PostgreSQL +  │              │  Shared File Storage   │
│  PostGIS 16    │              │  (raster/pointcloud)   │
└────────────────┘              └───────────────────────┘
         │
┌────────▼───────┐
│     Redis      │
│  (cache/queue) │
└────────────────┘
```

### Repository layout

This is a monorepo. `webodm_core`, `webodm_frontend`, and the geospatial service
are tracked directly in this repository; `frappe` is the only external app (a git
submodule).

| Path | Purpose |
|---|---|
| `frappe-bench/` | Frappe Bench environment (Frappe version = `frappe` submodule pin, see `scripts/frappe-version.sh`) |
| `frappe-bench/apps/frappe/` | Frappe framework (submodule, upstream `frappe/frappe`) |
| `frappe-bench/apps/webodm_core/` | All DocTypes, business logic, and API |
| `frappe-bench/apps/webodm_frontend/` | Vue 3 SPA + Frappe page hooks |
| `services/geospatial/` | Standalone FastAPI tile/analysis service |
| `infra/` | Caddy, backup, and image build assets |

## Tech Stack

### Backend
| Component | Technology |
|---|---|
| Framework | Frappe v16.34.0 (Python 3.14) — pinned via the `frappe` submodule |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| Task Queue | Frappe RQ (Redis-backed) |
| Cache | Redis 7 |

### Frontend
| Component | Technology |
|---|---|
| Framework | Vue 3 (Composition API) |
| UI Library | frappe-ui (TailwindCSS + Headless UI) |
| Map | Leaflet 1.9 + @vue-leaflet/vue-leaflet |
| Bundler | Vite 6 |
| Icons | Lucide (via unplugin-icons) |

### Geospatial Service
| Component | Technology |
|---|---|
| Framework | FastAPI |
| Raster Processing | GDAL, Rasterio, rio-tiler |
| Point Cloud | PDAL |

## Quick Start

### Prerequisites
- Python 3.14+
- Node.js 20+
- Docker + Docker Compose
- Redis (or use the one started by bench)

### Development Setup

```bash
# 1. Start PostgreSQL + PostGIS
docker run -d --name webodm-db \
  -e POSTGRES_USER=webodm \
  -e POSTGRES_PASSWORD=webodm \
  -e POSTGRES_DB=webodm \
  -v pgdata:/var/lib/postgresql/data \
  -p 5432:5432 \
  postgis/postgis:16-3.4

# 2. Enable PostGIS
docker exec webodm-db psql -U webodm -d webodm -c "CREATE EXTENSION IF NOT EXISTS postgis;"

# 3. Start Frappe (from frappe-bench directory)
cd frappe-bench
source env/bin/activate
bench start

# The site webodm.local runs on http://localhost:8080
# Login: Administrator / admin
```

### Frontend Dev Server (Hot Reload)

```bash
cd apps/webodm_frontend/frontend
npm run dev
# Runs on http://localhost:8081, proxies /api to Frappe
```

### Build Frontend

```bash
cd apps/webodm_frontend/frontend
npm run build
# Output goes to ../webodm_frontend/public/frontend/
```

### Geospatial Service

```bash
cd services/geospatial

# Create venv and install
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run
uvicorn app.main:app --reload --port 5000
```

### Docker Compose (Full Stack)

```bash
docker compose up -d
```

## Project Structure

```
g20-daas/
├── .env                          # Environment variables
├── docker-compose.yml            # Full stack orchestration
├── README.md                     # This file
├── SPEC.md                       # Specification document
├── TRD.md                        # Technical requirements document
├── docs/                         # Runbook, deployment guide, migration
├── infra/                        # Caddy, backup, image build assets
├── services/geospatial/          # Standalone FastAPI geospatial service
│
└── frappe-bench/                 # Frappe Bench root
    ├── Procfile                  # bench start process definitions
    ├── apps/
    │   ├── frappe/               # Frappe framework (submodule, pinned tag)
    │   ├── webodm_core/          # DocTypes + business logic
    │   │   └── webodm_core/
    │   │       ├── hooks.py
    │   │       ├── config/
    │   │       └── templates/
    │   │
    │   ├── webodm_frontend/      # Vue 3 SPA
    │   │   ├── frontend/         # Vite + Vue 3 project
    │   │   │   ├── src/
    │   │   │   │   ├── pages/
    │   │   │   │   │   ├── Dashboard.vue    # Project list
    │   │   │   │   │   ├── MapView.vue      # 2D map (Leaflet)
    │   │   │   │   │   ├── ModelView.vue    # 3D viewer (Potree)
    │   │   │   │   │   └── Console.vue      # Task logs
    │   │   │   │   ├── components/
    │   │   │   │   ├── composables/
    │   │   │   │   └── utils/
    │   │   │   ├── vite.config.js
    │   │   │   ├── index.html
    │   │   │   └── package.json
    │   │   └── webodm_frontend/
    │   │       ├── hooks.py       # Frappe hooks (page registration)
    │   │       └── public/        # Built frontend assets
    │   │
    ├── sites/
    │   ├── common_site_config.json
    │   └── webodm.local/
    │       └── site_config.json
    │
    ├── env/                      # Python venv (Python 3.14)
    ├── config/                   # Redis configs
    └── logs/                     # Log files

services/geospatial/              # Standalone FastAPI service
├── app/
│   ├── main.py                   # FastAPI entrypoint
│   ├── routers/
│   │   ├── tiles.py              # TMS tile endpoints
│   │   ├── export.py             # Raster export + COG endpoints
│   │   ├── pointcloud.py         # Point cloud endpoints
│   │   ├── analysis.py           # Analysis plugin endpoints
│   │   └── volume.py             # Volume measurement endpoints
│   ├── models/task.py            # Pydantic models
│   └── utils/
│       ├── raster.py             # GDAL/rasterio helpers
│       └── storage.py            # File path resolution
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── tests/
```

## Key Commands

### Frappe / Bench
```bash
bench start                    # Start all dev processes
bench --site webodm.local      # Run commands against site
bench console                  # Interactive Python shell
bench migrate                  # Run pending migrations
bench build                    # Rebuild Frappe assets
```

### App Development
```bash
bench make-app ./apps <name>   # Create new app (interactive)
bench --site webodm.local install-app <name>
bench --site webodm.local uninstall-app <name>
```

### DocType Development
```bash
bench --site webodm.local --app webodm_core create-doctype <name>
# Or create via the Frappe Desk UI at /desk
```

## Credits

Based on the original [WebODM](https://github.com/OpenDroneMap/WebODM) by Piero Toffanin
and contributors. Licensed under MIT.
