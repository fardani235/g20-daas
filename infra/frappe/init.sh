#!/usr/bin/env bash
# One-shot site bootstrap. Runs as FRAPPE_ROLE=init from the sites directory
# (entrypoint.sh has already resolved secrets and written common_site_config.json).
set -euo pipefail

: "${SITE_NAME:?SITE_NAME is required}"
: "${DB_HOST:?DB_HOST is required}"
: "${DB_PORT:?DB_PORT is required}"
: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required (set via DB_PASSWORD_FILE)}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD is required (set via ADMIN_PASSWORD_FILE)}"

BENCH=/workspace/frappe-bench
PY=$BENCH/env/bin/python
cd "$BENCH/sites"

if [ -f "${SITE_NAME}/site_config.json" ]; then
  echo "[init] Site ${SITE_NAME} exists; running migrate."
  exec "$PY" -m frappe.utils.bench_helper frappe --site "${SITE_NAME}" migrate
fi

echo "[init] Creating site ${SITE_NAME} on postgres://${DB_HOST}:${DB_PORT}/${DB_NAME}"
"$PY" -m frappe.utils.bench_helper frappe new-site "${SITE_NAME}" \
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

# Re-run the config step so the fresh site gets max_file_size etc.
"$PY" /workspace/infra/frappe/configure_site.py
echo "[init] Site ${SITE_NAME} ready."
