#!/usr/bin/env bash
# Package the plugin for upload. Produces two zips next to this directory:
#   semantic-segmentation-<version>.zip           (class mask, raster output)
#   semantic-segmentation-polygons-<version>.zip  (class polygons, vector output)
# Both contain the same code and models; only plugin.json differs.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$here/..}"
version="$(python3 -c 'import json;print(json.load(open("'"$here"'/plugin.json"))["version"])')"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

copy_tree() {
  mkdir -p "$1"
  (cd "$here" && tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
      --exclude='tests' --exclude='tools' --exclude='plugin-polygons.json' \
      -cf - main.py plugin.json segplugin models README.md) | tar -xf - -C "$1"
}

copy_tree "$stage/mask"
(cd "$stage/mask" && rm -f "$out/semantic-segmentation-$version.zip" \
  && zip -qr "$out/semantic-segmentation-$version.zip" .)

copy_tree "$stage/polygons"
cp "$here/plugin-polygons.json" "$stage/polygons/plugin.json"
(cd "$stage/polygons" && rm -f "$out/semantic-segmentation-polygons-$version.zip" \
  && zip -qr "$out/semantic-segmentation-polygons-$version.zip" .)

ls -la "$out"/semantic-segmentation-"$version".zip "$out"/semantic-segmentation-polygons-"$version".zip
