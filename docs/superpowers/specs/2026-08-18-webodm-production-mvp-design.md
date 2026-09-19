# WebODM Production-Grade MVP Docker Compose

**Date**: 2026-08-18
**Status**: Approved for planning
**Scope**: MVP — single-VPS, single-host deployment with TLS, process isolation, idempotent init

## 1. Goals & Non-Goals

### Goals
- Replace current dev-only `docker-compose.yml` with a production-grade stack deployable to a single VPS
- Split every Frappe Procfile process into its own container for isolation, healthchecks, and independent restart
- Add Caddy as reverse proxy with automatic TLS via DNS-01 ACME challenge
- Make site initialization idempotent (re-runs on existing site are no-ops, not destructive)
- Move secrets out of `.env` into Docker secrets
- Segment network into 3 tiers (frontend/backend/data) with explicit egress boundaries
- Document the migration path from the existing host-based deployment

### Non-Goals (YAGNI for MVP)
- Multi-host / HA / Kubernetes
- Prometheus + Grafana observability stack
- Centralized log aggregation (Loki, ELK)
- CDN in front of Caddy
- Automatic image build & push to registry (manual `docker compose build` for MVP)
- Auto-scaling of workers (single worker instance is enough for MVP)
- SocketIO horizontal scaling (single socketio container is enough)
- Hot reload / live-reload in production containers (assets built at image build time)

## 2. Architecture

### 2.1 Topology

```
Internet
   │
   ▼ (443/80)
┌──────────────────────┐
│       Caddy          │  reverse proxy + TLS termination
│                      │  - /api/* → frappe-web
│                      │  - /socket.io/* → frappe-socketio
│                      │  - /tiles/* → geospatial
│                      │  - /assets/*, /files/*, /private/files/*, /public/* → frappe-web
│                      │  - everything else → frappe-web (Vue SPA fallback)
└──────┬───────┬───────┘
       │       │
       │       └────────► [frappe-socketio :9000]
       │
       └────────► [frappe-web :8000]   (gunicorn)
                    │
        ┌───────────┼───────────┐
        │           │           │
   [postgres]  [redis-cache]  [redis-queue]   (all internal)
        │
   [frappe-worker]  [frappe-scheduler]   (internal)
        │
   [geospatial :5000]   (internal)
        │
   [nodeodm :3000]      (internal, photogrammetry engine)

One-shot (profile=init):
   [frappe-init]   → bench new-site + install-app, exit 0

Periodic:
   [backup]        → bench backup, daily 03:00, retains 14 days
```

### 2.2 Networks (3 tier, segmented)

| Network | Internal | Services |
|---|---|---|
| `frontend` | no | caddy, frappe-web, frappe-socketio, geospatial |
| `backend` | yes (no egress) | frappe-web, frappe-worker, frappe-scheduler, frappe-init, frappe-socketio, geospatial, postgres, redis-cache, redis-queue, backup |
| `data` | yes (no egress) | postgres, redis-cache, redis-queue, frappe-init |

- Caddy is only on `frontend` (it terminates traffic from the internet)
- Postgres and Redis are only on `data` and `backend`, never on `frontend`
- Only the published Caddy ports (`80`, `443`) are reachable from the host
- All app containers on `backend` (which is internal) cannot reach the internet — outbound HTTP requires explicit egress, which is not configured. Frappe's outbound email (SMTP) must be configured via a relay or the `backend` network must be made non-internal in a future iteration. For MVP, either accept this limitation or document it as known issue.

### 2.3 Service Inventory

| Service | Image | Port | Purpose |
|---|---|---|---|
| `caddy` | custom build (caddy + DNS plugin) | 80, 443 | TLS, reverse proxy, security headers |
| `frappe-init` | webodm-frappe | — | One-shot: bench new-site + install-app |
| `frappe-web` | webodm-frappe | 8000 | Gunicorn web server |
| `frappe-worker` | webodm-frappe | — | RQ worker (queues: default, long, short) |
| `frappe-scheduler` | webodm-frappe | — | RQ beat / cron dispatcher |
| `frappe-socketio` | webodm-frappe | 9000 | Node SocketIO server |
| `postgres` | postgis/postgis:16-3.4 | 5432 | DB + PostGIS |
| `redis-cache` | redis:7-alpine | 13000 | Frappe cache + SocketIO pub/sub |
| `redis-queue` | redis:7-alpine | 11000 | RQ jobs |
| `geospatial` | custom build (services/geospatial) | 5000 | FastAPI tile service |
| `nodeodm` | opendronemap/nodeodm:latest | 3000 | Photogrammetry engine |
| `backup` | custom build (infra/backup) | — | Daily bench backup |

All Frappe process containers (`web`, `worker`, `scheduler`, `socketio`, `init`) share one image and select role via the `FRAPPE_ROLE` env variable consumed by `/entrypoint.sh`.

## 3. Frappe Custom Image

### 3.1 Multi-Stage Dockerfile

Build context: `frappe-bench/apps/`
Dockerfile: `frappe-bench/apps/Dockerfile`

**Stage 1 (builder)** — `python:3.14-slim-bookworm`
- apt: build-essential, libpq-dev, libmariadb-dev, libxml2-dev, libxslt1-dev, libssl-dev, libjpeg-dev, zlib1g-dev, libpango1.0-dev, libpangoft2-1.0-0, pkg-config, default-libmysqlclient-dev, git
- pip install all Frappe + app deps (Frappe v16.26.3, webodm_core, webodm_frontend editable)
- `bench setup requirements`, `bench build --production --hard-link`
- Validate `node --version` available for SocketIO build

**Stage 2 (runtime)** — `python:3.14-slim-bookworm`
- apt: libpq5, libmagic1, libssl3, libxml2, libxslt1.1, libjpeg62-turbo, zlib1g, libpango-1.0-0, libpangocairo-1.0-0, poppler-utils, fonts-noto-core, libffi8, curl, tini
- Node.js 20 (for SocketIO worker)
- COPY `--from=builder` the bench
- WORKDIR `/workspace/frappe-bench`
- Entrypoint `/entrypoint.sh` reads `FRAPPE_ROLE`

### 3.2 Image Tags

- Locally built: `webodm-frappe:16.26.3` (versioned to match `apps.json`)
- For MVP: not pushed to a registry — `docker compose build` runs on the target host

### 3.3 Why one image, multi-role

- Reuses pip cache across `web`, `worker`, `scheduler`, `socketio`
- Same image can also run as `init` for site bootstrap
- Different `command:` per service picks the role from `FRAPPE_ROLE` env

## 4. Caddy + TLS

### 4.1 Custom Caddy Image

Path: `infra/caddy/Dockerfile`

Builds Caddy with DNS plugins via `xcaddy`:
- `github.com/caddy-dns/cloudflare`
- `github.com/caddy-dns/route53`

Other DNS providers can be added later by extending the `xcaddy build` line.

### 4.2 Caddyfile

Path: `infra/caddy/Caddyfile`

Key directives:
- `{$SITE_DOMAIN}` site block, default `webodm.local`
- `encode zstd gzip`
- Security headers: HSTS, X-Content-Type-Options nosniff, X-Frame-Options DENY, Referrer-Policy strict-origin-when-cross-origin, Permissions-Policy, CSP (customized for Frappe desk + Vue SPA + Leaflet — Leaflet tile sources are proxied through `/tiles/*` on the same origin, so `connect-src 'self' wss://{$SITE_DOMAIN}` and `img-src 'self' data: blob:` are sufficient)
- `-Server` header to suppress version disclosure
- Route matchers:
  - `@login path /api/method/login` → rate_limit 50r/s
  - `@socketio path /socket.io/*` → reverse_proxy `frappe-socketio:9000`
  - `@tiles path /tiles/*` → reverse_proxy `geospatial:5000` with `flush_interval -1` (large raster bytes)
  - `@assets` for Frappe static files → reverse_proxy `frappe-web:8000`
  - Default route → reverse_proxy `frappe-web:8000` with `X-Forwarded-For`/`X-Forwarded-Proto` headers

### 4.3 TLS Strategy

- Caddy auto-requests a cert from Let's Encrypt via DNS-01 challenge
- DNS provider selected at container start via env: `CLOUDFLARE_API_TOKEN` (Cloudflare) or `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (Route53)
- Certs stored in `caddy_data` named volume (`/data`)
- For `SITE_DOMAIN=localhost` or other non-FQDN, Caddy self-signs an internal CA cert (acceptable for dev/LAN)

### 4.4 Caddy Volumes

- `caddy_data:/data` — ACME accounts, certs
- `caddy_config:/config` — Caddy runtime config cache
- `./infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro` — config file

## 5. Site Initialization (Idempotent)

### 5.1 Init Container

Service `frappe-init`, profile `init`. Built from same image as `web`/`worker`/`scheduler`/`socketio` but with `FRAPPE_ROLE=init` and a one-shot entrypoint.

Entrypoint logic (`infra/frappe/init.sh`):
1. If `sites/${SITE_NAME}/site_config.json` already exists → log and exit 0
2. Otherwise: `bench new-site ${SITE_NAME} --db-type postgres --db-host postgres --db-port 5432 --db-name ${DB_NAME} --db-user ${DB_USER} --db-password ${DB_PASSWORD} --admin-password ${ADMIN_PASSWORD} --install-app webodm_core --install-app webodm_frontend --set-default`
3. The `install-app` step is also idempotent on re-run (Frappe skips already-installed apps)

Invocation:
```bash
docker compose --profile init run --rm frappe-init
```

### 5.2 Depends_on Chain

```
postgres (healthy)
  └─→ frappe-init (completed)
        ├─→ frappe-web (healthy)
        ├─→ frappe-worker
        ├─→ frappe-scheduler
        └─→ frappe-socketio
              └─→ caddy (healthy)
```

## 6. Secrets Management

### 6.1 Docker Secrets

Four secrets, files mounted from `./secrets/` (gitignored):

| Secret file | Mount path | Consumer |
|---|---|---|
| `db_password.txt` | `/run/secrets/db_password` | postgres (POSTGRES_PASSWORD_FILE), frappe-init/web (DB_PASSWORD_FILE) |
| `admin_password.txt` | `/run/secrets/admin_password` | frappe-init/web (ADMIN_PASSWORD_FILE) |
| `redis_cache_password.txt` | `/run/secrets/redis_cache_password` | redis-cache, frappe-web/worker/scheduler/socketio |
| `redis_queue_password.txt` | `/run/secrets/redis_queue_password` | redis-queue, frappe-web/worker/scheduler |

Generated by `scripts/init-secrets.sh` using `openssl rand -base64 32`. File mode `0600`.

### 6.2 Why Docker Secrets over `.env`

- Per-service scoping (a postgres secret can't be read by geospatial)
- File-based, no env-var exposure in `docker inspect`
- Built into Docker Engine, no extra infra

### 6.3 Non-Secret Config (.env)

`.env` (gitignored) carries:
- `SITE_DOMAIN`, `SITE_NAME`
- `DB_NAME`, `DB_USER`
- `FRAPPE_VERSION=16.26.3`
- DNS provider tokens for Caddy
- Backup destination config (S3 bucket/keys)
- `BACKUP_SCHEDULE`, `BACKUP_RETENTION_DAYS`

`.env.example` is committed with empty values.

## 7. Volumes

| Volume | Mount | Consumers |
|---|---|---|
| `postgres_data` | `/var/lib/postgresql/data` | postgres |
| `redis_cache_data` | `/data` | redis-cache |
| `redis_queue_data` | `/data` | redis-queue |
| `frappe_sites` | `/workspace/frappe-bench/sites` | frappe-init/web/worker/scheduler/socketio, backup (ro) |
| `frappe_assets` | `/workspace/frappe-bench/sites/assets` | frappe-web/socketio |
| `frappe_logs` | `/workspace/frappe-bench/logs` | frappe-web/worker/scheduler/socketio |
| `frappe_data` | `/data` | frappe-web/worker (uploads), geospatial |
| `nodeodm_data` | `/var/www/data` | nodeodm |
| `backup_storage` | `/backups` | backup |
| `caddy_data` | `/data` | caddy |
| `caddy_config` | `/config` | caddy |

## 8. Healthchecks

| Service | Test | Interval | Timeout | Retries | Start period |
|---|---|---|---|---|---|
| postgres | `pg_isready -U ${DB_USER}` | 10s | 5s | 5 | — |
| redis-cache | `redis-cli -a $(cat /run/secrets/...) ping` | 10s | 3s | 5 | — |
| redis-queue | same as above | 10s | 3s | 5 | — |
| frappe-web | `curl -fsS http://localhost:8000/api/method/ping` | 30s | 10s | 3 | 60s |
| frappe-socketio | `curl -fsS http://localhost:9000/socket.io/?EIO=4&transport=polling` | 30s | 5s | 3 | 60s |
| caddy | `wget --spider -q http://localhost:2019/health` | 30s | 5s | 3 | — |
| geospatial | `wget -qO- http://localhost:5000/health` | 30s | 5s | 3 | — |
| nodeodm | `curl -fsS http://localhost:3000/info` | 30s | 10s | 3 | 60s |
| backup | none (cron container, restart on failure is enough) | — | — | — | — |

Worker and scheduler do not need healthchecks — RQ heartbeat handles liveness.

## 9. Resource Limits

| Service | CPU limit | Memory limit |
|---|---|---|
| frappe-web | 2 | 2G |
| frappe-worker | 2 | 2G |
| frappe-scheduler | 1 | 1G |
| frappe-socketio | 1 | 1G |
| frappe-init | 1 | 1G |
| postgres | 2 | 2G |
| redis-cache | 0.5 | 512M |
| redis-queue | 0.5 | 512M |
| caddy | 0.5 | 256M |
| geospatial | 2 | 2G |
| nodeodm | (omitted — engine manages its own limits) | — |
| backup | 0.5 | 512M |

Single host should have at least 8 vCPU and 16 GB RAM to fit these.

## 10. Backup Strategy

Service `backup` runs `cron` (super-dim container) with one job:
- Schedule: `BACKUP_SCHEDULE` (default `0 3 * * *`)
- Action: `bench --site ${SITE_NAME} backup --with-files --with-private-files`
- Retention: `BACKUP_RETENTION_DAYS` (default 14) — deletes local backups older than that
- Offsite: if `BACKUP_S3_BUCKET` set, rsync to S3-compatible storage (B2, MinIO, etc.)

Backup container mounts `frappe_sites:ro` and `frappe_data:ro` so it never mutates the source.

For MVP: local backups only are required. S3 is a "set env var and it works" upgrade.

## 11. Migration from Host-Based Deployment

### 11.1 Steps

```bash
# On the old host:
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench
bench --site webodm.local backup --with-files --with-private-files

# Copy to compose root:
mkdir -p ./migration
cp sites/webodm.local/private/backups/<timestamp>-webodm-local-* ./migration/

# On the new host (after `docker compose build` + `docker compose up -d postgres`):
# Start frappe-init so the site directory exists
docker compose --profile init run --rm frappe-init

# Restore into the freshly created empty site:
docker compose exec -T frappe-web \
  bench --site webodm.local restore \
    /workspace/frappe-bench/sites/webodm.local/private/backups/migration/<timestamp>-webodm-local-database.sql.gz \
    --with-private-files /workspace/frappe-bench/sites/webodm.local/private/backups/migration/<timestamp>-webodm-local-files.tar.gz
```

After restore: `docker compose restart frappe-web frappe-worker frappe-scheduler frappe-socketio`.

## 12. Operational Runbook

```bash
# First-time setup on a fresh host:
./scripts/init-secrets.sh
cp .env.example .env && $EDITOR .env
docker compose build
docker compose up -d postgres redis-cache redis-queue   # start data tier first
docker compose --profile init run --rm frappe-init      # bootstrap site
docker compose up -d                                    # start all services
docker compose logs -f caddy                            # watch TLS cert issuance

# Verify:
curl -fsSL https://${SITE_DOMAIN}/api/method/ping
docker compose exec frappe-web bench --site ${SITE_NAME} doctor

# Manual backup:
docker compose exec backup /usr/local/bin/backup.sh

# Update images / restart:
docker compose pull                                     # for upstream images
docker compose build                                    # for our custom images
docker compose up -d

# Tail logs:
docker compose logs -f --tail=200 frappe-web
```

## 13. Out of Scope (Future Iterations)

These were considered and explicitly deferred:

1. **HA / multi-host**: requires session affinity layer (Redis session store, sticky load balancer) and DB replication. Out of scope for single-VPS MVP.
2. **Observability stack**: Prometheus + Grafana + cAdvisor + Loki. Adds 4-6 containers; defer to next iteration when there's actual load.
3. **CDN**: Single VPS handles MVP traffic; add Cloudflare/Fastly when egress costs matter.
4. **Image registry**: For MVP, `docker compose build` on each host. Add GHCR/Docker Hub when there are >1 hosts.
5. **Auto-scaling**: Single worker is fine for MVP. Add scale-up logic when queue lag exceeds threshold.
6. **SocketIO horizontal scaling**: requires sticky sessions. Defer.
7. **Hot reload**: development-only feature, excluded from production image.

## 14. Future Nginx Note

Per stakeholder direction, **nginx** is reserved for future features (likely: complex request routing, WebSocket load balancing, or per-tenant routing rules that Caddyfile becomes unwieldy for). For MVP, Caddy covers all current requirements with less configuration overhead.

If the future nginx migration becomes necessary:
- Drop Caddy container
- Add nginx + certbot (or cert-manager sidecar)
- Move TLS renewal to a cron-based certbot + S3-style cert sync

This is not in the MVP scope and is documented here only for future-reference continuity.

## 15. Open Questions / Assumptions

- **Single worker is sufficient**: assumption based on current load (single NodeODM, single user team). Validate post-launch.
- **Single host has ≥8 vCPU / 16GB RAM**: hardware requirement from §9. Confirm with deployment target.
- **DNS provider is Cloudflare or Route53**: both DNS plugins built into Caddy image. Other providers require extending `xcaddy` build line.
- **`SITE_DOMAIN` is a real FQDN**: required for Let's Encrypt to issue a cert. Local dev uses `webodm.local` (Caddy self-signed) and host file override.
- **NodeODM is included in the compose stack**: required by SPEC.md §2.1. The current docker-compose omits it — MVP fixes that.
- **Existing host's `frappe-bench/sites/webodm.local/site_config.json` has `db_host: 127.0.0.1`** — must be updated during migration to `db_host: postgres` (or via `bench set-mariadb-host`).
- **`backend` network is internal**: outbound SMTP/HTTP from Frappe is blocked. For email notifications, either (a) deploy a sidecar SMTP relay on a non-internal network, (b) keep `backend` non-internal in MVP, or (c) accept that email notifications are disabled until the next iteration. Default for MVP: option (b) — make `backend` non-internal. Caddy still cannot reach `backend` (it stays on `frontend` only), but Frappe apps can egress for SMTP.
- **`use_redis_auth` in `common_site_config.json`**: the existing config has `use_redis_auth: false`. For MVP we switch to `true` so Redis ACL passwords are honored. This is documented in the runbook.
