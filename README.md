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
┌────────▼───────┐              ┌────────▼──────────────┐    ┌──────────────────┐
│  PostgreSQL +  │              │  Serving cache         │◄──►│  Object storage  │
│  PostGIS 16    │              │  (private/files)       │    │  S3 / MinIO      │
└────────────────┘              └───────────────────────┘    │  (canonical)     │
         │                                                   └──────────────────┘
┌────────▼───────┐   ┌──────────────────┐   ┌──────────────────────┐
│     Redis      │   │  Provisioner     │──►│ on-demand NodeODM     │
│  (cache/queue) │   │  (AWS EC2 now)   │   │ (created per task)    │
└────────────────┘   └──────────────────┘   └──────────────────────┘
                     static `nodeodm` service = fallback / default
```

Processing compute and storage are both pluggable and both optional: with
nothing configured the stack runs the built-in `nodeodm` engine and keeps
files on the host, as before. With a bucket and a provisioner configured,
tasks provision a machine on demand and all inputs/outputs live in S3 (the
host keeps a serving cache). See
[`docs/on-demand-processing/`](docs/on-demand-processing/architecture.md).

### Repository layout

This is a monorepo. `webodm_core`, `webodm_frontend`, and the geospatial service
are tracked directly in this repository; `frappe` is the only external app (a git
submodule).

| Path | Purpose |
|---|---|
| `frappe-bench/` | Frappe Bench environment (version = the `frappe` submodule pin; `scripts/frappe-version.sh`) |
| `frappe-bench/apps/frappe/` | Frappe framework (submodule, upstream `frappe/frappe`) |
| `frappe-bench/apps/webodm_core/` | All DocTypes, business logic, and API |
| `frappe-bench/apps/webodm_frontend/` | Vue 3 SPA + Frappe page hooks |
| `services/geospatial/` | Standalone FastAPI tile/analysis service (hosts the **system** analysis plugins) |
| `services/plugin-runner/` | Sandbox that executes **user** analysis plugins (uploaded per organization) |
| `services/provisioner/` | On-demand compute service: one `Provider` interface (AWS EC2, dev `fixed`), stateless HTTP API |
| `docs/on-demand-processing/` | User guide, deployment, runbook, troubleshooting, configuration reference, architecture for on-demand compute + object storage |
| `infra/aws/` | IAM policies per identity, bucket policy, security group notes, NodeODM AMI bake script |
| `docs/plugins/` | User plugin guide, example plugin, Semantic Segmentation and 3D Reconstruction plugin docs |
| `plugins/semantic-segmentation/` | Semantic Segmentation **user** plugin (orthophoto / DSM / DTM, pluggable ONNX + rule models) |
| `plugins/3d-reconstruction/` | 3D Reconstruction **user** plugin (DSM/DTM/LAZ/orthophoto/ODM mesh → web-ready georeferenced GLB for the 3D viewer) |
| `infra/` | Caddy, backup, Frappe entrypoint/config, AWS artefacts |

## Tech Stack

### Backend
| Component | Technology |
|---|---|
| Framework | Frappe v16 (pinned via submodule; Python 3.14) |
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

### Plugin Runner (user plugin sandbox)

```bash
cd services/plugin-runner
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# Sandbox dir must match Frappe's `plugin_sandbox_dir` (default: the site's private/plugin_sandbox)
SANDBOX_DIR=$PWD/../../frappe-bench/sites/webodm.local/private/plugin_sandbox \
  uvicorn app.main:app --port 5001

# Run a plugin package locally without the stack
python -m app.cli ../../docs/plugins/examples/elevation-mask --input raster=dsm.tif --param threshold=120 --output out.tif
```

See [`docs/plugins/user-plugin-guide.md`](docs/plugins/user-plugin-guide.md) for writing, testing, packaging and installing plugins.

### Docker Compose (Full Stack)

```bash
docker compose up -d
```

With MinIO standing in for S3 and the `fixed` compute provider (exercises the
whole on-demand lifecycle without a cloud account):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

See [`docs/on-demand-processing/deployment.md`](docs/on-demand-processing/deployment.md).

### Provisioner service

```bash
cd services/provisioner
python -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python -m pytest -q            # AWS provider tested against moto
PROVISIONER_PROVIDER=fixed PROVISIONER_FIXED_ENDPOINT=127.0.0.1:3000 ./venv/bin/uvicorn app.main:app --port 5002
```

## Project Structure

```
g20-daas/
├── .env                          # Environment variables
├── docker-compose.yml            # Full stack orchestration
├── README.md                     # This file
├── SPEC.md                       # Specification document
├── TRD.md                        # Technical requirements document
├── docker-compose.dev.yml        # Dev override: MinIO + fixed compute provider
├── docs/                         # Runbook, deployment guide, migration, plugin guide, on-demand processing
├── infra/                        # Caddy, backup, Frappe entrypoint, AWS artefacts
├── services/geospatial/          # Standalone FastAPI geospatial service (local + s3:// rasters)
├── services/plugin-runner/       # Sandbox service for user analysis plugins
├── services/provisioner/         # On-demand compute provisioner (provider seam)
│
└── frappe-bench/                 # Frappe Bench root
    ├── Procfile                  # bench start process definitions
    ├── apps/
    │   ├── frappe/               # Frappe framework (git submodule)
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
    │   │   │   │   │   ├── ModelView.vue    # 3D viewer (Three.js, GLB)
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

services/plugin-runner/           # User plugin sandbox
├── app/
│   ├── main.py                   # FastAPI: POST /run, GET /health
│   ├── sandbox.py                # Package extraction, rlimited subprocess, output georef
│   └── cli.py                    # `python -m app.cli` — run a plugin locally
├── Dockerfile
├── requirements.txt
└── tests/

docs/plugins/
├── user-plugin-guide.md          # How to write, test, package, upload, manage plugins
├── semantic-segmentation.md      # Running segmentation on orthophoto / DSM / DTM / all three
├── 3d-reconstruction.md          # Building web-ready 3D models from a task's ODM outputs
└── examples/elevation-mask/      # Starter plugin (manifest, entrypoint, tests)
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
