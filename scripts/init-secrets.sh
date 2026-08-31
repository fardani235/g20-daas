#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p secrets
umask 077

for name in db_password admin_password redis_cache_password redis_queue_password frappe_admin_password; do
  path="secrets/${name}.txt"
  if [ -f "$path" ]; then
    echo "OK: $path already exists (skipping)"
  else
    # Use hex for db_password to avoid +, /, = breaking psql command parsing.
    # Other secrets use base64 (no shell quoting issues).
    if [ "$name" = "db_password" ]; then
      openssl rand -hex 32 > "$path"
    else
      openssl rand -base64 32 | tr -d '\n' > "$path"
    fi
    chmod 644 "$path"
    echo "GEN: $path"
  fi
done

echo
echo "Secrets ready in ./secrets/"
ls -l secrets/
