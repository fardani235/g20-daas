#!/usr/bin/env bash
# A throwaway Frappe site for running the webodm_core tests from a checkout,
# without a local bench: PostgreSQL + two Redis containers on their own Docker
# network, and the Frappe image with this checkout's webodm_core mounted over
# the copy in the image.
#
#   scripts/metering/test-site.sh up                        # create network, containers, site
#   scripts/metering/test-site.sh migrate                   # after a DocType JSON change
#   scripts/metering/test-site.sh test                      # the whole webodm_core suite
#   scripts/metering/test-site.sh test --module webodm_core.metering.test_ledger
#   scripts/metering/test-site.sh py <script.py>            # a script, with the site available
#   scripts/metering/test-site.sh frappe execute webodm_core.metering.report.health   # any bench command
#   scripts/metering/test-site.sh down                      # remove everything again
#
# Names are prefixed with $METER_PREFIX (default "meter") so two checkouts do
# not share containers. The image must match the Frappe version the code
# targets; override with $FRAPPE_IMAGE.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PREFIX="${METER_PREFIX:-meter}"
NET="${PREFIX}-test"
IMG="${FRAPPE_IMAGE:-ghcr.io/fardani235/webodm-frappe:16.34.0}"
APP="${WEBODM_CORE_DIR:-$ROOT/frappe-bench/apps/webodm_core}"
SITE=ci.localhost
ENVV=(-e SITE_NAME=$SITE -e DB_HOST=${PREFIX}-pg -e DB_PORT=5432 -e DB_NAME=webodm_ci -e DB_USER=webodm_ci
      -e DB_PASSWORD=webodm_ci -e ADMIN_PASSWORD=admin -e FRAPPE_ROOT_USER=postgres -e FRAPPE_ROOT_PASSWORD=postgres
      -e REDIS_CACHE_HOST=${PREFIX}-redis-cache -e REDIS_CACHE_PORT=6379
      -e REDIS_QUEUE_HOST=${PREFIX}-redis-queue -e REDIS_QUEUE_PORT=6379
      -e GEOSPATIAL_URL=http://127.0.0.1:5000)

run() {
  docker run --rm --network "$NET" -v "${PREFIX}-sites:/workspace/frappe-bench/sites" \
    -v "$APP:/workspace/webodm_core" "${ENVV[@]}" "$@"
}
frappe() {
  run -e FRAPPE_ROLE=exec "$IMG" /workspace/frappe-bench/env/bin/python -m frappe.utils.bench_helper frappe --site $SITE "$@"
}

case "${1:-}" in
  up)
    docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
    docker volume create "${PREFIX}-sites" >/dev/null
    docker run -d --name "${PREFIX}-pg" --network "$NET" -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
      -e POSTGRES_DB=postgres postgis/postgis:16-3.4 >/dev/null
    docker run -d --name "${PREFIX}-redis-cache" --network "$NET" redis:7-alpine >/dev/null
    docker run -d --name "${PREFIX}-redis-queue" --network "$NET" redis:7-alpine >/dev/null
    for _ in $(seq 1 60); do docker exec "${PREFIX}-pg" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
    sleep 3
    run -e FRAPPE_ROLE=init "$IMG"
    frappe set-config allow_tests true
    ;;
  migrate) frappe migrate ;;
  test) shift; frappe run-tests --app webodm_core "$@" ;;
  frappe) shift; frappe "$@" ;;
  py)
    shift
    script="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
    run -e FRAPPE_ROLE=exec -v "$script:/probe/script.py:ro" "$IMG" /workspace/frappe-bench/env/bin/python /probe/script.py
    ;;
  down)
    docker rm -f "${PREFIX}-pg" "${PREFIX}-redis-cache" "${PREFIX}-redis-queue" >/dev/null 2>&1 || true
    docker volume rm "${PREFIX}-sites" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    ;;
  *) echo "usage: $0 up | migrate | test [run-tests args] | py <script.py> | frappe <args> | down" >&2; exit 2 ;;
esac
