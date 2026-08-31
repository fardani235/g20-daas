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
    # gunicorn with sync workers, threads off (Frappe handles its own concurrency per worker).
    # We use `infra_frappe_wsgi:application` instead of `frappe.app:application` because the
    # latter bypasses Frappe's static middleware (which is only installed in
    # `frappe.app.serve()` for the werkzeug dev server). Our WSGI wrapper at
    # /workspace/frappe-bench/infra_frappe_wsgi.py installs the same static middleware so
    # /assets/* and /files/* are served directly from sites/assets without round-tripping
    # through the WSGI app.
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
    exec bench socketio --port 9000
    ;;
  *)
    echo "Unknown FRAPPE_ROLE: $role" >&2
    exit 1
    ;;
esac