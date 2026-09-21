#!/usr/bin/env bash
# Re-pin every `image:` in docker-compose.yml to the current digest of its tag.
#
#   scripts/pin-images.sh            # rewrite docker-compose.yml in place
#   scripts/pin-images.sh --check    # exit 1 if any pin is stale (for CI)
#   FRAPPE_VERSION=16.34.0 scripts/pin-images.sh   # also move the frappe tag
#
# Resolution uses `docker buildx imagetools inspect`, so it works for remote
# registries without pulling layers. Review the diff before committing.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
compose="$root/docker-compose.yml"
check=0
[ "${1:-}" = "--check" ] && check=1

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
cp "$compose" "$tmp"

stale=0
# Match "image: <ref>" lines, where <ref> may already carry "@sha256:...".
while IFS= read -r line; do
  ref="$(sed -nE 's/^[[:space:]]*image:[[:space:]]*([^[:space:]#]+).*/\1/p' <<<"$line")"
  [ -n "$ref" ] || continue
  tagged="${ref%%@*}"
  old_digest="${ref#*@}"; [ "$old_digest" = "$ref" ] && old_digest=""

  # Optionally retarget the frappe image tag to FRAPPE_VERSION.
  if [[ "$tagged" == */webodm-frappe:* ]] && [ -n "${FRAPPE_VERSION:-}" ]; then
    tagged="${tagged%%:*}:${FRAPPE_VERSION}"
  fi

  digest="$(docker buildx imagetools inspect "$tagged" 2>/dev/null | awk '/^Digest:/{print $2; exit}')"
  if [ -z "$digest" ]; then
    echo "!! could not resolve $tagged" >&2
    exit 1
  fi
  new_ref="${tagged}@${digest}"
  if [ "$new_ref" != "$ref" ]; then
    stale=1
    echo "$ref -> $new_ref"
    # Escape for sed: only [.+/@:] can appear in refs.
    esc_old="$(printf '%s' "$ref" | sed 's/[.[\*^$/]/\\&/g')"
    esc_new="$(printf '%s' "$new_ref" | sed 's/[&/\]/\\&/g')"
    sed -i "s/image:\([[:space:]]*\)${esc_old}/image:\1${esc_new}/" "$tmp"
  fi
done < "$compose"

if [ "$check" = 1 ]; then
  [ "$stale" = 0 ] && echo "all image pins are current" || { echo "image pins are stale (run scripts/pin-images.sh)" >&2; exit 1; }
  exit 0
fi

if [ "$stale" = 1 ]; then
  cp "$tmp" "$compose"
  echo "updated $compose"
else
  echo "all image pins already current"
fi
