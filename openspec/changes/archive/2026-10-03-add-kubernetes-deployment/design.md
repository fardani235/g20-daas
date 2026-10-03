## Context

See `proposal.md` for motivation. Current state that shapes the approach:

- `docker-compose.yml` runs 13 services: caddy, postgres (PostGIS), redis-cache
  and redis-queue, frappe-init/web/worker/scheduler/socketio, geospatial,
  plugin-runner, provisioner, nodeodm, backup. All app images are already built
  and published to GHCR (`webodm-frappe`, `webodm-caddy`, `webodm-geospatial`,
  `webodm-plugin-runner`, `webodm-provisioner`, `webodm-backup`).
- The Frappe image dispatches every role from one image via `FRAPPE_ROLE`
  (`init | web | worker | scheduler | socketio`), so roles are Deployments of the
  same image rather than separate builds.
- Health endpoints already exist: frappe-web `/api/method/ping`, socketio
  `/socket.io/?EIO=4&transport=polling`, geospatial `/health`, plugin-runner and
  provisioner `/health`, nodeodm `/info`, plus Postgres `pg_isready` and Redis
  `ping`.
- Caddy carries behaviour a generic Ingress does not: the admin subdomain IP
  whitelist, SocketIO `Host` fixups, HSTS/security headers and a CSP.
- Stateful data sits in named volumes; credentials come from `secrets/*` files;
  backups are a cron in the `webodm-backup` image; object storage is external S3
  (the dev compose adds MinIO, prod uses S3).

## Goals / Non-Goals

**Goals:**
- A self-contained dev install on a single-node cluster and a prod install on a
  real cluster, from one chart.
- Correct startup ordering and health gating so pods do not serve before the
  site exists and unhealthy pods are replaced.
- Durable Postgres and queue data across pod reschedules.
- No application code changes and no secrets in templates.

**Non-Goals:**
- Replacing Compose for local development, or automated migration of existing
  Compose volumes into the cluster.
- HA Postgres / multi-replica stateful services, autoscaling, a service mesh,
  External Secrets, or moving on-demand EC2 compute into the cluster.
- Removing Caddy in favour of cert-manager + a Kubernetes Ingress.

## Decisions

### 1. One Helm chart with per-environment values

Ship `infra/helm/webodm` with a `values.yaml` default plus `values.dev.yaml`
and `values.prod.yaml` (Helm has no overlays; values files are the equivalent).

- **Why:** the stack is already one unit with shared config; values files give
  the requested dev/prod split without a second tool.
- **Alternatives considered:** Kustomize base + overlays (equals the capability,
  but Helm's templating is a better fit for the many parameterized values) and
  plain manifests (no environment handling).

### 2. Keep Caddy in-cluster as the edge

Run the existing `webodm-caddy` image as a Deployment + Service, exposed as
NodePort for dev and LoadBalancer (or fronted by an Ingress) for prod.

- **Why:** it owns the admin IP whitelist, SocketIO `Host` fixups, security
  headers and CSP; rebuilding those as Ingress annotations is risk with no gain.
- **Alternatives considered:** a Kubernetes Ingress + cert-manager (loses the
  custom logic), or exposing every service directly (loses the single entry and
  TLS).

### 3. Frappe roles as separate Deployments of one image

`frappe-web`, `frappe-worker`, `frappe-scheduler`, `frappe-socketio` are four
Deployments of the same image, each with its `FRAPPE_ROLE`.

- **Why:** matches the image's existing dispatch, lets a busy worker scale or
  restart without touching web, and keeps one image to publish.
- **Alternative considered:** one pod running multiple roles (couples failure
  domains and prevents independent scaling).

### 4. Bootstrap ordering via a Job plus a wait gate

`frappe-init` runs as a Job; the app Deployments carry an initContainer that
waits for the site to be ready before starting.

- **Why:** Helm hooks can race with readiness, and running migrations in every
  app pod's initContainer would run them many times. A single Job plus a gate is
  deterministic.
- **Alternative considered:** a Helm `post-install` hook alone (races), or
  initContainers that run migrations (duplicate side effects).

### 5. StatefulSets for Postgres and Redis; object storage is configurable

PostgreSQL and Redis are StatefulSets with `volumeClaimTemplates`; the queue
Redis gets a PVC while the cache Redis may use `emptyDir`. Object storage is an
in-cluster MinIO in the dev overlay and external S3 in prod (decision 11).

- **Why:** databases need stable identity and durable storage; object storage is
  the pluggable canonical-store seam the app already has.
- **Alternatives considered:** a single Postgres Deployment with a shared PVC
  (no stable identity), or one external object store for both environments (dev
  would need cloud credentials).

### 6. Secrets by reference, never in templates

The chart either references an existing Secret by name or creates one from
values, and every credential is read from a Secret.

- **Why:** the Compose `secrets/*` files have no Kubernetes analogue, and
  rendered manifests must never contain credentials.
- **Alternative considered:** ConfigMap data (wrong: not secret) or External
  Secrets (deferred to a prod overlay follow-up).

### 7. NodeODM and provisioner are toggles

The in-cluster `nodeodm` Deployment is on for dev and optional in prod; the
`provisioner` runs in-cluster with AWS credentials from a Secret when on-demand
compute is used.

- **Why:** dev needs a processing engine with no cloud; prod may rely on EC2.
- **Alternative considered:** always run nodeodm in-cluster (wastes a node) or
  always rely on EC2 (breaks the self-contained dev install).

### 8. Probes reuse the existing health endpoints

Liveness/readiness commands mirror the Compose healthchecks per service.

- **Why:** the endpoints are already correct and tested; no new health surface.
- **Alternative considered:** TCP-only probes (weaker signal).

### 9. SocketIO needs affinity

The socketio Service uses client-IP session affinity (and a single replica by
default), because Socket.IO's HTTP-polling transport is stateful across requests.

- **Why:** without affinity, polling handshakes land on different pods and break.
- **Alternative considered:** scaling socketio freely behind a load balancer
  (breaks polling; would need a shared adapter).

### 10. Images pinned by values, pulled with a GHCR pull secret

Values carry the image repository plus tag/digest, and an optional
`imagePullSecret` is set on every pod.

- **Why:** reproducible rollouts and private-registry pulls.
- **Alternative considered:** `latest` tags (unreproducible).

### 11. Dev object storage is in-cluster MinIO; prod is external S3

The dev overlay runs a MinIO Deployment with a PVC, a Service and a bucket-init
Job that creates the bucket before the app pods use it; the application and the
geospatial service get the MinIO endpoint (path-style) and credentials from a
Secret. The prod overlay points the same settings at external S3 and deploys no
MinIO.

- **Why:** it mirrors the existing dev Compose stack, keeps the dev install fully
  self-contained, and makes dev exercise the real S3 code path instead of the
  host-disk fallback.
- **Alternatives considered:** host-disk storage on the shared site PVC for dev
  (works on one node but does not exercise S3 and ties state to that node) or
  external S3 for dev (needs cloud credentials).

### 12. The Frappe site directory is a shared PVC

`frappe_sites` (site code, config and the serving cache), `frappe_data` and
`plugin_sandbox` remain PersistentVolumeClaims mounted by every pod that shares
them. On the single-node dev cluster a ReadWriteOnce claim is mounted by all
these pods on one node; prod removes the shared-file dependency by keeping
object storage external, so only the cache stays on disk.

- **Why:** even with MinIO/S3 as the system of record, the upload path writes to
  the host first and serves through the cache, and several pods must see the same
  site directory.
- **Alternatives considered:** `emptyDir` for the site directory (loses the site
  on restart) or requiring a ReadWriteMany claim (not available on minikube by
  default).

## Risks / Trade-offs

- **[Single-node dev is resource-tight with nodeodm + all services]** → dev
  values shrink replicas/resources and allow disabling `nodeodm` and `backup`.
- **[MinIO plus the full stack on one minikube node]** → keep MinIO small in dev
  values and verify it fits alongside nodeodm, or run MinIO with nodeodm
  disabled.
- **[App and geospatial reach the object store with the wrong addressing]** → set
  the endpoint and path-style from values and give both services the same
  endpoint; verify a COG write/read through MinIO.
- **[Bucket not ready when the app starts]** → the bucket-init Job runs before
  the app pods, gated by the startup wait.
- **[Shared site PVC is ReadWriteOnce]** → single-node dev only; prod avoids the
  shared-file dependency by keeping object storage external (decision 12).
- **[Admin IP whitelist sees the wrong client IP behind NodePort/LoadBalancer]**
  → set `externalTrafficPolicy: Local` and document the proxy-protocol option;
  keep the whitelist defaulting to private ranges so it fails closed.
- **[SocketIO breaks under multiple replicas without affinity]** → client-IP
  affinity and default single replica (decision 9).
- **[PVC binding depends on the cluster's storage class]** → make the storage
  class and sizes values; document the minikube default.
- **[Private GHCR images fail to pull]** → values-supplied `imagePullSecret`.
- **[Existing Compose data is not migrated automatically]** → document a manual
  `pg_dump`/restore and volume copy for the S3-backed files; a fresh install is
  the supported path.
- **[Init ordering race if the Job is disabled]** → the wait gate keeps app pods
  from serving until the site is ready even if the Job is skipped.

## Migration Plan

1. Add the chart and values; render it with `helm template` for dev and prod and
   fix until both are valid (no cluster needed).
2. Add a CI job that runs `helm lint` and `helm template` for both values files.
3. Install the dev overlay (with its in-cluster MinIO) on a local single-node
   cluster and smoke-test login, the SPA, tiles, a task, and a restart.
4. For prod, install on a cluster with a storage class and a GHCR pull secret;
   move existing data by dumping the Compose database and restoring into the
   Postgres PVC, and copying stored files into the bucket (documented).

Rollback: uninstall the Helm release; because this is additive, Compose remains
the working deployment throughout.

## Open Questions

None blocking. External Secrets, a cert-manager Ingress alternative and
multi-replica Postgres are deliberately deferred as non-goals.
