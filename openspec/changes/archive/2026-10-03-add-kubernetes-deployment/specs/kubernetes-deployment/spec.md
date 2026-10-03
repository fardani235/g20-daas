## Purpose

Defines how the WebODM stack is packaged and operated on Kubernetes: a single
Helm chart with dev and prod values, the workload each service maps to, startup
ordering and health gating, persistence, secrets and external object storage,
and the in-cluster edge.

## ADDED Requirements

### Requirement: A single Helm chart packages the stack

The whole stack SHALL be deployable from one Helm chart, parameterized by values
for image tags, domain, storage class, resources, secrets and optional
components. Rendering the chart with the dev or prod values MUST produce valid
Kubernetes manifests without editing templates.

#### Scenario: Chart renders for each environment

- **WHEN** the chart is rendered with the dev values and with the prod values
- **THEN** both produce a complete, valid manifest set with no missing required
  value

#### Scenario: Reuses published images

- **WHEN** the chart is installed
- **THEN** it pulls the already-published container images, and every Frappe role
  (web, worker, scheduler, socketio) runs from the same image selected by its
  role environment variable

### Requirement: Services map to the right workload kinds

Each service SHALL be represented by the Kubernetes workload kind appropriate to
its lifecycle: Deployments for the stateless services, StatefulSets with
PersistentVolumeClaims for the stateful ones, a Job for the one-time site
bootstrap, and a CronJob for scheduled backups.

#### Scenario: Stateless services

- **WHEN** the chart is installed
- **THEN** the web, worker, scheduler, socketio, edge proxy, geospatial,
  plugin-runner, provisioner and node services are Deployments with a Service
  where they need to be reached

#### Scenario: Stateful services

- **WHEN** the chart is installed
- **THEN** PostgreSQL and Redis run as StatefulSets backed by PersistentVolumeClaims

#### Scenario: Bootstrap and backup

- **WHEN** the chart is installed
- **THEN** the site bootstrap runs as a Job and backups are scheduled as a
  CronJob, and both can be disabled by values

### Requirement: Startup ordering and health gating

The stack SHALL NOT serve traffic before the site bootstrap has completed, and
every workload SHALL expose liveness and readiness probes so unhealthy pods are
restarted and not sent traffic.

#### Scenario: Bootstrap completes before serving

- **WHEN** the cluster starts from nothing
- **THEN** the site bootstrap Job runs to completion first, and the web, worker
  and scheduler pods become ready only after it has succeeded

#### Scenario: Probes reflect real health

- **WHEN** a workload becomes unhealthy
- **THEN** its readiness probe takes it out of service and its liveness probe
  restarts it, using the endpoints the application already exposes for health

### Requirement: Persistence and object storage

Database and queue data SHALL survive a pod being rescheduled. The canonical
object store SHALL be configurable: the dev overlay SHALL run an in-cluster
object store (MinIO) so it is self-contained, and the prod overlay SHALL use
external S3 supplied through values and a secret. In both cases the application
and the geospatial service SHALL point at the configured store.

#### Scenario: Database survives rescheduling

- **WHEN** the PostgreSQL pod is deleted and recreated
- **THEN** it reattaches its PersistentVolumeClaim and the site data is intact

#### Scenario: Dev uses an in-cluster object store

- **WHEN** the dev values are installed
- **THEN** an in-cluster object store runs with its bucket created before the
  application uses it, and the application and geospatial service read and write
  through its endpoint

#### Scenario: Prod uses external S3

- **WHEN** the prod values are installed
- **THEN** the application and services use the external S3 endpoint and
  credentials supplied by values/secret, and no in-cluster object store is
  deployed

#### Scenario: Uploaded files survive a pod restart

- **WHEN** an image is uploaded and the pod that received it is restarted
- **THEN** the image is still available from the configured object store

### Requirement: Configuration and secrets

Image tags, the site domain, storage class, resources and feature toggles SHALL
come from values, and credentials SHALL be supplied as Kubernetes Secrets (an
existing Secret referenced by name, or one created from values), never baked
into templates.

#### Scenario: Secrets are not baked in

- **WHEN** the manifests are rendered
- **THEN** no credential value is written into a template or ConfigMap; workload
  pods read credentials from a Secret by reference

#### Scenario: Existing secret

- **WHEN** an existing Secret name is supplied in values
- **THEN** the chart references it instead of creating one

### Requirement: In-cluster edge and environment overlays

The edge reverse proxy SHALL run in-cluster and preserve the stack's routing,
TLS, admin-IP whitelist and SocketIO behaviour, exposed through a Service that is
NodePort for the dev overlay and LoadBalancer/Ingress for the prod overlay. The
chart SHALL ship a dev overlay that runs self-contained on a single node and a
prod overlay for a real domain with TLS.

#### Scenario: Dev overlay on a single node

- **WHEN** the dev values are installed on a local single-node cluster
- **THEN** the stack comes up self-contained (in-cluster database, cache, object
  store and processing node) and is reachable without external dependencies

#### Scenario: Prod overlay

- **WHEN** the prod values are installed
- **THEN** the site is served on the configured domain over TLS, the admin
  surface keeps its IP whitelist, and object storage points at external S3

#### Scenario: Real-time updates still work

- **WHEN** a signed-in user receives a real-time update through the edge proxy
- **THEN** the SocketIO connection is routed to the socketio service with the
  correct host, as it is under Compose
