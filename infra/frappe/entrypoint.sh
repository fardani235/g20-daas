#!/usr/bin/env bash
# Container entrypoint for every Frappe role (init / web / worker / scheduler /
# socketio). This file is bind-mounted from the repo by docker-compose.yml and
# also baked into the image as a fallback, so fixes here deploy without an
# image rebuild.
#
# Responsibilities, in order:
#   1. Resolve *_PASSWORD_FILE secrets into env vars.
#   2. Build FRAPPE_REDIS_CACHE / FRAPPE_REDIS_QUEUE with the auth embedded
#      (Frappe reads these env vars; compose cannot interpolate secret files).
#   3. Bridge the paths Frappe/Bench resolve relative to CWD onto the sites
#      volume (logs, assets, apps.txt, common_site_config.json, per-site dirs).
#   4. Upsert common_site_config.json / site_config.json (configure_site.py).
#   5. exec the role's process.
set -euo pipefail

role="${FRAPPE_ROLE:-web}"
echo "[entrypoint] FRAPPE_ROLE=${role}"

BENCH=/workspace/frappe-bench
SITES="${BENCH}/sites"
INFRA=/workspace/infra/frappe

# --- 1. secrets -------------------------------------------------------------
for var in DB_PASSWORD ADMIN_PASSWORD REDIS_CACHE_PASSWORD REDIS_QUEUE_PASSWORD FRAPPE_ROOT_PASSWORD; do
  file_var="${var}_FILE"
  if [ -n "${!file_var:-}" ] && [ -f "${!file_var}" ]; then
    export "$var=$(cat "${!file_var}")"
  fi
done
: "${SITE_NAME:?SITE_NAME must be set}"
: "${DB_USER:?DB_USER must be set}"
: "${DB_PASSWORD:?DB_PASSWORD (or DB_PASSWORD_FILE) must be set}"
: "${REDIS_CACHE_PASSWORD:?REDIS_CACHE_PASSWORD (or _FILE) must be set}"
: "${REDIS_QUEUE_PASSWORD:?REDIS_QUEUE_PASSWORD (or _FILE) must be set}"
: "${FRAPPE_ROOT_PASSWORD:?FRAPPE_ROOT_PASSWORD (or _FILE) must be set}"
export FRAPPE_ROOT_USER="${FRAPPE_ROOT_USER:-frappe_admin}"

# --- 2. redis URLs ----------------------------------------------------------
urlenc() { python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""), end="")' "$1"; }
export FRAPPE_REDIS_CACHE="redis://:$(urlenc "$REDIS_CACHE_PASSWORD")@${REDIS_CACHE_HOST:-redis-cache}:${REDIS_CACHE_PORT:-13000}"
export FRAPPE_REDIS_QUEUE="redis://:$(urlenc "$REDIS_QUEUE_PASSWORD")@${REDIS_QUEUE_HOST:-redis-queue}:${REDIS_QUEUE_PORT:-11000}"

# --- 3. path bridges --------------------------------------------------------
# Frappe writes logs to ../logs relative to the bench, reads assets/assets.json,
# apps.txt and common_site_config.json relative to CWD, and builds per-site log
# paths as <sitename>/logs relative to CWD. The volume layout puts all of those
# under sites/, so alias them.
mkdir -p "$SITES"
ln -sfn "${BENCH}/logs" /workspace/logs
ln -sfn "${SITES}/assets" "${BENCH}/assets"
ln -sfn "${SITES}/apps.txt" "${BENCH}/apps.txt"
ln -sfn "${SITES}/apps.json" "${BENCH}/apps.json"
ln -sfn "${SITES}/common_site_config.json" "${BENCH}/common_site_config.json"
if [ -f "${SITES}/${SITE_NAME}/site_config.json" ]; then
  for alias in "${SITE_NAME}" localhost; do
    mkdir -p "${SITES}/${alias}/logs" 2>/dev/null || true
    ln -sfn "${SITES}/${SITE_NAME}" "${BENCH}/${alias}"
  done
  # Direct requests with Host: localhost (health checks, curl on the gunicorn
  # port) need a "localhost" site; Frappe has no unknown-host fallback.
  if [ ! -L "${SITES}/localhost" ]; then
    rm -rf "${SITES}/localhost"
    ln -sfn "${SITE_NAME}" "${SITES}/localhost"
  fi
fi

# --- 4. site config ---------------------------------------------------------
SITES_PATH="$SITES" python3 "${INFRA}/configure_site.py"

cd "$BENCH"

# --- 5. role ----------------------------------------------------------------
case "$role" in
  init)
    exec "${INFRA}/init.sh"
    ;;
  web)
    # Refresh sites/assets from the image's baked copy and drop the cached
    # asset manifest, so a rebuilt image's hashes are used (else CSS 404s).
    "${BENCH}/env/bin/python" "${INFRA}/prepare_assets.py" || true
    # `frappe.app:application` lacks the static middleware that `bench serve`
    # installs; infra_frappe_wsgi wraps it so /assets and /files are served
    # from disk and SPA history-mode routes fall back to index.html.
    install -m 0644 "${INFRA}/wsgi.py" "${BENCH}/infra_frappe_wsgi.py"
    exec gunicorn \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --worker-class sync \
      --timeout "${GUNICORN_TIMEOUT:-120}" \
      --access-logfile - \
      --error-logfile - \
      infra_frappe_wsgi:application
    ;;
  worker)
    exec bench worker --queue default,long,short
    ;;
  scheduler)
    exec bench schedule
    ;;
  socketio)
    # `bench socketio` takes no --port flag; realtime/index.js reads
    # FRAPPE_SOCKETIO_PORT (or conf.socketio_port).
    export FRAPPE_SOCKETIO_PORT="${FRAPPE_SOCKETIO_PORT:-9000}"
    exec bench socketio
    ;;
  *)
    echo "Unknown FRAPPE_ROLE: $role" >&2
    exit 1
    ;;
esac
