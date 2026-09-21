#!/usr/bin/env bash
# Print the Frappe version this repo is pinned to.
#
# Single source of truth is the `frappe-bench/apps/frappe` submodule gitlink.
# If the submodule is checked out, read frappe/__init__.py; otherwise resolve
# the pinned commit against upstream tags (needs network).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUB="$ROOT/frappe-bench/apps/frappe"

if [ -f "$SUB/frappe/__init__.py" ]; then
  sed -nE 's/^__version__ = "([^"]+)"/\1/p' "$SUB/frappe/__init__.py" | head -1
  exit 0
fi

sha="$(git -C "$ROOT" ls-files --stage frappe-bench/apps/frappe | awk '{print $2}')"
[ -n "$sha" ] || { echo "frappe submodule gitlink not found" >&2; exit 1; }
url="$(git -C "$ROOT" config -f .gitmodules submodule.frappe-bench/apps/frappe.url)"
# Match both lightweight tags (refs/tags/vX -> commit) and annotated ones (peeled ^{} line).
tag="$(git ls-remote --tags "$url" 'refs/tags/v*' | awk -v s="$sha" '$1==s {t=$2; sub("refs/tags/v","",t); sub(/\^\{\}$/,"",t); print t; exit}')"
[ -n "$tag" ] || { echo "no upstream tag points at pinned frappe commit $sha" >&2; exit 1; }
echo "$tag"
