# Migrating from Host-Based Deployment to Docker Compose

## When to use this

You have an existing Frappe bench installed on the host (the legacy `docker-compose.yml`
was only running postgres and redis on the host; bench was started manually with
`bench start` or `bench serve` / `bench worker` / `bench schedule` / `bench socketio`).
You want to move the bench into the container stack **without losing data**.

The migration is one-way: the host-side bench is left untouched on disk so you can roll
back at any point (see [Rollback](#rollback)).

## Prerequisites

- Host-side `frappe-bench/` directory still exists (check `ls frappe-bench/sites/`).
- The Docker Compose stack is configured at the repo root (this directory).
- `docker compose --profile init config --quiet` exits 0 (compose file is valid).
- You have shell access as the user that owns `frappe-bench/`.

The backup file format is the one produced by `bench backup --with-files` (database dump
as `.sql.gz`; public files as `*-files.tar`; private files as `*-private-files.tar`;
site_config snapshot as `*-site_config_backup.json`). If your existing host-side Frappe
is on v13 or earlier and uses a different layout, see [Notes for older Frappe versions](#notes-for-older-frappe-versions).

## Steps

### 1. Stop the host-side Frappe services

Stop any host-side processes that bind to the same ports (`8000`, `9000`) — otherwise
the compose stack will fail healthchecks or hit port conflicts.

```bash
# If you used bench start (Procfile / honcho):
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench
pkill -f 'bench serve'   || true
pkill -f 'bench worker'  || true
pkill -f 'bench schedule' || true
pkill -f 'bench socketio' || true

# If you ran gunicorn directly:
pkill -f 'gunicorn.*frappe.app' || true

# If you used supervisord:
sudo supervisorctl status     # list all webodm programs
sudo supervisorctl stop all
```

Verify nothing is bound anymore:

```bash
ss -ltnp | grep -E ':(8000|9000) ' || echo "OK: ports 8000/9000 are free"
```

### 2. Take a backup on the host

```bash
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench
source env/bin/activate
bench --site webodm.local backup --with-files
ls -l sites/webodm.local/private/backups/
```

You should see a fresh `<timestamp>-webodm-local-*.{sql.gz,files.tar,private-files.tar,site_config_backup.json}`
set. Note the full timestamp prefix (e.g. `20260819_004608`).

> **Note:** `bench backup` in Frappe v16 has dropped `--with-private-files`. The
> `--with-files` flag already includes private files (the `*-private-files.tar` blob).

### 3. Copy the backup into a staging area

The compose stack's `frappe-init` restore path expects backups under
`/workspace/frappe-bench/sites/<site>/private/backups/` inside the `frappe-init` /
`frappe-web` container. The cleanest way to get them there is to drop them into a
bind-mounted host directory and `docker cp` them in.

```bash
cd /home/ridwan/workspaces/frappe-webodm
mkdir -p migration
TS=20260819_004608                # <- the timestamp from Step 2
cp frappe-bench/sites/webodm.local/private/backups/${TS}-webodm-local-database.sql.gz \
   frappe-bench/sites/webodm.local/private/backups/${TS}-webodm-local-files.tar \
   frappe-bench/sites/webodm.local/private/backups/${TS}-webodm-local-private-files.tar \
   frappe-bench/sites/webodm.local/private/backups/${TS}-webodm-local-site_config_backup.json \
   migration/
ls -l migration/
```

### 4. Bring up only the data tier first

```bash
docker compose --profile init up -d postgres redis-cache redis-queue
docker compose --profile init ps   # all three should be 'Up (healthy)'
```

Wait ~10 seconds for healthchecks to flip from `starting` to `healthy`:

```bash
docker compose --profile init ps --format '{{.Service}}\t{{.Status}}' \
  | grep -E 'postgres|redis'
```

### 5. Run the init container (creates an empty site)

> **Important:** This step wipes the application database (`webodm`) inside the container
> postgres. It does **not** touch the host-side `frappe-bench/sites/webodm.local/` —
> that's what we're migrating from.

```bash
docker compose --profile init run --rm frappe-init
```

This creates an empty `webodm.local` site and installs `webodm_core` and `webodm_frontend`.
The idempotency guard in `infra/frappe/init.sh` will skip on a re-run — if you need to
force-recreate, delete the `frappe_sites` named volume first:

```bash
docker compose --profile init down
docker volume rm webodm_frappe_sites
docker compose --profile init up -d postgres redis-cache redis-queue
docker compose --profile init run --rm frappe-init
```

### 6. Restore the backup into the empty site

The fresh `frappe-init` container ran in Step 5 created an empty `webodm.local`
site directory on the `frappe_sites` named volume. The restore needs to run
inside a container that has access to that same volume AND has the `bench`
CLI on PATH — the `frappe-init` image is the right one (it has both).

```bash
cd /home/ridwan/workspaces/frappe-webodm
TS=20260819_004608                # the timestamp captured in Step 2

# Start frappe-init as a long-running container (not `run --rm`) so we can
# `docker cp` into it AND `docker exec` bench restore.
docker compose --profile init up -d frappe-init

# Copy the four backup files into the container. The container's
# /workspace/frappe-bench/sites/webodm.local/private/backups/ already exists
# (Step 5 created it during bench new-site).
docker compose --profile init cp migration/${TS}-webodm-local-database.sql.gz \
  frappe-init:/workspace/frappe-bench/sites/webodm.local/private/backups/
docker compose --profile init cp migration/${TS}-webodm-local-files.tar \
  frappe-init:/workspace/frappe-bench/sites/webodm.local/private/backups/
docker compose --profile init cp migration/${TS}-webodm-local-private-files.tar \
  frappe-init:/workspace/frappe-bench/sites/webodm.local/private/backups/
docker compose --profile init cp migration/${TS}-webodm-local-site_config_backup.json \
  frappe-init:/workspace/frappe-bench/sites/webodm.local/private/backups/

# Now run the restore via docker exec into the same container. The restore
# wipes the empty site created in Step 5 and replaces it with the backed-up one.
# Note: --with-private-files is implicit in modern bench restore when --with-files
# is passed (Frappe v13+ bundles both into --with-files from --with-files).
docker compose --profile init exec -T frappe-init \
  bench --site webodm.local restore \
    /workspace/frappe-bench/sites/webodm.local/private/backups/${TS}-webodm-local-database.sql.gz \
    --with-private-files /workspace/frappe-bench/sites/webodm.local/private/backups/${TS}-webodm-local-private-files.tar
```

(`bench restore` errors out if you try to restore into a non-empty site. If you see
"IntegrityError" or "table already exists", drop the application DB first via
`docker compose --profile init exec -T postgres psql -U webodm -d postgres -c "DROP DATABASE webodm;"`
and re-run `frappe-init` from Step 5.)

Stop the disposable `frappe-init` container so it doesn't block the long-running
`frappe-web`'s `depends_on: service_completed_successfully` check at startup:

```bash
docker compose --profile init stop frappe-init
```

### 7. Update `site_config.json` to point at the container DB

If your restored `site_config.json` still has `db_host: 127.0.0.1`, the Frappe web
container will try to talk to a postgres on its own loopback and fail. Verify
and override:

```bash
# Inspect the restored config to see what hostname / port the backup wrote:
docker compose --profile init run --rm --no-deps frappe-init \
  python3 -c "import json,sys; print(json.dumps(json.load(open('/workspace/frappe-bench/sites/webodm.local/site_config.json'))))"

# If db_host is anything other than 'postgres', fix it:
docker compose --profile init run --rm --no-deps frappe-init \
  bench --site webodm.local set-config db_host postgres

# Same for db_port (should be 5432):
docker compose --profile init run --rm --no-deps frappe-init \
  bench --site webodm.local set-config db_port 5432
```

`db_name`, `db_user`, and `db_password` are usually fine — the restore step
overwrites `site_config.json` with the values from the backup, and the postgres
container's `POSTGRES_DB` and `POSTGRES_USER` env vars match the host-side
defaults. The `db_password` in the file is the same as the `POSTGRES_PASSWORD`
in the new container (set via Docker secret `db_password` → `POSTGRES_PASSWORD_FILE`),
so the application can connect.

> **If the host-side Frappe used a different DB password**, generate the matching
> secret before bringing up the stack:
> ```bash
> echo -n 'your-original-db-password' > secrets/db_password.txt
> chmod 600 secrets/db_password.txt
> ```

### 8. Run `bench migrate` before starting the scheduler

After a restore, Frappe's scheduler reads `scheduled_job_log` and may fire a
flood of catch-up jobs. Run `bench migrate` to clear them, then start the stack:

```bash
# Migrate from inside frappe-init (the disposable container has bench on PATH).
docker compose --profile init run --rm --no-deps frappe-init \
  bench --site webodm.local migrate

# Now bring up the long-running services.
docker compose --profile init up -d frappe-web frappe-worker frappe-scheduler frappe-socketio
```

`frappe-web` has a healthcheck on `http://localhost:8000/api/method/ping`; wait
until it flips to `(healthy)`:

```bash
docker compose --profile init ps frappe-web
# ... Up X minutes (healthy)
```

### 9. Verify

```bash
# Direct probe to the web container (bypasses Caddy, no TLS):
docker compose --profile init exec -T frappe-web \
  curl -fsS http://localhost:8000/api/method/ping
# {"message":"pong"}

# Through Caddy (HTTPS, self-signed for webodm.local):
curl -kfsSL https://webodm.local/api/method/ping
# {"message":"pong"}
```

Login at `https://webodm.local/` with `Administrator` and your existing admin password.
If you forgot it, reset:

```bash
docker compose --profile init exec -T frappe-web \
  bench --site webodm.local set-admin-password 'new-password-here'
```

### 10. Confirm scheduler and worker are picking up jobs

```bash
docker compose --profile init logs --tail=100 frappe-scheduler
RC=$(cat secrets/redis_cache_password.txt)
docker compose --profile init exec -T redis-cache \
  redis-cli -p 13000 -a "$RC" LLEN rq:queue:default
```

The `LLEN` should be `0` (or growing slowly) — Frappe dispatches jobs via the
RQ queue at `redis://redis-queue:11000` (use `redis-cli -p 11000 -a <password>` on
`redis-queue` to check that queue).

## Rollback

If migration fails, the host-side bench is untouched (you only `pkill`ed its processes;
the `frappe-bench/sites/` directory still exists on disk). To roll back:

```bash
# Stop the compose stack (do NOT use -v, that would wipe the host volumes)
docker compose --profile init down

# On the host:
cd /home/ridwan/workspaces/frappe-webodm/frappe-bench
source env/bin/activate
bench serve --port 8000 &
bench worker --queue default,long,short &
bench schedule &
bench socketio --port 9000 &
```

The host's `site_config.json` is unchanged by the migration; nothing on the host's
side has been written to. The Frappe database inside the container is gone, but
you can re-restore from the same backup file you used in Step 6.

## Notes for older Frappe versions

If your host-side bench is on Frappe v13 or earlier:

- The backup file extension may be `.sql` (uncompressed) instead of `.sql.gz`. Add
  `--compress` to `bench backup` in Step 2 or set `FRAPPE_BACKUP_COMPRESSION=gzip`.
- The `*-private-files.tar` blob may not exist — `--with-files` on v13 only backs up
  public files. Use the manual `tar -C sites/<site>/private/files` workflow and
  skip the `--with-private-files` flag in Step 6's `bench restore`.
- `bench restore` on v13 may require `--force` to overwrite the empty site.
- The default site on older bench installs is `site1.local`, not `webodm.local` —
  swap `SITE_NAME=webodm.local` for your actual site name throughout.
