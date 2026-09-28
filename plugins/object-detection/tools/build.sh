#!/usr/bin/env bash
# Package the plugin for upload. Produces object-detection-<version>.zip next
# to this plugin's directory (or in $1). Run tools/fetch_models.py first so the
# package contains the weights its cards describe; cards without weights are
# reported as unavailable at run time, and the other models still run.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$here/..}"
python3 "$here/tools/update_manifest.py"
version="$(python3 -c 'import json;print(json.load(open("'"$here"'/plugin.json"))["version"])')"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

mkdir -p "$stage/pkg"
(cd "$here" && tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
    --exclude='models/*.part' \
    -cf - main.py plugin.json detplugin models README.md) | tar -xf - -C "$stage/pkg"

missing=$(cd "$here" && python3 tools/fetch_models.py --list | grep -v ' present$' || true)
if [ -n "$missing" ]; then
  echo "note: cards without packaged weights (unavailable at run time):"
  echo "$missing" | sed 's/^/  /'
fi

(cd "$stage/pkg" && rm -f "$out/object-detection-$version.zip" \
  && zip -qr "$out/object-detection-$version.zip" .)
ls -la "$out/object-detection-$version.zip"
