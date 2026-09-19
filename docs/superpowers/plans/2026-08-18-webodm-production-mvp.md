# WebODM Production-Grade MVP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dev-only `docker-compose.yml` at `/home/ridwan/workspaces/frappe-webodm/` with a production-grade MVP stack: Caddy + Frappe split per process + 2 Redis + Postgres/PostGIS + Geospatial + NodeODM + backup, all behind Docker secrets and segmented networks.

**Architecture:** Single-root docker-compose with 12 services split into 3 network tiers (frontend/backend/data). One Frappe image, multi-role via `FRAPPE_ROLE` env. Caddy terminates TLS via DNS-01 ACME (Cloudflare or Route53). Site init is a one-shot idempotent container. Backup runs as a cron container.

**Tech Stack:** Docker Compose v2, Caddy 2 (with caddy-dns plugins), Python 3.14, Frappe v16.26.3, PostgreSQL 16 + PostGIS 3.4, Redis 7, Node.js 20, FastAPI (geospatial), NodeODM, OpenSSL for secret generation.

**Spec:** `docs/superpowers/specs/2026-08-18-webodm-production-mvp-design.md`

## Global Constraints

- **Workspace root:** `/home/ridwan/workspaces/frappe-webodm/` — all new files relative to here unless noted.
- **Frappe version:** 16.26.3 (match `frappe-bench/sites/apps.json`).
- **Python:** 3.14 (required by Frappe v16).
- **Image name:** `webodm-frappe:16.26.3` (locally built, not pushed to a registry).
- **Caddy image name:** `webodm-caddy:2` (locally built with DNS plugins).
- **Geospatial build context:** `../webodm-geospatial` (relative to compose root).
- **Postgres image:** `postgis/postgis:16-3.4`.
- **Redis images:** `redis:7-alpine`, ports 13000 (cache) and 11000 (queue).
- **NodeODM image:** `opendronemap/nodeodm:latest`.
- **Networks:** `frontend` (caddy + reachable app services), `backend` (non-internal, allows SMTP egress), `data` (internal, postgres + redis).
- **Compose project name:** `webodm`.
- **Secrets storage:** Docker secrets, files in `./secrets/` (gitignored), mounted at `/run/secrets/*`.
- **No commits** unless explicitly requested in a task.
- **Verification gate per task:** every task ends with concrete `docker compose …` / `curl …` / `docker compose exec …` checks.

---

## Task 1: Repo Hygiene (`.gitignore`, `.env.example`, secrets init script)

**Files:**
- Modify: `.gitignore` (create if missing)
- Create: `.env.example`
- Create: `scripts/init-secrets.sh`

**Interfaces:**
- Produces: `./secrets/{db,admin,redis_cache,redis_queue}_password.txt` files of mode 0600
- Produces: `.env` template that `cp .env.example .env` can use

- [ ] **Step 1: Create `.gitignore`** (or modify if exists). Add these lines (preserving any existing entries):

```
# Secrets — never commit
.env
.env.local
.env.*.local
secrets/
secrets/*.txt

# Docker compose override for local dev
docker-compose.override.yml

# Migration artifacts
migration/

# OS / editor cruft
.DS_Store
*.swp
*.bak
.idea/
.vscode/
```

Create file with: `mkdir -p .gitignore.d 2>/dev/null; touch .gitignore` only if absent. Edit existing `.gitignore` to ensure entries above are present.

- [ ] **Step 2: Create `.env.example`**

```bash
cat > .env.example <<'EOF'
# --- Site identity ---
SITE_DOMAIN=webodm.local
SITE_NAME=webodm.local
FRAPPE_VERSION=16.26.3

# --- Database (non-secret handles only — password via Docker secret) ---
DB_NAME=webodm
DB_USER=webodm

# --- Caddy TLS (set ONE provider) ---
# Cloudflare:
CLOUDFLARE_API_TOKEN=
# Route53 (set both):
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

# --- Backup ---
BACKUP_SCHEDULE=0 3 * * *
BACKUP_RETENTION_DAYS=14
BACKUP_S3_BUCKET=
BACKUP_S3_ACCESS_KEY=
BACKUP_S3_SECRET_KEY=
BACKUP_S3_ENDPOINT=
EOF
```

- [ ] **Step 3: Create `scripts/init-secrets.sh`**

```bash
mkdir -p scripts
cat > scripts/init-secrets.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p secrets
umask 077

for name in db_password admin_password redis_cache_password redis_queue_password; do
  path="secrets/${name}.txt"
  if [ -f "$path" ]; then
    echo "OK: $path already exists (skipping)"
  else
    openssl rand -base64 32 | tr -d '\n' > "$path"
    chmod 600 "$path"
    echo "GEN: $path"
  fi
done

echo
echo "Secrets ready in ./secrets/"
ls -l secrets/
EOF
chmod +x scripts/init-secrets.sh
```

- [ ] **Step 4: Run the secrets init script and verify**

Run: `./scripts/init-secrets.sh`
Expected output (example):
```
GEN: secrets/db_password.txt
GEN: secrets/admin_password.txt
GEN: secrets/redis_cache_password.txt
GEN: secrets/redis_queue_password.txt

Secrets ready in ./secrets/
total 16
-rw------- 1 ... db_password.txt
-rw------- 1 ... admin_password.txt
-rw------- 1 ... redis_cache_password.txt
-rw------- 1 ... redis_queue_password.txt
```

- [ ] **Step 5: Verify `.gitignore` blocks secrets**

Run:
```bash
git check-ignore -v secrets/db_password.txt .env 2>&1 || \
  git status --porcelain --ignored | grep -E '(secrets/|\.env)' && echo "PASS: ignored"
```

Expected: at least one of the two commands prints a `PASS: ignored` line, OR `git check-ignore` exits 0 with a path showing `.gitignore:N: secrets/...` or `.gitignore:N: .env`.

If git returns "fatal: not a git repository", that's fine — verify by listing `.gitignore`:
```bash
grep -E '^\.env$|^secrets/' .gitignore && echo "PASS"
```

- [ ] **Step 6: Mark task complete**

No commit. Move to Task 2.

---

## Task 2: Frappe Image (Dockerfile + entrypoint + init script)

**Files:**
- Create: `frappe-bench/apps/Dockerfile`
- Create: `infra/frappe/entrypoint.sh`
- Create: `infra/frappe/init.sh`

**Interfaces:**
- Produces: image `webodm-frappe:${FRAPPE_VERSION:-16.26.3}` that runs any of these roles when `FRAPPE_ROLE` is set: `init`, `web`, `worker`, `scheduler`, `socketio`
- `entrypoint.sh` reads `FRAPPE_ROLE` and dispatches
- `init.sh` is called when `FRAPPE_ROLE=init`; exits 0 idempotently

### Step 1: Create the multi-stage Dockerfile

```bash
mkdir -p infra/frappe
cat > frappe-bench/apps/Dockerfile <<'EOF'
# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.14
ARG NODE_MAJOR=20

# -------- Stage 1: builder --------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    default-libmysqlclient-dev \
    git \
    libffi-dev \
    libjpeg-dev \
    libldap2-dev \
    libmariadb-dev \
    libpango1.0-dev \
    libpangoft2-1.0-0 \
    libpq-dev \
    libsasl2-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    pkg-config \
    zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Copy apps so the build context has them. Sites dir comes from a volume at runtime.
COPY . /workspace/

# Install bench + Frappe + custom apps (editable so bench update works inside the container).
RUN pip install --no-cache-dir frappe-bench \
 && pip install --no-cache-dir -e ./frappe \
 && pip install --no-cache-dir -e ./webodm_core \
 && pip install --no-cache-dir -e ./webodm_frontend

# Build production assets.
RUN cd /workspace/frappe-bench && \
    bench setup requirements && \
    bench build --production --hard-link
EOF
```

### Step 2: Create the runtime stage (append to same Dockerfile)

Append to `frappe-bench/apps/Dockerfile`:

```bash
cat >> frappe-bench/apps/Dockerfile <<'EOF'

# -------- Stage 2: runtime --------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SITES_PATH=/workspace/frappe-bench/sites

ARG NODE_MAJOR
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    fonts-noto-core \
    libffi8 \
    libjpeg62-turbo \
    libldap-2.5-0 \
    libmagic1 \
    libmariadb3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpq5 \
    libsasl2-2 \
    libssl3 \
    libxml2 \
    libxslt1.1 \
    poppler-utils \
    tini \
    zlib1g \
 && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && node --version

COPY --from=builder /workspace /workspace
COPY infra/frappe/entrypoint.sh /entrypoint.sh
COPY infra/frappe/init.sh /usr/local/bin/frappe-init.sh
RUN chmod +x /entrypoint.sh /usr/local/bin/frappe-init.sh

WORKDIR /workspace/frappe-bench

EXPOSE 8000 9000

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["frappe-web"]
EOF
```

### Step 3: Create `infra/frappe/entrypoint.sh`

```bash
mkdir -p infra/frappe
cat > infra/frappe/entrypoint.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

role="${FRAPPE_ROLE:-web}"
echo "[entrypoint] FRAPPE_ROLE=${role}"

# Resolve *_FILE secrets to env vars for downstream tooling.
for var in DB_PASSWORD ADMIN_PASSWORD REDIS_CACHE_PASSWORD REDIS_QUEUE_PASSWORD; do
  file_var="${var}_FILE"
  if [ -n "${!file_var:-}" ] && [ -f "${!file_var}" ]; then
    export "$var=$(cat "${!file_var}")"
  fi
done

cd /workspace/frappe-bench

case "$role" in
  init)
    exec /usr/local/bin/frappe-init.sh
    ;;
  web)
    # gunicorn with sync workers, threads off (Frappe handles its own concurrency per worker)
    exec gunicorn \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --worker-class sync \
      --timeout "${GUNICORN_TIMEOUT:-120}" \
      --access-logfile - \
      --error-logfile - \
      frappe.app:application
    ;;
  worker)
    exec bench worker --queue default,long,short
    ;;
  scheduler)
    exec bench schedule
    ;;
  socketio)
    exec bench socketio --port 9000
    ;;
  *)
    echo "Unknown FRAPPE_ROLE: $role" >&2
    exit 1
    ;;
esac
EOF
chmod +x infra/frappe/entrypoint.sh
```

### Step 4: Create `infra/frappe/init.sh`

```bash
cat > infra/frappe/init.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "${SITE_NAME:?SITE_NAME is required}"
: "${DB_HOST:?DB_HOST is required}"
: "${DB_PORT:?DB_PORT is required}"
: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required (set via DB_PASSWORD_FILE)}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD is required (set via ADMIN_PASSWORD_FILE)}"

cd /workspace/frappe-bench

# Idempotency guard: if site_config.json already exists, do nothing.
if [ -f "sites/${SITE_NAME}/site_config.json" ]; then
  echo "[init] Site ${SITE_NAME} already exists; skipping bootstrap."
  exit 0
fi

echo "[init] Creating site ${SITE_NAME} on postgres://${DB_HOST}:${DB_PORT}/${DB_NAME}"
bench new-site "${SITE_NAME}" \
  --db-type postgres \
  --db-host "${DB_HOST}" \
  --db-port "${DB_PORT}" \
  --db-name "${DB_NAME}" \
  --db-user "${DB_USER}" \
  --db-password "${DB_PASSWORD}" \
  --admin-password "${ADMIN_PASSWORD}" \
  --install-app webodm_core \
  --install-app webodm_frontend \
  --set-default

echo "[init] Site ${SITE_NAME} ready."
EOF
chmod +x infra/frappe/init.sh
```

### Step 5: Build the image (initial verification)

Run from workspace root:
```bash
DOCKER_BUILDKIT=1 docker build \
  -t webodm-frappe:16.26.3 \
  -f frappe-bench/apps/Dockerfile \
  --build-context root=. \
  frappe-bench/apps
```

**Expected build time**: 5-15 minutes depending on network and host speed. Look for final line:
```
=> => naming to docker.io/library/webodm-frappe:16.26.3
```

If the build fails on `bench build --production --hard-link`, capture the error and check whether the `frappe-bench/sites` directory is present in the build context — `bench` needs `sites/apps.txt` to know what apps to build for. Confirm by:
```bash
ls frappe-bench/sites/apps.txt
```
Expected: file exists.

### Step 6: Smoke-test the image boots in `web` role

```bash
docker run --rm \
  -e FRAPPE_ROLE=web \
  -e SITE_NAME=smoketest.local \
  webodm-frappe:16.26.3 \
  sh -c 'echo "ROLE=$FRAPPE_ROLE" && ls /workspace/frappe-bench/apps.txt 2>/dev/null; ls /workspace/frappe-bench/sites 2>/dev/null | head -5; which gunicorn; gunicorn --version'
```

Expected:
- `ROLE=web` printed
- `/workspace/frappe-bench/apps.txt` exists (or close enough — sites/apps.txt may not exist without volume mount)
- `which gunicorn` returns `/usr/local/bin/gunicorn` or `/usr/bin/gunicorn`
- `gunicorn --version` prints a version string

### Step 7: Smoke-test `init` role with missing site_config.json

```bash
docker run --rm \
  -e FRAPPE_ROLE=init \
  -e SITE_NAME=smoketest.local \
  -e DB_HOST=127.0.0.1 \
  -e DB_PORT=5432 \
  -e DB_NAME=smoketest \
  -e DB_USER=smoke \
  -e DB_PASSWORD=smoke \
  -e ADMIN_PASSWORD=smoke \
  -v $(pwd)/frappe-bench/sites:/workspace/frappe-bench/sites \
  webodm-frappe:16.26.3
```

Expected: the script exits early because pg connection will fail (no postgres running), proving the entrypoint dispatched to `init.sh`. Failure output should show `[init] Creating site ...` then a Postgres connection error. That's fine for this smoke test.

If you want a clean idempotent skip instead, run a second time after manually creating a dummy site:
```bash
mkdir -p frappe-bench/sites/smoketest.local
cat > frappe-bench/sites/smoketest.local/site_config.json <<'EOF'
{"db_name":"smoketest","db_password":"x"}
EOF
docker run --rm \
  -e FRAPPE_ROLE=init \
  -e SITE_NAME=smoketest.local \
  -v $(pwd)/frappe-bench/sites:/workspace/frappe-bench/sites \
  webodm-frappe:16.26.3
```
Expected: prints `[init] Site smoketest.local already exists; skipping bootstrap.` and exits 0.

After test, clean up:
```bash
rm -rf frappe-bench/sites/smoketest.local
```

### Step 8: Mark task complete

Image built and verified. Move to Task 3.

---

## Task 3: Caddy Image + Caddyfile

**Files:**
- Create: `infra/caddy/Dockerfile`
- Create: `infra/caddy/Caddyfile`

**Interfaces:**
- Produces: image `webodm-caddy:2` with Cloudflare + Route53 DNS plugins baked in
- Produces: `Caddyfile` that reads `$SITE_DOMAIN` and reverse-proxies to compose services

### Step 1: Create the Caddy Dockerfile

```bash
mkdir -p infra/caddy
cat > infra/caddy/Dockerfile <<'EOF'
# syntax=docker/dockerfile:1.7

FROM caddy:2-builder AS builder
RUN xcaddy build \
    --with github.com/caddy-dns/cloudflare \
    --with github.com/caddy-dns/route53

FROM caddy:2-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
EOF
```

### Step 2: Create the Caddyfile

```bash
cat > infra/caddy/Caddyfile <<'EOF'
# Auto-TLS via Let's Encrypt DNS-01 challenge.
# Set CLOUDFLARE_API_TOKEN or AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY in the environment.
{
    email {$ADMIN_EMAIL:admin@{$SITE_DOMAIN}}
    admin off
}

{$SITE_DOMAIN} {
    encode zstd gzip

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "geolocation=(), microphone=(), camera=()"
        Content-Security-Policy "default-src 'self'; img-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self' wss://{$SITE_DOMAIN}; font-src 'self' data:; frame-ancestors 'none'"
        -Server
    }

    # Rate limit Frappe login endpoint.
    @login path /api/method/login
    rate_limit @login 50r/s

    # SocketIO long-lived connections.
    @socketio path /socket.io/*
    reverse_proxy @socketio frappe-socketio:9000

    # Geospatial raster tiles (large responses; disable buffering).
    @tiles path /tiles/*
    reverse_proxy @tiles geospatial:5000 {
        flush_interval -1
    }

    # Frappe static files (assets, files, public, private).
    @assets path /assets/* /files/* /public/* /private/files/*
    reverse_proxy @assets frappe-web:8000

    # Everything else → Frappe web (Vue SPA fallback included).
    reverse_proxy frappe-web:8000 {
        header_up Host {host}
        header_up X-Real-IP {remote}
        header_up X-Forwarded-For {remote}
        header_up X-Forwarded-Proto {scheme}
    }
}
EOF
```

### Step 3: Build the image

```bash
docker build -t webodm-caddy:2 infra/caddy
```

Expected: ends with `=> => naming to docker.io/library/webodm-caddy:2`. Build typically takes 1-3 minutes (pulls Go module cache first time).

### Step 4: Verify Caddy binary has DNS plugins

```bash
docker run --rm webodm-caddy:2 caddy version
docker run --rm webodm-caddy:2 caddy list-modules | grep -E '^dns\.(cloudflare|route53)'
```

Expected:
- `v2.x.x ...` (any 2.x)
- Two lines containing `dns.cloudflare` and `dns.route53`

If empty, the xcaddy build silently failed — re-run `docker build` with `--progress=plain` and inspect for `xcaddy build` errors.

### Step 5: Validate the Caddyfile syntax (without bringing up the full stack)

```bash
docker run --rm \
  -e SITE_DOMAIN=example.com \
  -v $(pwd)/infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro \
  webodm-caddy:2 \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Expected: `Valid configuration` followed by the JSON dump truncated. If "invalid configuration", read the error line and fix the Caddyfile.

### Step 6: Mark task complete

Move to Task 4.

---

## Task 4: Backup Container Image + Script

**Files:**
- Create: `infra/backup/Dockerfile`
- Create: `infra/backup/backup.sh`
- Create: `infra/backup/crontab`

**Interfaces:**
- Produces: image `webodm-backup:1` that runs `cron` in the foreground
- Produces: cron job running `backup.sh` at `$BACKUP_SCHEDULE`
- Produces: local backups in `/backups` volume, optional S3 sync

### Step 1: Create the Dockerfile

```bash
mkdir -p infra/backup
cat > infra/backup/Dockerfile <<'EOF'
# syntax=docker/dockerfile:1.7

FROM python:3.14-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    cron \
    curl \
    tini \
 && rm -rf /var/lib/apt/lists/*

# bench needs a Frappe bench to call `bench backup`. We don't run a full bench here;
# we exec into the frappe-web service via docker compose, so this container just needs
# bash + cron + a small wrapper that does the exec via the compose socket.
COPY backup.sh /usr/local/bin/backup.sh
COPY crontab /etc/cron.d/webodm-backup

RUN chmod +x /usr/local/bin/backup.sh \
 && chmod 0644 /etc/cron.d/webodm-backup \
 && crontab /etc/cron.d/webodm-backup \
 && touch /var/log/cron.log

CMD ["/usr/bin/tini", "--", "bash", "-c", "cron -f >> /var/log/cron.log 2>&1"]
EOF
```

### Step 2: Create the crontab

```bash
cat > infra/backup/crontab <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

${BACKUP_SCHEDULE} /usr/local/bin/backup.sh >> /var/log/cron.log 2>&1
EOF
```

### Step 3: Create the backup script

```bash
cat > infra/backup/backup.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

: "${SITE_NAME:?SITE_NAME is required}"

log() { echo "[backup $(date -u +%FT%TZ)] $*"; }

backup_root="/backups"
mkdir -p "${backup_root}"
chmod 700 "${backup_root}"

# Run the bench backup via docker compose exec into the web service.
# This requires the backup container to share the compose network AND have access to
# the docker socket, OR to use docker compose CLI. We use docker compose via the socket
# mounted at /var/run/docker.sock.

cd /compose-root || cd /workspace
docker compose exec -T frappe-web \
    bench --site "${SITE_NAME}" backup --with-files --with-private-files \
  || log "WARN: bench backup returned non-zero"

# Retention: delete local backups older than BACKUP_RETENTION_DAYS
if [ -n "${BACKUP_RETENTION_DAYS:-}" ]; then
  find "${backup_root}" -type f -mtime "+${BACKUP_RETENTION_DAYS}" -delete \
    && log "Pruned local backups older than ${BACKUP_RETENTION_DAYS} days"
fi

# Optional S3 sync (any S3-compatible: B2, MinIO, etc.)
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  if command -v aws >/dev/null 2>&1; then
    AWS_ARGS=()
    [ -n "${BACKUP_S3_ENDPOINT:-}" ] && AWS_ARGS+=(--endpoint-url "${BACKUP_S3_ENDPOINT}")
    [ -n "${BACKUP_S3_ACCESS_KEY:-}" ] && export AWS_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY}"
    [ -n "${BACKUP_S3_SECRET_KEY:-}" ] && export AWS_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_KEY}"
    aws "${AWS_ARGS[@]}" s3 sync "${backup_root}" "s3://${BACKUP_S3_BUCKET}/webodm/" \
      && log "Synced backups to s3://${BACKUP_S3_BUCKET}/webodm/"
  else
    log "WARN: BACKUP_S3_BUCKET set but 'aws' CLI not present"
  fi
fi

log "Backup cycle done"
EOF
chmod +x infra/backup/backup.sh
```

### Step 4: Build the image

```bash
docker build -t webodm-backup:1 infra/backup
```

Expected: ends with `=> => naming to docker.io/library/webodm-backup:1`. Build is fast (~30 sec).

### Step 5: Mark task complete

Move to Task 5.

---

## Task 5: docker-compose.yml Skeleton + Networks + Volumes

**Files:**
- Create: `docker-compose.yml`

**Interfaces:**
- Produces: a compose file with name `webodm`, all networks and volumes defined, but only `caddy` defined as a service (so we can incrementally add services and verify each tier)
- All other services commented out or removed for this task; we'll add them in Tasks 6-11

### Step 1: Create the docker-compose.yml skeleton

```bash
cat > docker-compose.yml <<'EOF'
name: webodm

x-frappe-image: &frappe-image
  image: webodm-frappe:${FRAPPE_VERSION:-16.26.3}
  build:
    context: ./frappe-bench/apps
    dockerfile: Dockerfile

x-frappe-env-base: &frappe-env-base
  SITE_NAME: ${SITE_NAME:-webodm.local}
  DB_HOST: postgres
  DB_PORT: 5432
  DB_NAME: ${DB_NAME:-webodm}
  DB_USER: ${DB_USER:-webodm}
  REDIS_CACHE: redis://redis-cache:13000
  REDIS_QUEUE: redis://redis-queue:11000
  REDIS_SOCKETIO: use_redis_cache
  FRAPPE_VERSION: ${FRAPPE_VERSION:-16.26.3}

x-frappe-volumes: &frappe-volumes
  - frappe_sites:/workspace/frappe-bench/sites
  - frappe_assets:/workspace/frappe-bench/sites/assets
  - frappe_logs:/workspace/frappe-bench/logs
  - frappe_data:/data

services:
  caddy:
    image: webodm-caddy:2
    build:
      context: ./infra/caddy
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    environment:
      SITE_DOMAIN: ${SITE_DOMAIN:-webodm.local}
      ADMIN_EMAIL: ${ADMIN_EMAIL:-}
      CLOUDFLARE_API_TOKEN: ${CLOUDFLARE_API_TOKEN:-}
      AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID:-}
      AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY:-}
    volumes:
      - ./infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    networks:
      - frontend
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://localhost:2019/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 256M

networks:
  frontend:
    driver: bridge
  backend:
    driver: bridge
  data:
    driver: bridge
    internal: true

volumes:
  postgres_data:
  redis_cache_data:
  redis_queue_data:
  frappe_sites:
  frappe_assets:
  frappe_logs:
  frappe_data:
  nodeodm_data:
  backup_storage:
  caddy_data:
  caddy_config:

secrets:
  db_password:
    file: ./secrets/db_password.txt
  admin_password:
    file: ./secrets/admin_password.txt
  redis_cache_password:
    file: ./secrets/redis_cache_password.txt
  redis_queue_password:
    file: ./secrets/redis_queue_password.txt
EOF
```

### Step 2: Validate compose file syntax

```bash
docker compose config --quiet && echo "PASS" || echo "FAIL"
```

Expected: `PASS`. If `FAIL`, run `docker compose config` (without `--quiet`) and read the error.

### Step 3: Create the named volumes

```bash
docker compose up -d caddy
docker compose ps
docker compose logs caddy | tail -20
```

Expected:
- `docker compose ps` shows `caddy` with state `Up (healthy)` (after ~30 sec — healthcheck interval)
- Logs show Caddy trying to fetch a cert for `webodm.local` (will fail without real DNS, but that's fine — the container is up)

If the cert request fills logs with errors about DNS lookup, that's expected for `webodm.local`. To stop the noise, stop the container:
```bash
docker compose down
```

### Step 4: Mark task complete

Move to Task 6.

---

## Task 6: Data Tier Services (Postgres, Redis-Cache, Redis-Queue)

**Files:**
- Modify: `docker-compose.yml` — add 3 services to the `services:` block

**Interfaces:**
- `postgres` (named `postgres` on network `data` + `backend`) listens on 5432 (internal)
- `redis-cache` listens on 13000 (internal) with password from secret
- `redis-queue` listens on 11000 (internal) with password from secret

### Step 1: Add the three services

Edit `docker-compose.yml` and insert these services under `caddy:` (preserve indentation):

```yaml
  postgres:
    image: postgis/postgis:16-3.4
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${DB_NAME:-webodm}
      POSTGRES_USER: ${DB_USER:-webodm}
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password
      POSTGRES_INITDB_ARGS: "--encoding=UTF8 --locale=C"
    secrets:
      - db_password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - data
      - backend
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER:-webodm}"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          cpus: "2"
          memory: 2G

  redis-cache:
    image: redis:7-alpine
    restart: unless-stopped
    command:
      - redis-server
      - --save
      - ""
      - --appendonly
      - "no"
      - --maxmemory
      - 512mb
      - --maxmemory-policy
      - allkeys-lru
      - --requirepass
      - /run/secrets/redis_cache_password
    secrets:
      - redis_cache_password
    volumes:
      - redis_cache_data:/data
    networks:
      - data
      - backend
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a \"$$(cat /run/secrets/redis_cache_password)\" ping | grep -q PONG"]
      interval: 10s
      timeout: 3s
      retries: 5
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M

  redis-queue:
    image: redis:7-alpine
    restart: unless-stopped
    command:
      - redis-server
      - --save
      - ""
      - --appendonly
      - "yes"
      - --appendfsync
      - everysec
      - --requirepass
      - /run/secrets/redis_queue_password
    secrets:
      - redis_queue_password
    volumes:
      - redis_queue_data:/data
    networks:
      - data
      - backend
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a \"$$(cat /run/secrets/redis_queue_password)\" ping | grep -q PONG"]
      interval: 10s
      timeout: 3s
      retries: 5
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
```

### Step 2: Validate compose

```bash
docker compose config --quiet && echo "PASS"
```

Expected: `PASS`.

### Step 3: Start the data tier

```bash
docker compose up -d postgres redis-cache redis-queue
docker compose ps
```

Expected: all three with state `Up (healthy)` within ~30 seconds.

### Step 4: Verify each service from the host

Postgres:
```bash
DB_PW=$(cat secrets/db_password.txt)
docker compose exec postgres \
  psql -U webodm -d webodm -c 'SELECT version();'
```
Expected: prints a `PostgreSQL 16.x ...` line.

Redis cache:
```bash
RC_PW=$(cat secrets/redis_cache_password.txt)
docker compose exec redis-cache \
  redis-cli -a "$RC_PW" ping
```
Expected: `PONG`.

Redis queue:
```bash
RQ_PW=$(cat secrets/redis_queue_password.txt)
docker compose exec redis-queue \
  redis-cli -a "$RQ_PW" ping
```
Expected: `PONG`.

### Step 5: Verify network segmentation (postgres NOT reachable from outside)

```bash
# This should FAIL — postgres is not on the frontend network and port 5432 is not published.
nc -zv 127.0.0.1 5432 2>&1 || echo "PASS: postgres not exposed"
```

Expected: `PASS: postgres not exposed` (or `nc` reports connection refused).

### Step 6: Mark task complete

Move to Task 7.

---

## Task 7: Frappe Services (init, web, worker, scheduler, socketio)

**Files:**
- Modify: `docker-compose.yml` — add 5 frappe services

**Interfaces:**
- `frappe-init` (profile `init`, no auto-start): runs `init.sh`, exits 0 idempotently
- `frappe-web` (gunicorn on :8000, on `backend` + `frontend`)
- `frappe-worker` (RQ worker, `backend`)
- `frappe-scheduler` (RQ beat, `backend`)
- `frappe-socketio` (Node SocketIO on :9000, `backend` + `frontend`)

### Step 1: Add the five services

Insert these under `redis-queue:` in `docker-compose.yml`:

```yaml
  frappe-init:
    <<: *frappe-image
    profiles: ["init"]
    environment:
      <<: *frappe-env-base
      FRAPPE_ROLE: init
      ADMIN_PASSWORD_FILE: /run/secrets/admin_password
      DB_PASSWORD_FILE: /run/secrets/db_password
    secrets:
      - admin_password
      - db_password
    volumes:
      - frappe_sites:/workspace/frappe-bench/sites
    networks:
      - backend
      - data
    depends_on:
      postgres:
        condition: service_healthy
      redis-cache:
        condition: service_healthy
      redis-queue:
        condition: service_healthy
    deploy:
      resources:
        limits:
          cpus: "1"
          memory: 1G

  frappe-web:
    <<: *frappe-image
    environment:
      <<: *frappe-env-base
      FRAPPE_ROLE: web
      ADMIN_PASSWORD_FILE: /run/secrets/admin_password
      DB_PASSWORD_FILE: /run/secrets/db_password
      REDIS_CACHE_PASSWORD_FILE: /run/secrets/redis_cache_password
      REDIS_QUEUE_PASSWORD_FILE: /run/secrets/redis_queue_password
    secrets:
      - admin_password
      - db_password
      - redis_cache_password
      - redis_queue_password
    command: ["frappe-web"]
    volumes:
      - frappe_sites:/workspace/frappe-bench/sites
      - frappe_assets:/workspace/frappe-bench/sites/assets
      - frappe_logs:/workspace/frappe-bench/logs
      - frappe_data:/data
    networks:
      - backend
      - frontend
    depends_on:
      frappe-init:
        condition: service_completed_successfully
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/api/method/ping"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    deploy:
      resources:
        limits:
          cpus: "2"
          memory: 2G

  frappe-worker:
    <<: *frappe-image
    environment:
      <<: *frappe-env-base
      FRAPPE_ROLE: worker
      ADMIN_PASSWORD_FILE: /run/secrets/admin_password
      DB_PASSWORD_FILE: /run/secrets/db_password
      REDIS_CACHE_PASSWORD_FILE: /run/secrets/redis_cache_password
      REDIS_QUEUE_PASSWORD_FILE: /run/secrets/redis_queue_password
    secrets:
      - admin_password
      - db_password
      - redis_cache_password
      - redis_queue_password
    command: ["frappe-worker"]
    volumes:
      - frappe_sites:/workspace/frappe-bench/sites
      - frappe_logs:/workspace/frappe-bench/logs
      - frappe_data:/data
    networks:
      - backend
    depends_on:
      frappe-init:
        condition: service_completed_successfully
      postgres:
        condition: service_healthy
    deploy:
      resources:
        limits:
          cpus: "2"
          memory: 2G

  frappe-scheduler:
    <<: *frappe-image
    environment:
      <<: *frappe-env-base
      FRAPPE_ROLE: scheduler
      ADMIN_PASSWORD_FILE: /run/secrets/admin_password
      DB_PASSWORD_FILE: /run/secrets/db_password
      REDIS_CACHE_PASSWORD_FILE: /run/secrets/redis_cache_password
      REDIS_QUEUE_PASSWORD_FILE: /run/secrets/redis_queue_password
    secrets:
      - admin_password
      - db_password
      - redis_cache_password
      - redis_queue_password
    command: ["frappe-scheduler"]
    volumes:
      - frappe_sites:/workspace/frappe-bench/sites
      - frappe_logs:/workspace/frappe-bench/logs
    networks:
      - backend
    depends_on:
      frappe-init:
        condition: service_completed_successfully
      postgres:
        condition: service_healthy
    deploy:
      resources:
        limits:
          cpus: "1"
          memory: 1G

  frappe-socketio:
    <<: *frappe-image
    environment:
      <<: *frappe-env-base
      FRAPPE_ROLE: socketio
      ADMIN_PASSWORD_FILE: /run/secrets/admin_password
      DB_PASSWORD_FILE: /run/secrets/db_password
      REDIS_CACHE_PASSWORD_FILE: /run/secrets/redis_cache_password
      REDIS_QUEUE_PASSWORD_FILE: /run/secrets/redis_queue_password
    secrets:
      - admin_password
      - db_password
      - redis_cache_password
      - redis_queue_password
    command: ["frappe-socketio"]
    volumes:
      - frappe_sites:/workspace/frappe-bench/sites
      - frappe_assets:/workspace/frappe-bench/sites/assets
      - frappe_logs:/workspace/frappe-bench/logs
    networks:
      - backend
      - frontend
    depends_on:
      frappe-web:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS 'http://localhost:9000/socket.io/?EIO=4&transport=polling' | grep -q 'sid'"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
    deploy:
      resources:
        limits:
          cpus: "1"
          memory: 1G
```

### Step 2: Validate compose

```bash
docker compose config --quiet && echo "PASS"
```

### Step 3: Run the init container

```bash
docker compose --profile init run --rm frappe-init
```

Expected: prints `[init] Creating site webodm.local on postgres://postgres:5432/webodm` followed by Frappe's bench output ending with `*** Scheduler is disabled. Site name: webodm.local` (or similar success message), and exits 0.

If it fails with Postgres connection error: check `docker compose logs postgres`. If fails with "permission denied" on secrets: `chmod 600 secrets/*`.

### Step 4: Run re-init to confirm idempotency

```bash
docker compose --profile init run --rm frappe-init
```

Expected: prints `[init] Site webodm.local already exists; skipping bootstrap.` and exits 0.

### Step 5: Start web, worker, scheduler, socketio

```bash
docker compose up -d frappe-web frappe-worker frappe-scheduler frappe-socketio
docker compose ps
```

Expected: all four with state `Up` (healthy after start_period).

### Step 6: Verify Frappe responds

```bash
docker compose exec frappe-web \
  curl -fsS http://localhost:8000/api/method/ping
```

Expected JSON: `{"message":"pong"}`.

### Step 7: Verify SocketIO responds

```bash
docker compose exec frappe-socketio \
  curl -fsS 'http://localhost:9000/socket.io/?EIO=4&transport=polling' | head -c 200
```

Expected: a JSON-ish string containing `"sid"`.

### Step 8: Mark task complete

Move to Task 8.

---

## Task 8: Geospatial Service

**Files:**
- Modify: `docker-compose.yml` — add `geospatial` service

**Interfaces:**
- `geospatial` (FastAPI on :5000, on `backend` + `frontend`, reads `/data`)

### Step 1: Add the service

Insert under `frappe-socketio:` in `docker-compose.yml`:

```yaml
  geospatial:
    build:
      context: ../webodm-geospatial
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      DATA_DIR: /data
      REDIS_URL: redis://redis-cache:13000
    volumes:
      - frappe_data:/data
    networks:
      - backend
      - frontend
    depends_on:
      redis-cache:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:5000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          cpus: "2"
          memory: 2G
```

### Step 2: Validate compose

```bash
docker compose config --quiet && echo "PASS"
```

### Step 3: Start geospatial

```bash
docker compose up -d geospatial
docker compose ps geospatial
```

Expected: state `Up (healthy)`.

### Step 4: Verify health endpoint

```bash
docker compose exec geospatial wget -qO- http://localhost:5000/health
```

Expected: `{"status":"ok","service":"webodm-geospatial"}` (verify by reading `webodm-geospatial/app/main.py` lines 26-28).

### Step 5: Mark task complete

Move to Task 9.

---

## Task 9: NodeODM Service

**Files:**
- Modify: `docker-compose.yml` — add `nodeodm` service

**Interfaces:**
- `nodeodm` (NodeODM on :3000, on `backend`)

### Step 1: Add the service

Insert under `geospatial:` in `docker-compose.yml`:

```yaml
  nodeodm:
    image: opendronemap/nodeodm:latest
    restart: unless-stopped
    volumes:
      - nodeodm_data:/var/www/data
    networks:
      - backend
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:3000/info"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

### Step 2: Validate compose

```bash
docker compose config --quiet && echo "PASS"
```

### Step 3: Start NodeODM

```bash
docker compose up -d nodeodm
```

Expected: pulls image (~300MB), then state `Up` (healthy after start_period ~60 sec).

### Step 4: Verify

```bash
docker compose exec nodeodm curl -fsS http://localhost:3000/info
```

Expected: JSON with at least `{"version": "..."}` field.

### Step 5: Verify Frappe can reach NodeODM

```bash
docker compose exec frappe-web \
  curl -fsS http://nodeodm:3000/info
```

Expected: same JSON as step 4. This confirms the cross-container DNS works.

### Step 6: Register NodeODM in Frappe

If a NodeODM entry exists already in the DB, update its hostname; otherwise create one.

```bash
docker compose exec frappe-web bench --site webodm.local console <<'EOF'
EOF
```

Better — run a script:
```bash
docker compose exec -T frappe-web python3 - <<'EOF'
import frappe
frappe.connect(site='webodm.local')
node = frappe.get_all('WebODM Processing Node', fields=['name', 'hostname'])
print('Existing:', node)
EOF
```

Expected: prints existing entries (probably empty for a fresh site). Then register:
```bash
docker compose exec -T frappe-web python3 - <<'EOF'
import frappe
frappe.connect(site='webodm.local')
if not frappe.db.exists('WebODM Processing Node', 'Local NodeODM'):
    doc = frappe.get_doc({
        'doctype': 'WebODM Processing Node',
        'node_name': 'Local NodeODM',
        'hostname': 'nodeodm',
        'port': 3000,
        'engine': 'odm',
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    print('CREATED Local NodeODM -> nodeodm:3000')
else:
    frappe.db.set_value('WebODM Processing Node', 'Local NodeODM', 'hostname', 'nodeodm')
    frappe.db.set_value('WebODM Processing Node', 'Local NodeODM', 'port', 3000)
    frappe.db.commit()
    print('UPDATED Local NodeODM -> nodeodm:3000')
EOF
```

Expected: prints either `CREATED ...` or `UPDATED ...`.

### Step 7: Mark task complete

Move to Task 10.

---

## Task 10: Bring up Caddy + verify TLS

**Files:** none (Task 5 already added caddy)

**Interfaces:** Caddy reachable on host ports 80 and 443, terminating TLS for `$SITE_DOMAIN`.

### Step 1: Start Caddy

```bash
docker compose up -d caddy
docker compose ps caddy
```

Expected: state `Up (healthy)`.

### Step 2: Watch TLS cert issuance

```bash
docker compose logs -f caddy
```

Expected within ~60 seconds: lines like
```
[INFO] Obtaining a new certificate for ${SITE_DOMAIN}
[INFO] Certificate obtained successfully
```

Press `Ctrl-C` to exit logs.

### Step 3: Verify HTTPS works (using a real domain in `.env`)

If `SITE_DOMAIN` is a real FQDN with DNS pointing to this host:
```bash
curl -fsSLI https://${SITE_DOMAIN}/api/method/ping
```
Expected: `HTTP/2 200` with HSTS header.

### Step 4: Verify HTTPS works (using local-only `webodm.local`)

For local dev with `SITE_DOMAIN=webodm.local` (Caddy self-signed):
```bash
# 1) Add to /etc/hosts:
echo "127.0.0.1 webodm.local" | sudo tee -a /etc/hosts
# 2) Use -k to skip cert verification (self-signed):
curl -kfsSLI https://webodm.local/api/method/ping
```
Expected: `HTTP/2 200`.

### Step 5: Verify security headers

```bash
curl -kfsSI https://webodm.local/api/method/ping | grep -iE '^(strict-transport|x-content|x-frame|referrer|permissions)'
```

Expected: at least 4 of these 5 headers present.

### Step 6: Verify rate limit on login

```bash
for i in $(seq 1 55); do
  curl -k -s -o /dev/null -w '%{http_code} ' \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"usr":"x","pwd":"y"}' \
    https://webodm.local/api/method/login
done
```

Expected: mostly `200` or `401`, but a few `429` after ~50 requests.

### Step 7: Mark task complete

Move to Task 11.

---

## Task 11: Backup Service

**Files:**
- Modify: `docker-compose.yml` — add `backup` service
- Modify: `infra/backup/backup.sh` — adjust paths if needed after first run

**Interfaces:**
- `backup` runs cron; executes `backup.sh` at `$BACKUP_SCHEDULE`
- Local backups in `/backups` (named volume `backup_storage`)

### Step 1: Add the service

Insert under `nodeodm:` in `docker-compose.yml`:

```yaml
  backup:
    image: webodm-backup:1
    build:
      context: ./infra/backup
    restart: unless-stopped
    environment:
      SITE_NAME: ${SITE_NAME:-webodm.local}
      BACKUP_SCHEDULE: ${BACKUP_SCHEDULE:-0 3 * * *}
      BACKUP_RETENTION_DAYS: ${BACKUP_RETENTION_DAYS:-14}
      BACKUP_S3_BUCKET: ${BACKUP_S3_BUCKET:-}
      BACKUP_S3_ACCESS_KEY: ${BACKUP_S3_ACCESS_KEY:-}
      BACKUP_S3_SECRET_KEY: ${BACKUP_S3_SECRET_KEY:-}
      BACKUP_S3_ENDPOINT: ${BACKUP_S3_ENDPOINT:-}
    volumes:
      - frappe_sites:/workspace/frappe-bench/sites:ro
      - frappe_data:/data:ro
      - backup_storage:/backups
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - backend
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
```

### Step 2: Fix the backup script's compose root path

`backup.sh` (from Task 4) `cd`s into `/compose-root || /workspace`. The container has no such dirs. Either:
- (a) Use `/host-root` and bind-mount the compose root, or
- (b) Use the docker compose socket (already mounted) with `docker compose --project-directory /host ...`.

Edit `infra/backup/backup.sh` — replace the `cd /compose-root || cd /workspace` line and the `docker compose exec` invocation:

```bash
sed -i 's|^cd /compose-root || cd /workspace$|cd /host|' infra/backup/backup.sh
sed -i 's|^docker compose exec -T frappe-web$|docker compose --project-directory /host exec -T frappe-web|' infra/backup/backup.sh
```

Add the `/host` bind-mount in the service definition: modify the `volumes:` block to add
```yaml
      - .:/host:ro
```
(Insert above the existing `/var/run/docker.sock` line.)

Then rebuild:
```bash
docker compose build backup
```

### Step 3: Validate and start

```bash
docker compose config --quiet && echo "PASS"
docker compose up -d backup
docker compose ps backup
```

Expected: state `Up`.

### Step 4: Manually trigger a backup

```bash
docker compose exec backup /usr/local/bin/backup.sh
docker compose exec backup ls -l /backups
```

Expected: a recent timestamped file pair (`.sql.gz` and `-files.tar.gz`) appears in `/backups`. The first one or two attempts may take 1-2 minutes as Frappe prepares the dump.

If the backup script reports "WARN: bench backup returned non-zero", inspect with:
```bash
docker compose logs backup
docker compose exec frappe-web bench --site webodm.local backup --with-files --with-private-files
```

### Step 5: Confirm cron is scheduled

```bash
docker compose exec backup crontab -l
docker compose exec backup bash -c 'cat /etc/cron.d/webodm-backup'
```

Expected: shows the schedule line `0 3 * * * /usr/local/bin/backup.sh ...`.

### Step 6: Mark task complete

Move to Task 12.

---

## Task 12: Migration Runbook + Final Smoke Test

**Files:**
- Create: `docs/migration-from-host.md`
- Create: `docs/runbook.md`

**Interfaces:** markdown docs that an operator can follow end-to-end.

### Step 1: Create the migration runbook

```bash
mkdir -p docs
cat > docs/migration-from-host.md <<'EOF'
# Migrating from Host-Based Deployment to Docker Compose

## When to use this

You have an existing Frappe bench installed on the host (the legacy `docker-compose.yml` was only running postgres and redis on the host; bench was started manually with `bench start`). You want to move the bench into the container stack without losing data.

## Steps

### 1. Stop the host-side Frappe services

```bash
# If using Procfile-based dev:
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench
# Stop gunicorn / socketio / worker / scheduler / watch processes
# Easiest: kill the supervisord process if used, or pkill -f 'bench serve'
pkill -f 'bench serve' || true
pkill -f 'bench worker' || true
pkill -f 'bench schedule' || true
pkill -f 'bench socketio' || true
```

### 2. Take a backup on the host

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench
bench --site webodm.local backup --with-files --with-private-files
ls -l sites/webodm.local/private/backups/
```

Note the latest timestamped files:
- `<timestamp>-webodm-local-database.sql.gz`
- `<timestamp>-webodm-local-files.tar.gz`

### 3. Copy the backup into the compose stack's migration area

```bash
cd /home/ridwan/workspaces/frappe-webodm
mkdir -p migration
cp frappe-bench/sites/webodm.local/private/backups/<timestamp>-webodm-local-* migration/
ls -l migration/
```

### 4. Bring up only the data tier first

```bash
docker compose up -d postgres redis-cache redis-queue
docker compose ps   # all three should be 'Up (healthy)'
```

### 5. Run the init container (creates an empty site)

```bash
docker compose --profile init run --rm frappe-init
```

This creates an empty `webodm.local` site and installs both apps.

### 6. Restore the backup into the empty site

```bash
docker compose exec -T frappe-web \
  bench --site webodm.local restore \
    /workspace/frappe-bench/sites/webodm.local/private/backups/migration/<timestamp>-webodm-local-database.sql.gz \
    --with-private-files /workspace/frappe-bench/sites/webodm.local/private/backups/migration/<timestamp>-webodm-local-files.tar.gz
```

### 7. Update `site_config.json` to point at the container DB

If your restored `site_config.json` still has `db_host: 127.0.0.1`:

```bash
docker compose exec -T frappe-web \
  bench --site webodm.local set-config db_host postgres
```

### 8. Restart the Frappe stack

```bash
docker compose up -d frappe-web frappe-worker frappe-scheduler frappe-socketio
docker compose restart frappe-web
```

### 9. Verify

```bash
curl -kfsSL https://webodm.local/api/method/ping
docker compose exec frappe-web bench --site webodm.local doctor
```

Login at `https://webodm.local/` with `Administrator` and your existing admin password. If you forgot the password, reset:

```bash
docker compose exec -T frappe-web \
  bench --site webodm.local set-admin-password 'new-password-here'
```

## Rollback

If migration fails, the host-side bench is untouched (you only `pkill`ed its processes; the `frappe-bench/sites/` directory still exists on disk). To roll back:

```bash
# On the host:
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench
bench serve --port 8080 &
bench worker &
bench schedule &
bench socketio &
```

Re-restore the most recent host backup if you corrupted it. The host's `site_config.json` is unchanged by the migration.
EOF
```

### Step 2: Create the operational runbook

```bash
cat > docs/runbook.md <<'EOF'
# WebODM Docker Compose — Operational Runbook

## First-time setup on a fresh host

```bash
# 1. Clone the repo
git clone <repo-url> /opt/webodm
cd /opt/webodm

# 2. Generate secrets (one-time)
./scripts/init-secrets.sh

# 3. Configure
cp .env.example .env
$EDITOR .env    # set SITE_DOMAIN, DNS provider token, backup target, etc.

# 4. Build images
docker compose build

# 5. Start data tier
docker compose up -d postgres redis-cache redis-queue

# 6. Bootstrap the site (one-time)
docker compose --profile init run --rm frappe-init

# 7. Start the rest
docker compose up -d

# 8. Watch TLS
docker compose logs -f caddy
```

## Day-to-day operations

| Task | Command |
|---|---|
| Status of all services | `docker compose ps` |
| Tail Frappe web logs | `docker compose logs -f --tail=200 frappe-web` |
| Restart web | `docker compose restart frappe-web` |
| Open bench console | `docker compose exec frappe-web bench --site ${SITE_NAME} console` |
| Run Frappe doctor | `docker compose exec frappe-web bench --site ${SITE_NAME} doctor` |
| Trigger backup now | `docker compose exec backup /usr/local/bin/backup.sh` |
| List local backups | `docker compose exec backup ls -l /backups` |
| Migrate site | See `docs/migration-from-host.md` |
| Update custom images | `docker compose build && docker compose up -d` |
| Pull upstream images | `docker compose pull` |

## Troubleshooting

### Frappe can't reach Redis

Check Redis logs:
```bash
docker compose logs redis-cache redis-queue
```
Verify password files:
```bash
docker compose exec redis-cache \
  sh -c 'redis-cli -a "$(cat /run/secrets/redis_cache_password)" ping'
```

### TLS cert not issued

Caddy logs show the ACME flow. For DNS-01:
```bash
docker compose logs caddy | grep -i 'certificate\|acme\|dns'
```
Make sure `CLOUDFLARE_API_TOKEN` (or AWS keys) are set in `.env` and the token has DNS edit permission for the zone.

### Frappe web returns 500

```bash
docker compose logs --tail=500 frappe-web
docker compose exec frappe-web tail -100 logs/web.error.log
```

### Worker not picking up jobs

```bash
docker compose logs --tail=200 frappe-worker
docker compose exec frappe-worker \
  sh -c 'redis-cli -a "$(cat /run/secrets/redis_queue_password)" -h redis-queue LLEN rq:queue:default'
```

### Backup fails

```bash
docker compose logs backup
docker compose exec backup /usr/local/bin/backup.sh  # manual trigger
```

### Reset the site (destructive)

```bash
# WARNING: drops the entire database.
docker compose down
docker volume rm webodm_postgres_data webodm_frappe_sites
docker compose up -d postgres redis-cache redis-queue
docker compose --profile init run --rm frappe-init
docker compose up -d
```

## Capacity planning

Single-VPS sizing reference for MVP:
- 8 vCPU, 16 GB RAM comfortably fits the limits in §9 of the design spec.
- Image storage: 5 GB for Frappe image, 1 GB for Caddy image, 1 GB for backup image, 500 MB for the rest. Add ~10 GB for Postgres + 5 GB for `/data`.
- Backups: 1 GB per day with `--with-files --with-private-files`; multiply by `BACKUP_RETENTION_DAYS`.
EOF
```

### Step 3: Final end-to-end smoke test

```bash
# Stop everything, start fresh:
docker compose down -v
docker compose build
docker compose up -d postgres redis-cache redis-queue
sleep 10
docker compose --profile init run --rm frappe-init
docker compose up -d
docker compose ps

# Wait for healthy
sleep 30
docker compose ps

# Tests:
echo "--- ping ---"
docker compose exec frappe-web curl -fsS http://localhost:8000/api/method/ping
echo "--- https ---"
curl -kfsSI https://webodm.local/api/method/ping
echo "--- backup ---"
docker compose exec backup /usr/local/bin/backup.sh
docker compose exec backup ls -l /backups | tail -3
echo "--- nodeodm reachable from frappe ---"
docker compose exec frappe-web curl -fsS http://nodeodm:3000/info | head -c 200
echo
echo "--- geospatial ---"
docker compose exec geospatial wget -qO- http://localhost:5000/health
```

All five lines above should print non-error output.

### Step 4: Mark task complete

Implementation plan is fully executed. Move to the optional review step or handoff.

---

## Self-Review

### Spec coverage map

| Spec section | Covered in task |
|---|---|
| §1 Goals (split processes) | Task 7 |
| §1 Goals (Caddy + TLS) | Tasks 3, 5, 10 |
| §1 Goals (idempotent init) | Tasks 2 (init.sh), 7 (compose init svc), 7 (Step 4 re-init test) |
| §1 Goals (Docker secrets) | Tasks 1, 6, 7 |
| §1 Goals (3-tier network) | Task 5 |
| §1 Goals (migration path) | Task 12 |
| §2.1 Topology | Tasks 5-11 (all services defined) |
| §2.2 Networks | Task 5 |
| §2.3 Service inventory | Tasks 5-11 |
| §3 Frappe image | Task 2 |
| §4.1 Caddy image | Task 3 |
| §4.2 Caddyfile | Task 3 |
| §4.3 TLS strategy | Task 10 |
| §5 Init container | Task 7 |
| §6 Secrets | Tasks 1, 6, 7 |
| §7 Volumes | Tasks 5-11 |
| §8 Healthchecks | Tasks 6-10 |
| §9 Resource limits | Tasks 5-11 |
| §10 Backup strategy | Tasks 4, 11 |
| §11 Migration runbook | Task 12 |
| §12 Operational runbook | Task 12 |

### Placeholder scan

No `TODO`, `TBD`, "fill in", "implement later" markers. All commands, file contents, and expected outputs are concrete.

### Type / signature consistency

- `entrypoint.sh` switch on `FRAPPE_ROLE`: same values used in compose `environment.FRAPPE_ROLE` for all 5 services (`init`, `web`, `worker`, `scheduler`, `socketio`).
- `init.sh` env vars (`SITE_NAME`, `DB_HOST`, etc.) match the keys set in compose `x-frappe-env-base` and the per-service overrides.
- Compose `x-frappe-image` used by all 5 frappe services with consistent `secrets:` and `volumes:`.

### Coverage: PASS. No gaps detected.
