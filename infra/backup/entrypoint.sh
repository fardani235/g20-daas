#!/usr/bin/env bash
# entrypoint.sh — generate /etc/cron.d/webodm-backup from $BACKUP_SCHEDULE,
# then exec cron in the foreground. cron itself does NOT expand env vars in
# /etc/cron.d/* files, so we have to materialize the schedule at start time.
set -euo pipefail

SCHEDULE="${BACKUP_SCHEDULE:-0 2 * * *}"

# Validate: cron expects exactly 5 whitespace-separated fields
# (minute hour day-of-month month day-of-week).
FIELD_COUNT=$(printf '%s' "${SCHEDULE}" | awk '{print NF}')
if [ "${FIELD_COUNT}" -ne 5 ]; then
  echo "[entrypoint] ERROR: BACKUP_SCHEDULE must be 5 space-separated fields" >&2
  echo "[entrypoint] got: '${SCHEDULE}' (${FIELD_COUNT} fields)" >&2
  exit 1
fi

# Write the system crontab. The trailing newline is REQUIRED — cron silently
# ignores the last line of a file that doesn't end with \n.
cat > /etc/cron.d/webodm-backup <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

${SCHEDULE} root /usr/local/bin/backup.sh >> /var/log/cron.log 2>&1
CRON
chmod 0644 /etc/cron.d/webodm-backup

echo "[entrypoint] Installed cron schedule: ${SCHEDULE}"

# `cron -f` runs cron in the foreground; combined with tini as PID-1 this
# gives clean signal handling. We redirect to /var/log/cron.log so cron
# errors and startup messages are captured alongside the script's own output.
exec cron -f >> /var/log/cron.log 2>&1