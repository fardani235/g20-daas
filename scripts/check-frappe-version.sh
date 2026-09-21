#!/usr/bin/env bash
# CI guard: the Frappe image tag in docker-compose.yml must equal the version
# the frappe submodule is pinned to. Catches the drift where the repo says one
# version and deploys pull another.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
want="$("$ROOT/scripts/frappe-version.sh")"
have="$(sed -nE 's#^[[:space:]]*image:[[:space:]]*[^[:space:]]*/webodm-frappe:([0-9][^@[:space:]]*).*#\1#p' "$ROOT/docker-compose.yml" | head -1)"
if [ "$want" != "$have" ]; then
  echo "Frappe version drift: submodule pins $want but docker-compose.yml uses webodm-frappe:$have" >&2
  echo "Fix: bump the submodule (git -C frappe-bench/apps/frappe checkout v<ver>) or run scripts/pin-images.sh" >&2
  exit 1
fi
echo "frappe version OK: $want"
