# WebODM Docker Compose — Operational Runbook

Day-to-day operations for the WebODM Frappe + Vue + Geospatial stack run entirely
in Docker Compose. The compose project is named `g20-daas` and the project root is
the repo root (where `docker-compose.yml` lives). Override the project name with
the `COMPOSE_PROJECT_NAME` env var when running more than one stack on a host.

## Service map

| Service | Image | Host port(s) | Purpose |
|---|---|---|---|
| `caddy` | `webodm-caddy:2` | 80, 443 | TLS termination, static caching, rate-limit on `/api/method/login`, reverse proxy to `frappe-web`, `frappe-socketio`, `geospatial` |
| `postgres` | `postgis/postgis:16-3.4` | — | Application DB + PostGIS. Only on the `data` and `backend` networks |
| `redis-cache` | `redis:7-alpine` | — | Frappe's cache (port 13000, auth). Backend + data |
| `redis-queue` | `redis:7-alpine` | — | Frappe's RQ (port 11000, auth, AOF). Backend + data |
| `frappe-init` | `webodm-frappe:<ver>@sha256:…` | — | One-shot bootstrap (`bench new-site webodm.local`) |
| `frappe-web` | `webodm-frappe:<ver>@sha256:…` | — | Gunicorn on 8000. The web tier |
| `frappe-worker` | `webodm-frappe:<ver>@sha256:…` | — | RQ worker for `default,long,short` queues |
| `frappe-scheduler` | `webodm-frappe:<ver>@sha256:…` | — | `bench schedule` cron loop |
| `frappe-socketio` | `webodm-frappe:<ver>@sha256:…` | — | Node socketio on 9000 (real-time updates) |
| `geospatial` | `webodm-geospatial:1` | — | FastAPI tile/export service on 5000 |
| `nodeodm` | `opendronemap/nodeodm:latest` | — | ODM processing engine on 3000 |
| `backup` | `webodm-backup:1` | — | systemd-cron + bench backup, hourly at 03:00 by default |

Networks: `frontend` (caddy ↔ outside + caddy ↔ frappe-web/geospatial/socketio),
`backend` (everything that needs to talk to Frappe internals), `data` (internal —
postgres + redis only, no external ingress).

## First-time setup on a fresh host

```bash
# 1. Install prerequisites
#    - Docker Engine 25+ with Compose v2 (`docker compose` subcommand)
#    - 8 vCPU / 16 GB RAM minimum (Postgres alone reserves 2 GB)
#    - 50 GB free disk for image layers, volumes, and 14 days of backups

# 2. Clone the repo
git clone <repo-url> /opt/webodm
cd /opt/webodm

# 3. Generate secrets (one-time). Skips if secrets/*.txt already exist.
./scripts/init-secrets.sh

# 4. Configure
cp .env.example .env
$EDITOR .env   # set SITE_DOMAIN, DNS provider token, backup target, etc.

# 5. Add /etc/hosts entry for the local domain (dev only — production uses real DNS)
echo "127.0.0.1 ${SITE_DOMAIN}" | sudo tee -a /etc/hosts

# 6. Pull the custom images (caddy, frappe, backup, geospatial) from GHCR.
#    The build: blocks in docker-compose.yml are commented out, so deploys pull
#    prebuilt images. To rebuild locally, see frappe-bench/apps/Dockerfile.
docker compose pull caddy frappe-web geospatial backup

# 7. Start the data tier and wait for healthy
docker compose up -d postgres redis-cache redis-queue
sleep 10
docker compose ps --format '{{.Service}}: {{.Status}}' \
  | grep -E 'postgres|redis'

# 8. Bootstrap the site (one-time). Creates webodm.local + installs apps.
docker compose run --rm frappe-init

# 9. Start the rest (web, worker, scheduler, socketio, geospatial, nodeodm, backup, caddy)
docker compose up -d

# 10. Watch the TLS bootstrap
docker compose logs -f caddy
```

Login at `https://${SITE_DOMAIN}/` with `Administrator` and the password from
`secrets/admin_password.txt`.

### Required env vars

| Var | Default | Notes |
|---|---|---|
| `SITE_DOMAIN` | `webodm.local` | Used by Caddy for TLS issuance. Production must be a real FQDN |
| `SITE_NAME` | `webodm.local` | The Frappe bench site name |
| _(no `FRAPPE_VERSION`)_ | — | The Frappe image tag+digest is fixed in `docker-compose.yml`; bump with `scripts/pin-images.sh` |
| `DB_NAME` / `DB_USER` | `webodm` / `webodm` | Postgres DB + user (password is in `secrets/db_password.txt`) |
| `CLOUDFLARE_API_TOKEN` | (empty) | If set, Caddy uses DNS-01 via Cloudflare. Required for wildcard certs |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | (empty) | Alternative DNS-01 provider (Route53) |
| `ADMIN_EMAIL` | (empty) | Caddy contact email for Let's Encrypt expiry notices |
| `BACKUP_SCHEDULE` | `0 3 * * *` | 5-field cron expression for the `backup` service |
| `BACKUP_RETENTION_DAYS` | `14` | Local retention. S3 sync retains forever |
| `BACKUP_S3_BUCKET` / `_ACCESS_KEY` / `_SECRET_KEY` / `_ENDPOINT` | (empty) | Optional S3-compatible sync target |

## Day-to-day operations

> **Note:** `docker-compose.yml` defines no Compose profiles — every service is
> always in scope, so `--profile init` is not needed. The `frappe-init` service is
> a regular one-shot service (`profiles: ["init"]` was removed).

| Task | Command |
|---|---|
| Status of all services | `docker compose ps` |
| Tail web logs | `docker compose logs -f --tail=200 frappe-web` |
| Tail all Frappe logs | `docker compose logs -f --tail=50 frappe-web frappe-worker frappe-scheduler frappe-socketio` |
| Restart the web tier | `docker compose restart frappe-web` |
| Restart everything | `docker compose restart` |
| Open a bench console | `docker compose exec frappe-web bench --site ${SITE_NAME:-webodm.local} console` |
| Run `bench doctor` | `docker compose exec frappe-web bench --site ${SITE_NAME:-webodm.local} doctor` ⚠️ may fail with `AuthenticationError` against the auth-enabled Redis; treat as informational only |
| Run `bench migrate` | `docker compose exec frappe-web bench --site ${SITE_NAME:-webodm.local} migrate` |
| Trigger a backup now | `docker compose exec backup /usr/local/bin/backup.sh` |
| List local backups | `docker compose exec backup ls -l /backups` |
| Tail backup logs | `docker compose logs -f --tail=100 backup` |
| Live tail of cron | `docker compose exec backup tail -f /var/log/cron.log` |
| Update custom images | `docker compose pull && docker compose up -d` (rebuild locally from `frappe-bench/apps/Dockerfile` if you changed app code) |
| Pull upstream images | `docker compose pull postgres redis-cache redis-queue nodeodm` |
| Migrate from host install | See [docs/migration-from-host.md](migration-from-host.md) |

## Healthcheck reference

`docker compose ps` shows `(healthy)` / `(unhealthy)` next to the
status string for services that have a Docker healthcheck defined. Not every
service has one — the table below lists which do and which don't.

| Service | Has healthcheck? | Probe | Interval |
|---|---|---|---|
| `postgres` | ✅ | `pg_isready -U webodm` | 10s |
| `redis-cache` | ✅ | `redis-cli -p 13000 -a <pw> ping` matches `PONG` | 10s |
| `redis-queue` | ✅ | `redis-cli -p 11000 -a <pw> ping` matches `PONG` | 10s |
| `frappe-web` | ✅ | `curl -fsS http://localhost:8000/api/method/ping` | 30s |
| `frappe-socketio` | ✅ | `curl -fsS http://localhost:9000/socket.io/?EIO=4&transport=polling` contains `sid` | 30s |
| `geospatial` | ✅ | `wget -qO- http://localhost:5000/health` | 30s |
| `nodeodm` | ✅ | `curl -fsS http://localhost:3000/info` | 30s |
| `caddy` | ✅ | `curl -ksSo /dev/null --max-time 5 http://localhost:80/` | 30s |
| `frappe-worker` | ❌ | (long-running RQ worker; track via logs) | — |
| `frappe-scheduler` | ❌ | (long-running RQ beat; track via logs) | — |
| `frappe-init` | ❌ | (one-shot, exits 0/1) | — |
| `backup` | ❌ | (cron container; track via `docker logs`) | — |

Use `docker compose ps --format '{{.Service}}\t{{.Status}}'` for a
quick at-a-glance check. Expect `Up` (no healthcheck) on worker/scheduler/init/backup.

## Troubleshooting

### Frappe can't reach Redis

```bash
docker compose logs redis-cache redis-queue
```

Verify password files and connectivity:

```bash
docker compose exec redis-cache \
  sh -c 'redis-cli -p 13000 -a "$(cat /run/secrets/redis_cache_password)" ping'
docker compose exec redis-queue \
  sh -c 'redis-cli -p 11000 -a "$(cat /run/secrets/redis_queue_password)" ping'
```

If you see `NOAUTH Authentication required`, the `frappe_sites` volume's
`common_site_config.json` doesn't match the current redis secrets.
`infra/frappe/configure_site.py` (run by the entrypoint on **every** container
start) upserts `redis_cache` / `redis_queue` from the mounted secrets, so a
restart normally fixes it; if not, check that the `redis_*_password` secret
files are what the redis containers were started with. Inside the container the
file should contain `"redis_cache": "redis://:<pw>@redis-cache:13000"`.

### TLS cert not issued

Caddy will fall back to an internal CA for non-FQDNs (e.g. `webodm.local`), which
generates a self-signed cert that browsers will reject. For production:

```bash
docker compose logs caddy | grep -iE 'certificate|acme|dns'
```

Make sure `CLOUDFLARE_API_TOKEN` (or `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`)
is set in `.env` and the token has DNS edit permission for the zone. Restart Caddy
to pick up new env vars:

```bash
docker compose up -d --force-recreate caddy
```

### Frappe web returns 500

```bash
docker compose logs --tail=500 frappe-web
docker compose exec frappe-web \
  tail -100 /workspace/frappe-bench/logs/web.error.log
```

Common root causes:

- `db_host` in `site_config.json` is `127.0.0.1` — should be `postgres`.
- Postgres hasn't finished initialising — wait another 30s and retry.
- App code threw during a request — full traceback is in `web.error.log`.

### Worker not picking up jobs

```bash
docker compose logs --tail=200 frappe-worker
# `redis-cli` is NOT in the Frappe image (slim image); exec into the redis-queue
# container instead:
RQ_PW=$(cat secrets/redis_queue_password.txt)
docker compose exec -T redis-queue \
  redis-cli -p 11000 -a "$RQ_PW" LLEN rq:queue:default
```

If `LLEN` is non-zero and the worker is idle, the worker is failing to dequeue.
Look for `redis.exceptions` or `ConnectionError` in the worker logs.

### SocketIO not connecting

```bash
docker compose logs --tail=100 frappe-socketio
docker compose exec frappe-socketio \
  curl -fsS 'http://localhost:9000/socket.io/?EIO=4&transport=polling' | head -c 200
```

The probe must return an `sid` substring. If you see `EADDRINUSE 0.0.0.0:9000`,
another container is binding the port — check for orphaned `frappe-socketio`
containers with `docker ps -a`.

### Backup fails

```bash
docker compose logs backup
docker compose exec backup /usr/local/bin/backup.sh   # manual trigger
```

Common failure modes:

- **`docker: command not found`** — the `webodm-backup:1` image is missing the
  docker CLI. Rebuild it from `infra/backup/Dockerfile` (the `build:` block in
  `docker-compose.yml` is commented out, so `docker compose build` does nothing).
- **`pg_dump: error: server version mismatch`** — the Frappe image's
  `pg_dump` is older than the postgres server. The shipped image installs
  `postgresql-client-16` from `apt.postgresql.org` to align with Postgres 16.
- **`No such option: --with-private-files`** — `bench backup` in v16 dropped
  this flag. `--with-files` already includes private files.
- **`service "frappe-scheduler" depends on undefined service "frappe-init"`** —
  this error belonged to the old `profiles: ["init"]` setup, which has been
  removed. If you still see it, your `docker-compose.yml` is stale — update it.

### Geospatial service unreachable

```bash
docker compose logs geospatial
docker compose exec geospatial \
  wget -qO- http://localhost:5000/health
```

The `geospatial` image expects DATA_DIR=/data and REDIS_URL pointing at
`redis-cache:13000`. The named volume `frappe_data` is mounted at `/data`.

### Caddy returns 502 for all Frappe routes

The `frappe-web` container is unhealthy or restarting. Check:

```bash
docker compose ps frappe-web
docker compose logs --tail=50 frappe-web
```

If `frappe-web` is exiting immediately, read the first lines of its log: the
entrypoint (`infra/frappe/entrypoint.sh`) fails fast with the name of any
missing secret/env var. A corrupt `common_site_config.json` in the
`frappe_sites` volume is the other common cause; the entrypoint upserts the
keys it owns but does not repair invalid JSON. Reset:

```bash
docker compose down
docker volume rm g20-daas_frappe_sites
docker compose up -d postgres redis-cache redis-queue
docker compose run --rm frappe-init
docker compose up -d
```

### Reset the site (destructive)

```bash
# WARNING: drops the entire database and all uploaded files.
docker compose down -v
docker compose up -d postgres redis-cache redis-queue
docker compose run --rm frappe-init
docker compose up -d
```

This is the only command that deletes the `postgres_data`, `frappe_sites`,
`frappe_assets`, `frappe_data`, etc. named volumes. **Never** use `-v` on a
production stack as part of routine maintenance.

## Capacity planning

### Single-VPS sizing (MVP)

| Resource | Recommended | Notes |
|---|---|---|
| vCPU | 8 | Postgres: 2, Frappe web: 2, worker: 2, scheduler: 1, socketio: 1, geospatial: 2, caddy: 0.5, backup: 0.5, nodeodm: 0 |
| RAM | 16 GB | Postgres: 2 GB, Frappe web: 2 GB, worker: 2 GB, scheduler: 1 GB, socketio: 1 GB, geospatial: 2 GB, caddy: 256 MB, backup: 512 MB, nodeodm: dynamic |
| Disk | 100 GB SSD | 5 GB Frappe image, 1 GB Caddy image, 1 GB backup image, 500 MB other images, 10 GB Postgres, 5 GB `/data`, 14 days of backups |

### Storage growth

| Source | Rate | Drivers |
|---|---|---|
| Postgres | ~50 MB/day for metadata + DocTypes | Project count, task count, audit log |
| `/data` (raster, point cloud) | 20-500 MB per task | Orthophoto, DSM, DTM, LAZ exports |
| Backups (`/backups`) | ~1 GB/day with `--with-files` | Scales with private file uploads |
| `frappe_logs` | ~10 MB/day | Rotated by `find -mtime` if you set up logrotate (not currently automated) |

### Scaling up

- **Vertical (single host):** bump each service's `deploy.resources.limits` in
  `docker-compose.yml`. Restart the service (`docker compose up -d --force-recreate <svc>`).
- **Horizontal (multi-host):** split the compose project. Run `postgres` + `redis-*`
  on a dedicated DB host, run `frappe-web` + `frappe-worker` + `frappe-scheduler` on
  app hosts, run `caddy` on edge hosts. The `frappe_sites` and `frappe_data` volumes
  need to be promoted to shared storage (NFS, EFS, Longhorn) or migrated to a
  database/S3 backend.

### Backup retention

`BACKUP_RETENTION_DAYS` defaults to 14. The local find command prunes files in
both `/backups` (the `backup_storage` volume) and the source Frappe site dir.
S3 sync (if configured) is **not** pruned — manually `aws s3 rm` old prefixes
or wrap the `aws s3 sync` call with an `aws s3api list-objects-v2` filter.

## Security checklist

- `SITE_DOMAIN` is a real FQDN (not `webodm.local`) so Caddy can issue a public cert.
- `CLOUDFLARE_API_TOKEN` / `AWS_*_KEY` set for DNS-01 issuance.
- `ADMIN_PASSWORD` rotated from `admin` (the default in `.env.example` — but `.env.example`
  doesn't actually carry `ADMIN_PASSWORD`; the value lives in `secrets/admin_password.txt`
  which is generated by `scripts/init-secrets.sh`).
- `secrets/*.txt` files are `chmod 600` — `scripts/init-secrets.sh` sets this.
- `ADMIN off` in the Caddyfile — Caddy's admin API on port 2019 is unreachable
  from the host (verified by `curl --max-time 2 http://localhost:2019/` → connection refused).
- Rate limit on `/api/method/login` is 50 requests/sec/IP — this is enforced at the
  Caddy layer, not at Frappe. Brute-force attempts will get 429s.
- Caddyfile sets `Strict-Transport-Security`, `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, and a baseline
  `Content-Security-Policy` on every response.
- The `data` network is `internal: true` — postgres and redis are not reachable
  from the host or from outside the compose project.
