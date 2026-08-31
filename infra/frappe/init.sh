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