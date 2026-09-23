#!/usr/bin/env bash
# Package the plugin for upload. Produces next to this directory:
#   3d-reconstruction-<version>.zip
# Only runtime files are included (no tests/tools/caches); plugin.json ends up
# at the zip root, which is what the platform's package validator requires.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$here/..}"
version="$(python3 -c 'import json;print(json.load(open("'"$here"'/plugin.json"))["version"])')"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

mkdir -p "$stage/pkg"
(cd "$here" && tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
    -cf - main.py plugin.json recon README.md) | tar -xf - -C "$stage/pkg"
(cd "$stage/pkg" && rm -f "$out/3d-reconstruction-$version.zip" \
  && zip -qr "$out/3d-reconstruction-$version.zip" .)

ls -la "$out/3d-reconstruction-$version.zip"
