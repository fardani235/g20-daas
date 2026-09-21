#!/usr/bin/env bash
# Container entrypoint for every Frappe role (init | web | worker | scheduler | socketio | exec).
#
# Frappe assumes its working directory is the *sites* directory: it reads
# `apps.txt`, `assets/assets.json` and `<site>/logs/` relative to CWD and writes
# process logs to `../logs`. Every role below therefore runs from
# $BENCH/sites -- the same thing `bench start`/supervisor do -- instead of
# mirroring that tree at the bench root with symlinks.
set -euo pipefail

BENCH=/workspace/frappe-bench
SITES=$BENCH/sites
PY=$BENCH/env/bin/python
FRAPPE_CLI=("$PY" -m frappe.utils.bench_helper frappe)   # what `bench <cmd>` execs
role="${FRAPPE_ROLE:-web}"
echo "[entrypoint] FRAPPE_ROLE=${role}"

# --- 1. Secrets: resolve *_FILE (Docker secrets) into plain env vars --------
for var in DB_PASSWORD ADMIN_PASSWORD REDIS_CACHE_PASSWORD REDIS_QUEUE_PASSWORD FRAPPE_ROOT_PASSWORD; do
  file_var="${var}_FILE"
  if [ -n "${!file_var:-}" ]; then
    if [ ! -r "${!file_var}" ]; then
      echo "[entrypoint] ${file_var}=${!file_var} is not readable" >&2
      exit 1
    fi
    export "$var=$(cat "${!file_var}")"
  fi
done
: "${SITE_NAME:?SITE_NAME is required}"
: "${DB_PASSWORD:?DB_PASSWORD (or DB_PASSWORD_FILE) is required}"
: "${FRAPPE_ROOT_PASSWORD:?FRAPPE_ROOT_PASSWORD (or _FILE) is required}"
export FRAPPE_ROOT_USER="${FRAPPE_ROOT_USER:-frappe_admin}"
export FRAPPE_BENCH_ROOT="$BENCH"
export SITES_PATH="$SITES"
# Pin every request to the deployment's single site regardless of Host header
# (see infra/frappe/wsgi.py and frappe/node_utils.js).
export FRAPPE_SITE="${FRAPPE_SITE:-$SITE_NAME}"

# --- 2. Redis URLs with embedded, URL-encoded passwords --------------------
urlq() { "$PY" -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""), end="")' "$1"; }
redis_url() { # host port password
  if [ -n "$3" ]; then printf 'redis://:%s@%s:%s' "$(urlq "$3")" "$1" "$2"; else printf 'redis://%s:%s' "$1" "$2"; fi
}
export FRAPPE_REDIS_CACHE="$(redis_url "${REDIS_CACHE_HOST:-redis-cache}" "${REDIS_CACHE_PORT:-13000}" "${REDIS_CACHE_PASSWORD:-}")"
export FRAPPE_REDIS_QUEUE="$(redis_url "${REDIS_QUEUE_HOST:-redis-queue}" "${REDIS_QUEUE_PORT:-11000}" "${REDIS_QUEUE_PASSWORD:-}")"
export FRAPPE_SOCKETIO_PORT="${FRAPPE_SOCKETIO_PORT:-9000}"

# --- 3. Bench/site config files (idempotent) --------------------------------
mkdir -p "$SITES" "$BENCH/logs"
"$PY" /workspace/infra/frappe/configure_site.py

# --- 4. Role dispatch -------------------------------------------------------
cd "$SITES"

case "$role" in
  init)
    exec /workspace/infra/frappe/init.sh
    ;;
  web)
    # Refresh sites/assets from the image's baked copy and drop the cached
    # asset manifest, so a rebuilt image's hashes are used (else CSS 404s).
    "$PY" /workspace/infra/frappe/prepare_assets.py || true
    # infra/frappe/wsgi.py wraps frappe.app with the static middleware that
    # `frappe.app:application` alone lacks (only the werkzeug dev server
    # installs it), plus the SPA history-mode fallback.
    exec "$BENCH/env/bin/gunicorn" \
      --chdir "$SITES" \
      --pythonpath /workspace/infra/frappe \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --worker-class sync \
      --timeout "${GUNICORN_TIMEOUT:-120}" \
      --access-logfile - \
      --error-logfile - \
      wsgi:application
    ;;
  worker)
    exec "${FRAPPE_CLI[@]}" worker --queue "${FRAPPE_WORKER_QUEUES:-default,long,short}"
    ;;
  scheduler)
    exec "${FRAPPE_CLI[@]}" schedule
    ;;
  socketio)
    # Same as `bench socketio` with the node backend; the port comes from
    # FRAPPE_SOCKETIO_PORT (node_utils.js), there is no --port flag.
    cd "$BENCH"
    exec node /workspace/frappe/socketio.js
    ;;
  exec)
    # Run an arbitrary command with the environment prepared (used by CI to
    # run the test suite; also handy for one-off `docker compose run`).
    [ "$#" -gt 0 ] || { echo "[entrypoint] FRAPPE_ROLE=exec needs a command" >&2; exit 2; }
    exec "$@"
    ;;
  *)
    echo "[entrypoint] Unknown FRAPPE_ROLE: $role" >&2
    exit 1
    ;;
esac
