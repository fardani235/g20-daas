#!/usr/bin/env bash
set -euo pipefail

: "${SITE_NAME:?SITE_NAME is required}"

log() { echo "[backup $(date -u +%FT%TZ)] $*"; }

backup_root="/backups"
mkdir -p "${backup_root}"
chmod 700 "${backup_root}"

site_backup_dir="/workspace/frappe-bench/sites/${SITE_NAME}/private/backups"

# Capture the mtime of the newest existing backup BEFORE running bench, so we
# can identify only the files produced by THIS invocation afterwards.
prev_max_mtime=0
if [ -d "${site_backup_dir}" ]; then
  prev_max_mtime=$(find "${site_backup_dir}" -maxdepth 1 -type f -printf '%T@\n' | sort -n | tail -1 | cut -d. -f1)
  prev_max_mtime=${prev_max_mtime:-0}
fi
log "Pre-existing backup mtime baseline: ${prev_max_mtime}"

# Run the bench backup via docker compose exec into the web service.
# This requires the backup container to share the compose project network AND
# have access to the docker socket. We use docker compose via the socket
# mounted at /var/run/docker.sock (compose v2 "docker compose" subcommand).
# NOTE: any failure here aborts the script (set -e). The previous
# `|| log "WARN ..."` swallowed real failures and allowed stale backups to
# satisfy the verification gate.
cd /host
docker compose --project-directory /host --profile init exec -T frappe-web \
    bench --site "${SITE_NAME}" backup --with-files \
  || { log "ERROR: bench backup failed -- aborting (no stale files will be copied)"; exit 1; }

# bench writes the backup files into the Frappe site's private/backups/
# directory. We mount that volume read-only at /workspace/frappe-bench/sites,
# so copy only the freshly-created files (mtime > pre-existing baseline) to
# /backups. If bench failed and a file already existed, prev_max_mtime still
# wins, so we'd copy zero files and exit 1 below.
copied=0
if [ -d "${site_backup_dir}" ]; then
  shopt -s nullglob
  # Use distinct globs that don't overlap (the -files.tar glob would also
  # match -private-files.tar; explicit - private prefix avoids the duplicate).
  # Create a baseline file with current mtime BEFORE bench runs; use it with
  # find -newer to capture only files created after this point.
  baseline_file=$(mktemp)
  trap 'rm -f "${baseline_file}"' EXIT

  # Re-run the bench backup (idempotent on a fresh attempt). Actually no --
  # bench already ran above. We need the baseline from BEFORE bench ran.
  # To avoid re-running, we do the comparison in shell using stat.
  # Use distinct globs that don't overlap: the `*-files.tar` glob would also
  # match `*-private-files.tar`; explicit prefix avoids the duplicate.
  while IFS= read -r -d '' f; do
    f_mtime=$(stat -c %Y "${f}")
    if [ "${f_mtime}" -gt "${prev_max_mtime}" ]; then
      cp -p "${f}" "${backup_root}/" && log "Copied $(basename "${f}") to ${backup_root}" && copied=$((copied+1))
    fi
  done < <(find "${site_backup_dir}" -maxdepth 1 -type f \
              \( -name '*-database.sql.gz' -o -name '*-site_config_backup.json' \
              -o -name '*-private-files.tar' -o \
              \( -name '*-files.tar' -a ! -name '*-private-files.tar' \) \) \
              -print0)
  shopt -u nullglob
  rm -f "${baseline_file}"
fi

# If bench succeeded but we copied nothing, something is wrong -- abort loudly.
if [ "${copied}" -eq 0 ]; then
  log "ERROR: bench backup returned 0 but no new files appeared in ${site_backup_dir}"
  exit 1
fi
log "Copied ${copied} new file(s) to ${backup_root}"

# Retention: delete local backups in /backups older than BACKUP_RETENTION_DAYS.
if [ -n "${BACKUP_RETENTION_DAYS:-}" ]; then
  find "${backup_root}" -type f -mtime "+${BACKUP_RETENTION_DAYS}" -delete \
    && log "Pruned local backups older than ${BACKUP_RETENTION_DAYS} days"
  # Also prune the Frappe site's source backups. The source is mounted :ro in
  # THIS container, so the prune runs via docker compose exec on the web service.
  cd /host
  docker compose --project-directory /host --profile init exec -T frappe-web \
    bash -c "find /workspace/frappe-bench/sites/${SITE_NAME}/private/backups/ -type f -mtime '+${BACKUP_RETENTION_DAYS}' -delete" \
    && log "Pruned source backups older than ${BACKUP_RETENTION_DAYS} days"
fi

# Optional S3 sync (any S3-compatible: B2, MinIO, etc.)
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  if command -v aws >/dev/null 2>&1; then
    AWS_ARGS=()
    [ -n "${BACKUP_S3_ENDPOINT:-}" ] && AWS_ARGS+=(--endpoint-url "${BACKUP_S3_ENDPOINT}")
    [ -n "${BACKUP_S3_ACCESS_KEY:-}" ] && export AWS_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY}"
    [ -n "${BACKUP_S3_SECRET_KEY:-}" ] && export AWS_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_KEY}"
    aws "${AWS_ARGS[@]}" s3 sync "${backup_root}" "s3://${BACKUP_S3_BUCKET}/webodm/" \
      && log "Synced backups to s3://${BACKUP_S3_BUCKET}/webodm/"
  else
    log "WARN: BACKUP_S3_BUCKET set but 'aws' CLI not present"
  fi
fi

log "Backup cycle done"