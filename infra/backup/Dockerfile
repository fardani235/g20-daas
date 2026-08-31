# syntax=docker/dockerfile:1.7

FROM python:3.14-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    BACKUP_SCHEDULE="0 2 * * *"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    cron \
    curl \
    tini \
  && install -m 0755 -d /etc/apt/keyrings \
  && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
  && chmod a+r /etc/apt/keyrings/docker.asc \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
  && rm -rf /var/lib/apt/lists/*

COPY backup.sh /usr/local/bin/backup.sh
COPY crontab /etc/cron.d/webodm-backup
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/entrypoint.sh \
 && chmod 0644 /etc/cron.d/webodm-backup \
 && touch /var/log/cron.log

# tini is PID-1 for clean signal handling; entrypoint.sh materializes the
# /etc/cron.d/webodm-backup file from $BACKUP_SCHEDULE, then execs cron -f.
CMD ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]