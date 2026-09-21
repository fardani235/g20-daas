#!/usr/bin/env bash
# Print the Frappe version this repo is pinned to.
#
# The single source of truth is the `frappe-bench/apps/frappe` submodule
# (declared in .gitmodules, pinned to a commit). We read `__version__` from
# that checkout so CI, image tags and docs cannot drift from what is actually
# built. Requires the submodule to be checked out (git submodule update --init).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
init="$root/frappe-bench/apps/frappe/frappe/__init__.py"

if [ ! -f "$init" ]; then
  echo "frappe submodule not checked out at frappe-bench/apps/frappe (run: git submodule update --init)" >&2
  exit 1
fi

version="$(sed -nE 's/^__version__ *= *"([^"]+)".*/\1/p' "$init" | head -1)"
if [ -z "$version" ]; then
  echo "could not read __version__ from $init" >&2
  exit 1
fi
printf '%s\n' "$version"
