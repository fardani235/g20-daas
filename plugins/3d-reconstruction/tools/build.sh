#!/usr/bin/env bash
# Package the plugin for upload: plugins/3d-reconstruction-<version>.zip
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$here/..}"
version="$(python3 -c 'import json;print(json.load(open("'"$here"'/plugin.json"))["version"])')"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

(cd "$here" && tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
    --exclude='tests' --exclude='tools' -cf - main.py plugin.json recon3d README.md) | tar -xf - -C "$stage"
rm -f "$out/3d-reconstruction-$version.zip"
(cd "$stage" && zip -qr "$out/3d-reconstruction-$version.zip" .)
ls -la "$out/3d-reconstruction-$version.zip"
