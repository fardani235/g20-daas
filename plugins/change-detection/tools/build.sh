#!/usr/bin/env bash
# Package the plugin for upload. Produces two zips next to this directory (or in $1):
#   change-detection-<version>.zip          (cut/fill regions, vector output)
#   change-detection-raster-<version>.zip   (elevation difference GeoTIFF, raster output)
# Both contain the same code; only plugin.json differs. There are no models or
# other downloads: the package is self-contained.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$here/..}"
version="$(python3 -c 'import json;print(json.load(open("'"$here"'/plugin.json"))["version"])')"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

copy_tree() {
  mkdir -p "$1"
  (cd "$here" && tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
      -cf - main.py plugin.json cdplugin README.md) | tar -xf - -C "$1"
}

copy_tree "$stage/regions"
(cd "$stage/regions" && rm -f "$out/change-detection-$version.zip" \
  && zip -qr "$out/change-detection-$version.zip" .)

copy_tree "$stage/raster"
cp "$here/plugin-raster.json" "$stage/raster/plugin.json"
(cd "$stage/raster" && rm -f "$out/change-detection-raster-$version.zip" \
  && zip -qr "$out/change-detection-raster-$version.zip" .)

ls -la "$out"/change-detection-"$version".zip "$out"/change-detection-raster-"$version".zip
