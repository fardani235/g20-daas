#!/usr/bin/env bash
# Create the credentials Secret the Helm chart (infra/helm/webodm) reads, with
# random values -- the Kubernetes counterpart of scripts/init-secrets.sh.
#
#   scripts/k8s-init-secrets.sh [namespace] [secret-name]
#
# Defaults: namespace "webodm", secret "webodm-secrets". The namespace is
# created if missing. An existing Secret is never overwritten: rotating the
# database or Redis passwords of a running install takes more than a new value.
#
# Generated keys: the seven the stack always needs, plus MinIO root and
# app/geospatial S3 key pairs for the dev overlay (in-cluster MinIO). For
# external S3 or on-demand compute, add the cloud keys afterwards, e.g.
#
#   kubectl -n webodm patch secret webodm-secrets --type merge -p \
#     '{"stringData":{"s3_app_access_key_id":"AKIA...","s3_app_secret_access_key":"..."}}'
set -euo pipefail

namespace="${1:-webodm}"
secret="${2:-webodm-secrets}"

command -v kubectl >/dev/null || { echo "kubectl not found" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl not found" >&2; exit 1; }

kubectl get namespace "$namespace" >/dev/null 2>&1 || kubectl create namespace "$namespace"

if kubectl -n "$namespace" get secret "$secret" >/dev/null 2>&1; then
  echo "OK: secret $namespace/$secret already exists (left untouched)"
  exit 0
fi

# Hex only: safe in URLs, SQL and shell, and never ends in a newline.
rand() { openssl rand -hex "$1"; }

# Values go to kubectl through a 0600 env file, not the command line, so they
# never show up in the process list or shell history.
umask 077
envfile="$(mktemp)"
trap 'rm -f "$envfile"' EXIT
{
  for name in db_password admin_password frappe_admin_password redis_cache_password \
              redis_queue_password provisioner_api_token node_token_secret; do
    echo "${name}=$(rand 32)"
  done
  echo "minio_root_user=minio-$(rand 4)"
  echo "minio_root_password=$(rand 24)"
  echo "s3_app_access_key_id=app-$(rand 8)"
  echo "s3_app_secret_access_key=$(rand 24)"
  echo "s3_geospatial_access_key_id=geo-$(rand 8)"
  echo "s3_geospatial_secret_access_key=$(rand 24)"
} > "$envfile"

kubectl -n "$namespace" create secret generic "$secret" --from-env-file="$envfile"

cat <<MSG

Secret $namespace/$secret created. Administrator password:

  kubectl -n $namespace get secret $secret -o jsonpath='{.data.admin_password}' | base64 -d; echo
MSG
