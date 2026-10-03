# WebODM Helm chart

One chart for the whole stack, two environments:

| File | For |
|---|---|
| `values.yaml` | Defaults and the reference for every setting |
| `values.dev.yaml` | A single-node cluster (minikube): in-cluster Postgres, Redis, MinIO and NodeODM, NodePort edge, small requests |
| `values.prod.yaml` | A real cluster: public domain over TLS, external S3, pinned image digests, resource requests, LoadBalancer edge, registry pull secret |

The full walkthrough (prerequisites, production install, moving data across)
is in [`docs/deployment/kubernetes.md`](../../../docs/deployment/kubernetes.md).
Docker Compose remains the local development path; this chart does not change it.

## Quick start (minikube)

```bash
scripts/k8s-init-secrets.sh webodm      # namespace + Secret with random credentials
helm upgrade --install webodm infra/helm/webodm -n webodm \
  -f infra/helm/webodm/values.dev.yaml

kubectl -n webodm get pods -w           # Frappe pods wait in Init for the bootstrap Job
echo "$(minikube ip) webodm.local admin.webodm.local" | sudo tee -a /etc/hosts
# https://webodm.local:32443/  (self-signed certificate from Caddy's internal CA)
kubectl -n webodm get secret webodm-secrets -o jsonpath='{.data.admin_password}' | base64 -d; echo
```

Upgrade with the same `helm upgrade --install` command. When an upgrade changes
an image tag or digest, use `scripts/k8s-upgrade.py` instead: it resolves every
`images.<name>` tag to its current digest and applies them (see
[`docs/deployment/kubernetes.md` §5](../../../docs/deployment/kubernetes.md#5-upgrade-roll-back-uninstall)).
Remove with `helm uninstall webodm -n webodm`. Uninstalling keeps the data:
the Postgres and Redis queue volumes, the site, data, backup, MinIO and Caddy
claims (`helm.sh/resource-policy: keep`) and the Secret, so a reinstall picks
up where it left off. Delete the namespace to remove everything.

## What gets deployed

| Workload | Kind | Notes |
|---|---|---|
| `frappe-web`, `frappe-worker`, `frappe-scheduler`, `frappe-socketio` | Deployment | Same image, told apart by `FRAPPE_ROLE`. Services for `web` (8000) and `socketio` (9000, client-IP affinity) |
| `caddy` | Deployment + Service | The edge. NodePort (dev), LoadBalancer (prod) or behind an Ingress |
| `geospatial`, `plugin-runner`, `provisioner`, `nodeodm` | Deployment + Service | `pluginRunner`, `provisioner`, `nodeodm` have an `enabled` switch |
| `postgres`, `redis-queue` | StatefulSet + claim | Data survives rescheduling |
| `redis-cache` | StatefulSet | Ephemeral by design |
| `frappe-init-<hash>` | Job | Site bootstrap; `bootstrap.enabled` |
| `backup` | CronJob + claim | `backup.enabled` |
| `minio`, `minio-init-<hash>` | Deployment + Job | Dev object store; `minio.enabled` |
| `frappe-sites`, `frappe-data`, `plugin-sandbox` | PersistentVolumeClaim | Shared between pods, see [Storage](#storage) |

Resource names are fixed and equal to the Compose service names, because the
stack finds its peers by those names (Caddy's upstreams, the seeded
`nodeodm:3000` processing node). **Install one release per namespace.**

## Startup order

Nothing serves traffic before the site bootstrap has finished:

1. `frappe-init-<hash>` is an ordinary Job (not a Helm hook). It waits for
   Postgres and Redis, then creates the site on first install or runs
   `migrate` on later ones.
2. Each Frappe pod has a `wait-for-bootstrap` init container running
   `kubectl wait --for=condition=complete job/frappe-init-<hash>` (and the
   MinIO bucket Job in dev). It holds on install, on upgrade, and whenever a
   pod is rescheduled later, which is why the Job has no TTL.
3. `frappe-web` only becomes Ready, and so only receives traffic from Caddy,
   once `/api/method/ping` answers.

The `<hash>` covers the Frappe image and the site settings. A Job's pod
template is immutable, so a changed image produces a *new* Job, which runs the
migration, and the new pods wait for exactly that one. An upgrade that changes
neither leaves the Job and the pods alone. Force a re-run with
`--set bootstrap.runId=$(date +%s)`.

With `bootstrap.enabled=false` the Job and the wait are both dropped; use it
only when the site already exists on the `frappe-sites` claim and you run
migrations yourself.

## Health checks

| Workload | Probe |
|---|---|
| `frappe-web` | `GET /api/method/ping` |
| `frappe-socketio` | `GET /socket.io/?EIO=4&transport=polling` |
| `frappe-worker`, `frappe-scheduler` | `files/probe.py`: Redis queue answers, and this pod's RQ worker is registered / the scheduler process is running |
| `geospatial`, `plugin-runner`, `provisioner` | `GET /health` |
| `nodeodm` | `GET /info` |
| `postgres` | `pg_isready` |
| `redis-cache`, `redis-queue` | `redis-cli ping` |
| `caddy` | `GET /healthz` on an internal listener (8081) |
| `minio` | `/minio/health/ready`, `/minio/health/live` |

## Secrets

All credentials live in one Secret (`secrets.name`, default `webodm-secrets`)
and reach the pods only as `secretKeyRef` environment variables, key by key:
the plugin sandbox gets none, geospatial only its own S3 identity, and so on.
The chart never writes a credential into a template or ConfigMap.

| Key | Needed |
|---|---|
| `db_password`, `admin_password`, `frappe_admin_password`, `redis_cache_password`, `redis_queue_password`, `provisioner_api_token`, `node_token_secret` | always |
| `minio_root_user`, `minio_root_password` | `minio.enabled` |
| `s3_app_access_key_id`, `s3_app_secret_access_key` | object storage with static keys (required with MinIO) |
| `s3_geospatial_access_key_id`, `s3_geospatial_secret_access_key` | same, for the geospatial identity |
| `aws_provisioner_access_key_id`, `aws_provisioner_secret_access_key` | `provisioner.provider=aws` with static keys |
| `cloudflare_api_token` / `route53_access_key_id`, `route53_secret_access_key` | `caddy.tls.dnsProvider` |
| `backup_s3_access_key`, `backup_s3_secret_key` | `backup.s3.bucket` with static keys |

Two ways to provide it:

- **Existing Secret (default, recommended).** Create it yourself, or run
  `scripts/k8s-init-secrets.sh [namespace] [name]` for random values.
  `helm template` output then contains no secret material, and CI enforces
  that for both overlays.
- **Created from values.** `secrets.create=true` plus `secrets.values.<key>`,
  passed at install time from a file that is not in git
  (`-f secrets.local.yaml`). The values are then part of the rendered
  manifests and of Helm's release record; prefer the first option.

Values must not end in a newline (`kubectl create secret --from-file` on the
Compose `secrets/*.txt` files keeps one: strip it first). After changing a
Secret, restart the consumers: `kubectl -n webodm rollout restart deploy`.

## Storage

- **Object storage** (`objectStorage.*`) is the canonical store for task
  inputs and outputs. Dev: `minio.enabled=true` deploys MinIO with a claim,
  creates the bucket and a MinIO user per app/geospatial key pair, and points
  both services at `http://minio:9000` with path-style addressing. Prod: set
  `objectStorage.bucket`/`region` (and `endpointUrl` for non-AWS stores);
  MinIO is not deployed.
- **Shared claims.** `frappe-sites` (site config and the file serving cache),
  `frappe-data` and `plugin-sandbox` are mounted by several pods. They default
  to ReadWriteOnce, which attaches to one node, so with
  `persistence.colocate=true` those pods carry a pod affinity that keeps them
  on the same node. For a multi-node spread, give all three a ReadWriteMany
  class and set `persistence.colocate=false`.
- `storageClass` applies to every claim; empty uses the cluster default.

## The edge

Caddy stays in the cluster rather than being replaced by an Ingress, because it
owns the admin-subdomain IP whitelist, the SocketIO host fix-ups, the security
headers and the content security policy. Its Caddyfile is `files/Caddyfile`:
the site blocks mirror `infra/caddy/Caddyfile` and must be changed together
with it.

| Setting | Effect |
|---|---|
| `caddy.service.type` | `NodePort` (dev), `LoadBalancer` (prod), `ClusterIP` (behind the Ingress) |
| `caddy.service.externalTrafficPolicy` | `Local` (default): no SNAT, so Caddy sees the real client IP for the whitelist |
| `caddy.tls.mode` | `auto` public certificates (ACME), `internal` Caddy's own CA, `off` plain HTTP behind a TLS-terminating proxy |
| `caddy.tls.dnsProvider` | `cloudflare` or `route53` for DNS-01 when ports 80/443 are not reachable from the internet |
| `caddy.proxyProtocol` | Accept the PROXY protocol header from the listed load balancer CIDRs |
| `caddy.trustedProxies` | Trust `X-Forwarded-For` from these CIDRs (an ingress controller) |
| `caddy.ingress` | Optional Ingress in front of Caddy; requires `tls.mode=off` |

**Client IP and the admin whitelist.** `admin.<domain>` answers 403 to any
client outside `site.adminWhitelist`, so Caddy must see the real client address:

- `externalTrafficPolicy: Local` covers NodePort and most LoadBalancers.
- If the load balancer cannot preserve the source address, enable the PROXY
  protocol on both sides: the provider's Service annotation under
  `caddy.service.annotations` (AWS NLB:
  `service.beta.kubernetes.io/aws-load-balancer-proxy-protocol: "*"`) and
  `caddy.proxyProtocol.enabled=true` with `allow` set to the load balancer's
  addresses. Turn it on in Caddy first: a load balancer that sends the header
  to a Caddy that does not expect it breaks every connection, while Caddy
  ignores the header from any address outside `allow`.
- Behind an Ingress, set `caddy.trustedProxies` to the controller's pod CIDR.

SocketIO keeps per-client state across HTTP polling requests, so its Service
uses client-IP session affinity and `frappe.socketio.replicas` defaults to 1.

## Checking the chart

```bash
scripts/check-helm.sh
```

Runs `helm lint` and `helm template` for the defaults and both overlays (plus
the Ingress, PROXY-protocol and minimal variants), validates the output with
`kubeconform` when installed, and fails if any credential appears in the
rendered manifests. CI runs the same script on every push and pull request.
