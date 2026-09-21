#!/usr/bin/env bash
# Rewrite every `image:` line in docker-compose.yml to `name:tag@sha256:...`
# using the digest the registry currently serves for that tag.
#
# Deploys therefore run exactly the bytes that were inspected here, not
# whatever a mutable tag (`:1`, `:latest`) points at tomorrow. Run this after
# CI has published new images, review the diff, commit.
#
#   scripts/pin-images.sh            # all images
#   scripts/pin-images.sh geospatial # only lines mentioning "geospatial"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="$ROOT/docker-compose.yml"
FILTER="${1:-}"
FRAPPE_VERSION="$("$ROOT/scripts/frappe-version.sh")"

tmp="$(mktemp)"
while IFS= read -r line; do
  if [[ "$line" =~ ^([[:space:]]*image:[[:space:]]*)([^[:space:]@]+)(@sha256:[0-9a-f]+)?[[:space:]]*$ ]]; then
    prefix="${BASH_REMATCH[1]}"; ref="${BASH_REMATCH[2]}"
    # The Frappe image tag follows the submodule pin.
    if [[ "$ref" == */webodm-frappe:* ]]; then ref="${ref%%:*}:${FRAPPE_VERSION}"; fi
    if [ -z "$FILTER" ] || [[ "$ref" == *"$FILTER"* ]]; then
      digest="$(docker buildx imagetools inspect "$ref" 2>/dev/null | awk '/^Digest:/{print $2; exit}')"
      if [ -z "$digest" ]; then echo "WARN: could not resolve $ref, leaving as is" >&2; printf '%s\n' "$line" >>"$tmp"; continue; fi
      echo "$ref -> $digest" >&2
      printf '%s%s@%s\n' "$prefix" "$ref" "$digest" >>"$tmp"
      continue
    fi
  fi
  printf '%s\n' "$line" >>"$tmp"
done <"$COMPOSE"
mv "$tmp" "$COMPOSE"
