#!/bin/sh
# Create the application bucket and one MinIO user per configured key pair, so
# the app and the geospatial service never hold the MinIO root credentials.
# Idempotent: safe to re-run.
set -eu

i=0
until mc alias set local "$MINIO_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -lt 60 ] || { echo "MinIO at $MINIO_ENDPOINT did not become reachable" >&2; exit 1; }
  echo "waiting for MinIO at $MINIO_ENDPOINT ..."
  sleep 5
done

mc mb --ignore-existing "local/$BUCKET"

add_user() { # access-key secret-key
  [ -n "$1" ] && [ -n "$2" ] || return 0
  [ "$1" != "$MINIO_ROOT_USER" ] || return 0
  mc admin user add local "$1" "$2" >/dev/null
  mc admin policy attach local readwrite --user "$1" >/dev/null 2>&1 || true
  echo "user ready"
}
add_user "${APP_ACCESS_KEY_ID:-}" "${APP_SECRET_ACCESS_KEY:-}"
if [ "${GEO_ACCESS_KEY_ID:-}" != "${APP_ACCESS_KEY_ID:-}" ]; then
  add_user "${GEO_ACCESS_KEY_ID:-}" "${GEO_SECRET_ACCESS_KEY:-}"
fi

echo "bucket ready: $BUCKET"
