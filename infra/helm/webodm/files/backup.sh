#!/usr/bin/env bash
# Site backup for the Kubernetes CronJob. Runs inside the Frappe image with
# FRAPPE_ROLE=exec, so the entrypoint has already prepared the environment and
# the working directory is the sites directory. (The compose backup image
# drives `docker compose exec` and cannot run in a cluster.)
set -euo pipefail

: "${SITE_NAME:?SITE_NAME is required}"

log() { echo "[backup $(date -u +%FT%TZ)] $*"; }

backup_root=/backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="${backup_root}/${stamp}"
mkdir -p "${dest}"
chmod 700 "${backup_root}"

log "Backing up ${SITE_NAME} to ${dest}"
/workspace/frappe-bench/env/bin/python -m frappe.utils.bench_helper frappe \
  --site "${SITE_NAME}" backup --with-files --backup-path "${dest}" \
  || { log "ERROR: backup failed"; rm -rf "${dest}"; exit 1; }

if ! find "${dest}" -type f -name '*-database.sql.gz' -size +0 | grep -q .; then
  log "ERROR: backup reported success but ${dest} holds no database dump"
  exit 1
fi
log "Wrote $(find "${dest}" -type f | wc -l) file(s)"

if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  /workspace/frappe-bench/env/bin/python - "${dest}" "${stamp}" <<'PY'
import os
import sys

import boto3

dest, stamp = sys.argv[1], sys.argv[2]
client = boto3.client(
    "s3",
    endpoint_url=os.environ.get("BACKUP_S3_ENDPOINT") or None,
    aws_access_key_id=os.environ.get("BACKUP_S3_ACCESS_KEY") or None,
    aws_secret_access_key=os.environ.get("BACKUP_S3_SECRET_KEY") or None,
)
bucket = os.environ["BACKUP_S3_BUCKET"]
for name in sorted(os.listdir(dest)):
    client.upload_file(os.path.join(dest, name), bucket, f"webodm/{stamp}/{name}")
    print(f"[backup] uploaded s3://{bucket}/webodm/{stamp}/{name}")
PY
fi

# Retention applies to the local claim only; expire bucket copies with a
# lifecycle rule on the bucket.
if [ -n "${BACKUP_RETENTION_DAYS:-}" ]; then
  find "${backup_root}" -mindepth 1 -maxdepth 1 -type d -mtime "+${BACKUP_RETENTION_DAYS}" \
    -exec rm -rf {} + && log "Pruned backups older than ${BACKUP_RETENTION_DAYS} days"
fi

log "Backup cycle done"
