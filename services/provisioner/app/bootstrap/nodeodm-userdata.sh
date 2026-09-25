#!/bin/bash
# NodeODM node bootstrap (EC2 user-data). Rendered by the provisioner per run:
# the token and port are per-instance, everything else is deployment config.
#
# Works on two kinds of image:
#   * a prebaked AMI (infra/aws/bake-nodeodm-ami.sh) that already has Docker
#     and the NodeODM image: only the `docker run` below happens, boot is fast
#     and deterministic;
#   * a stock Ubuntu 22.04/24.04 or Amazon Linux 2023 AMI: Docker is installed
#     and the image pulled first. Slower (minutes) but nothing is blocked on
#     image work.
#
# Nothing else runs on the box: readiness is observed by the provisioner
# polling NodeODM, not reported by an agent.
set -euo pipefail
exec > >(tee -a /var/log/webodm-nodeodm-bootstrap.log) 2>&1

TOKEN='__TOKEN__'
PORT='__PORT__'
IMAGE='__IMAGE__'
MAX_LIFETIME='__MAX_LIFETIME__'
EXTRA_ARGS='__EXTRA_ARGS__'

echo "[bootstrap] $(date -u +%FT%TZ) start (port=${PORT}, image=${IMAGE})"

# Belt and braces against a dead control plane: the instance is launched with
# InstanceInitiatedShutdownBehavior=terminate, so a shutdown here terminates
# it even if nothing ever calls DELETE on the provisioner. Ten minutes of
# grace past the lifetime budget so the app's own sweep normally wins.
if [ -n "${MAX_LIFETIME}" ] && [ "${MAX_LIFETIME}" -gt 0 ]; then
  shutdown -h +$(( (MAX_LIFETIME + 600) / 60 )) "webodm: lifetime budget exhausted" || true
fi

install_docker() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y --no-install-recommends ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    . /etc/os-release
    curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y docker
  else
    echo "[bootstrap] no supported package manager to install docker" >&2
    exit 1
  fi
}

if ! command -v docker >/dev/null 2>&1; then
  echo "[bootstrap] docker not present: installing (non-prebaked image)"
  install_docker
fi
systemctl enable --now docker

# Scratch for ODM: use the instance store if one is mounted, else the root volume.
DATA_DIR=/opt/nodeodm/data
if mountpoint -q /mnt && [ -w /mnt ]; then DATA_DIR=/mnt/nodeodm/data; fi
mkdir -p "${DATA_DIR}"

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "[bootstrap] pulling ${IMAGE}"
  for i in 1 2 3 4 5; do docker pull "${IMAGE}" && break || sleep $((i * 10)); done
fi

docker rm -f nodeodm >/dev/null 2>&1 || true
# shellcheck disable=SC2086
docker run -d --name nodeodm --restart unless-stopped \
  -p "${PORT}:3000" \
  -v "${DATA_DIR}:/var/www/data" \
  "${IMAGE}" --token "${TOKEN}" ${EXTRA_ARGS}

echo "[bootstrap] $(date -u +%FT%TZ) nodeodm started"
