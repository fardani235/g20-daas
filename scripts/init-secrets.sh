#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p secrets
umask 077

for name in db_password admin_password redis_cache_password redis_queue_password frappe_admin_password \
            provisioner_api_token node_token_secret; do
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
    chmod 600 "$path"  # readable by the docker daemon (root) only
    echo "GEN: $path"
  fi
done

# Cloud credentials are *not* generated: they come from your AWS account (or
# MinIO). Compose still needs the files to exist, so create empty placeholders;
# an empty file means "use the default credential chain / not configured".
# Fill them in per docs/on-demand-processing/deployment.md.
for name in s3_app_access_key_id s3_app_secret_access_key \
            s3_geospatial_access_key_id s3_geospatial_secret_access_key \
            aws_provisioner_access_key_id aws_provisioner_secret_access_key; do
  path="secrets/${name}.txt"
  if [ -f "$path" ]; then
    echo "OK: $path already exists (skipping)"
  else
    : > "$path"
    chmod 600 "$path"
    echo "NEW: $path (empty placeholder -- fill in to enable object storage / on-demand compute)"
  fi
done

echo
echo "Secrets ready in ./secrets/"
ls -l secrets/
