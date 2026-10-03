## Why

The only documented production path is a single VM running the Docker Compose
stack: no health-based rollouts, manual restarts, hand-managed TLS/secret files,
and every service on one host. Kubernetes gives declarative, reproducible
deployments with liveness/readiness gating, persistent volumes, and per-
environment overlays — and the app images are already published to GHCR and the
Frappe image already dispatches roles (init / web / worker / scheduler /
socketio), so the stack maps onto Kubernetes cleanly. This adds that path
without disturbing local development.

## What Changes

- **One Helm chart** (`infra/helm/webodm`) that packages the whole stack, with
  values for image tags, domain, storage class, resources, secrets and feature
  toggles.
- **Two environments** out of the same chart: `values.dev.yaml` for a local
  single-node cluster (minikube) — self-contained with an in-cluster object
  store — and `values.prod.yaml` for a real cluster: domain/TLS, external S3,
  resource requests, pinned images.
- **Workload mapping**: Deployments for the stateless services (frappe web /
  worker / scheduler / socketio, caddy, geospatial, plugin-runner, provisioner,
  nodeodm), StatefulSets with PVCs for PostgreSQL (PostGIS) and Redis, a Job for
  the one-time `frappe-init` bootstrap, and a CronJob for backups.
- **Startup ordering and health**: the init Job runs first and the app pods wait
  for it; every workload gets liveness/readiness probes reusing the endpoints the
  Compose stack already health-checks.
- **Persistence**: Postgres data on a PersistentVolumeClaim so it survives pod
  rescheduling; queue Redis persisted, cache Redis ephemeral.
- **Edge/TLS**: Caddy stays in-cluster (it owns the admin-subdomain IP
  whitelist, SocketIO host fixups and security headers) and is exposed via a
  Service — NodePort for dev, LoadBalancer/Ingress for prod.
- **Object storage**: an in-cluster MinIO in the dev overlay (mirroring the dev
  Compose stack) and external S3 in prod, both configured through values and a
  secret.
- **Non-goals:** replacing Docker Compose for local dev; migrating existing
  Compose data into the cluster; HA/multi-replica Postgres; autoscaling; a
  service mesh; External Secrets; and moving the on-demand EC2 compute into the
  cluster.

## Capabilities

### New Capabilities

- `kubernetes-deployment`: how the stack is packaged and run on Kubernetes — the
  chart and its values, the workload kinds and health gating, startup ordering,
  persistence, secrets, external S3, the in-cluster edge, and the dev/prod
  overlays.

### Modified Capabilities

_(none — this is new deployment packaging; no existing requirement changes.)_

## Impact

- **New files**: `infra/helm/webodm/` (chart, templates, values, `values.dev.yaml`,
  `values.prod.yaml`, a `README`/NOTES), and a Kubernetes deployment guide under
  `docs/deployment/`.
- **CI**: an `helm lint` + `helm template` (dev and prod) job so the chart stays
  renderable; no application code changes.
- **Ops**: cluster prerequisites (storage class, image pull secret for GHCR,
  ingress/LoadBalancer, DNS), and the manual/fresh-install caveat for existing
  Compose data.
