#!/usr/bin/env bash
# Render check for the Helm chart (infra/helm/webodm), run by CI and locally:
#
#   scripts/check-helm.sh
#
# For the chart defaults and for each environment overlay it runs `helm lint`
# and `helm template`, then asserts the rendered manifests contain no Secret
# object and no inline credential -- credentials must only ever be referenced
# (secretKeyRef). Also exercises the edge variants and the value guards, and
# validates against the Kubernetes schemas when kubeconform is installed.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
chart="$root/infra/helm/webodm"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

command -v helm >/dev/null || { echo "helm not found" >&2; exit 1; }

fail() { echo "FAIL: $*" >&2; exit 1; }

render() { # name, then extra helm args
  local name="$1"; shift
  helm lint "$chart" --strict "$@" >"$out/$name.lint" 2>&1 || { cat "$out/$name.lint" >&2; fail "helm lint ($name)"; }
  helm template webodm "$chart" --namespace webodm "$@" >"$out/$name.yaml" || fail "helm template ($name)"
  [ -s "$out/$name.yaml" ] || fail "helm template ($name) rendered nothing"

  # No Secret objects, and no credential given as a literal env value: every
  # env var named like a credential must come from a secretKeyRef.
  if grep -nE '^kind:[[:space:]]*Secret[[:space:]]*$' "$out/$name.yaml"; then
    fail "$name renders a Secret object"
  fi
  if grep -nE -A1 '^[[:space:]]*- name: [A-Z0-9_]*(PASSWORD|SECRET|TOKEN|ACCESS_KEY|REDISCLI_AUTH|MINIO_ROOT_USER)[A-Z0-9_]*$' "$out/$name.yaml" \
      | grep -E '^[0-9]+-[[:space:]]*value:'; then
    fail "$name renders a credential as a literal env value"
  fi

  if command -v kubeconform >/dev/null; then
    kubeconform -strict -summary "$out/$name.yaml" || fail "kubeconform ($name)"
  fi
  echo "ok: $name ($(grep -c '^kind:' "$out/$name.yaml") objects)"
}

render defaults
render dev  -f "$chart/values.dev.yaml"
render prod -f "$chart/values.prod.yaml"

# Edge variants and component switches keep rendering.
render prod-ingress -f "$chart/values.prod.yaml" \
  --set caddy.tls.mode=off --set caddy.service.type=ClusterIP \
  --set caddy.ingress.enabled=true --set caddy.ingress.tlsSecretName=webodm-tls \
  --set 'caddy.trustedProxies={10.244.0.0/16}'
render prod-proxy-protocol -f "$chart/values.prod.yaml" \
  --set caddy.proxyProtocol.enabled=true --set 'caddy.proxyProtocol.allow={10.0.0.0/8}'
render dev-minimal -f "$chart/values.dev.yaml" \
  --set nodeodm.enabled=false --set backup.enabled=false --set bootstrap.enabled=false \
  --set pluginRunner.enabled=false --set provisioner.enabled=false --set minio.enabled=false

# What each overlay promises.
grep -q '^kind: StatefulSet' "$out/dev.yaml" || fail "dev: no StatefulSet"
grep -q 'name: minio$' "$out/dev.yaml" || fail "dev: MinIO missing"
grep -q 'name: minio$' "$out/prod.yaml" && fail "prod: MinIO must not be deployed"
grep -q 'type: NodePort' "$out/dev.yaml" || fail "dev: edge is not a NodePort"
grep -q 'type: LoadBalancer' "$out/prod.yaml" || fail "prod: edge is not a LoadBalancer"
grep -q 'externalTrafficPolicy: Local' "$out/prod.yaml" || fail "prod: externalTrafficPolicy is not Local"
grep -q 'imagePullSecrets:' "$out/prod.yaml" || fail "prod: no image pull secret"
if grep -E '^[[:space:]]*image:' "$out/prod.yaml" | grep -v '@sha256:'; then
  fail "prod: image not pinned by digest"
fi
grep -q '^kind: Job' "$out/dev-minimal.yaml" && fail "bootstrap.enabled=false still renders a Job"
grep -q '^kind: CronJob' "$out/dev-minimal.yaml" && fail "backup.enabled=false still renders a CronJob"

# A Job's pod template is immutable, so the Job name must change whenever the
# template does (otherwise `helm upgrade` fails with "field is immutable").
# Render the dev overlay with an added imagePullSecret and assert both Job
# names change: guards the whole-pod-template hash in _helpers.tpl.
render dev-pull -f "$chart/values.dev.yaml" --set 'imagePullSecrets[0].name=ghcr-pull'
job_name() { grep -oE "^  name: ${1}-[a-f0-9]+" "$2" | head -1 | sed 's/^  name: //'; }
for job in frappe-init minio-init; do
  before="$(job_name "$job" "$out/dev.yaml")"
  after="$(job_name "$job" "$out/dev-pull.yaml")"
  [ -n "$before" ] || fail "job-name guard: $job missing from the dev render"
  [ -n "$after" ] || fail "job-name guard: $job missing from the dev-pull render"
  [ "$before" != "$after" ] || fail "job-name guard: $job name unchanged when the pod template changed ($before)"
done
echo "ok: Job names track the pod template"

# The guards reject values that cannot work.
expect_fail() { # description, then helm args
  local what="$1"; shift
  if helm template webodm "$chart" "$@" >/dev/null 2>&1; then fail "expected a render error: $what"; fi
  echo "ok: rejected ($what)"
}
expect_fail "ingress with Caddy TLS on" --set caddy.ingress.enabled=true
expect_fail "unknown TLS mode" --set caddy.tls.mode=bogus
expect_fail "secrets.create without values" --set secrets.create=true
expect_fail "PROXY protocol without allow list" --set caddy.proxyProtocol.enabled=true

echo "helm chart checks passed"
