#!/usr/bin/env bash
# Run the webodm_core test suite inside a built Frappe image against throwaway
# Postgres/Redis containers. Used by CI before any image is pushed; works
# locally too:
#
#   IMAGE=ghcr.io/fardani235/webodm-frappe:16.34.0 scripts/ci/test-frappe.sh
#   # optional: overlay the working tree's app onto the image
#   IMAGE=... APP_SRC=$PWD/frappe-bench/apps/webodm_core scripts/ci/test-frappe.sh
set -euo pipefail
: "${IMAGE:?IMAGE (frappe image to test) must be set}"
APP_SRC="${APP_SRC:-}"
NET="frappe-ci-$$"
PG="pg-$$"; RC="rc-$$"; RQ="rq-$$"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgis/postgis:16-3.4}"
REDIS_IMAGE="${REDIS_IMAGE:-redis:7-alpine}"

cleanup() {
  docker rm -f "$PG" "$RC" "$RQ" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=postgres \
  "$POSTGRES_IMAGE" >/dev/null
docker run -d --name "$RC" --network "$NET" "$REDIS_IMAGE" redis-server --port 13000 --save "" >/dev/null
docker run -d --name "$RQ" --network "$NET" "$REDIS_IMAGE" redis-server --port 11000 --save "" >/dev/null

echo "[test-frappe] waiting for postgres"
for _ in $(seq 1 60); do
  docker exec "$PG" pg_isready -U postgres -q && break
  sleep 1
done
docker exec "$PG" pg_isready -U postgres

extra=()
if [ -n "$APP_SRC" ]; then extra+=(-v "$APP_SRC:/workspace/webodm_core:ro"); fi

docker run --rm --network "$NET" --entrypoint bash "${extra[@]}" \
  -e "PG=$PG" -e "RC=$RC" -e "RQ=$RQ" \
  -w /workspace/frappe-bench "$IMAGE" -c '
set -euo pipefail
export FRAPPE_BENCH_ROOT=/workspace/frappe-bench
# Bench refuses to run as root unless frappe_user says so; the image bakes that.
bench set-config -g redis_cache    "redis://$RC:13000"
bench set-config -g redis_queue    "redis://$RQ:11000"
bench set-config -g redis_socketio "redis://$RC:13000"
bench new-site ci.local \
  --db-type postgres --db-host "$PG" --db-port 5432 \
  --db-root-username postgres --db-root-password postgres \
  --admin-password admin \
  --install-app webodm_core --install-app webodm_frontend --set-default
bench --site ci.local set-config allow_tests true
bench --site ci.local run-tests --app webodm_core
' 2>&1 | grep -vE "^\s{4}\w+ = |WARN: You should not run this command as root"
