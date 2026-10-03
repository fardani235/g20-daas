## 1. Chart skeleton and values

- [ ] 1.1 Create `infra/helm/webodm` (`Chart.yaml`, `values.yaml`, `_helpers.tpl`, `NOTES.txt`) with values for images, domain, storage class, resources, secrets, S3 and component toggles; verify `helm lint` passes.
- [ ] 1.2 Define the value surface for both environments (site domain, image tag/digest + pull secret, storage class and PVC sizes, resource requests/limits, nodeodm/backup/s3 toggles) and verify `helm template` renders with no missing required value.

## 2. Config, secrets and stateful services

- [ ] 2.1 Add Secret handling (reference an existing Secret by name, or create one from values) plus a ConfigMap for non-secret settings; verify no credential value appears anywhere in `helm template` output.
- [ ] 2.2 Add the PostgreSQL StatefulSet + headless Service + `volumeClaimTemplate`, and the two Redis StatefulSets (cache on `emptyDir`, queue on a PVC) with their auth from a Secret; verify the PVCs bind and the pods become ready on a local cluster.
- [ ] 2.3 Add the dev object store: a MinIO Deployment with a PVC and a Service plus a bucket-init Job (both behind a toggle), and the S3 settings (endpoint, path-style, region, credentials from a Secret) that point the application and the geospatial service at it; verify the bucket exists before the app pods start and an upload round-trips through MinIO.

## 3. Frappe workloads

- [ ] 3.1 Add the `frappe-init` Job and the wait-gate `initContainer` on the app Deployments; verify from a clean cluster that the Job completes first and the app pods only start after the site is ready.
- [ ] 3.2 Add the `frappe-web` Deployment + Service with `/api/method/ping` probes, the `frappe-worker` and `frappe-scheduler` Deployments, and the `frappe-socketio` Deployment + Service with client-IP affinity and one replica; verify the pods become ready and restart on a failed probe.
- [ ] 3.3 Verify end to end that login and the API answer through the web Service and a real-time SocketIO connection stays on one pod.

## 4. Service workloads

- [ ] 4.1 Add the `geospatial`, `plugin-runner` and `provisioner` Deployments + Services with their `/health` probes; verify each becomes ready.
- [ ] 4.2 Add the `nodeodm` Deployment behind a toggle and wire the `provisioner`'s AWS credentials from a Secret; verify nodeodm is reachable when enabled and cleanly absent when disabled.
- [ ] 4.3 Add the `backup` CronJob behind a toggle; verify the schedule renders and the job can be run manually.

## 5. Edge and ingress

- [ ] 5.1 Add the `caddy` Deployment with the Caddyfile as a ConfigMap and a Service (NodePort for dev, LoadBalancer/Ingress for prod); verify the SPA, API, assets/private files and SocketIO all route through it.
- [ ] 5.2 Set `externalTrafficPolicy` and document the proxy-protocol option so the admin subdomain IP whitelist sees the real client IP; verify a denied source gets 403 on the admin host.

## 6. Environment overlays

- [ ] 6.1 Add `values.dev.yaml` for a self-contained single-node install (in-cluster Postgres/Redis, MinIO on, nodeodm on, external S3 off, NodePort, shrunk resources, `nodeodm`/`backup` disable-able) and the shared site/files/plugin-sandbox PVCs; verify a clean minikube install comes up with no external dependency and that files written by the worker are visible to web.
- [ ] 6.2 Add `values.prod.yaml` (real domain with TLS, external S3 and MinIO off, resource requests, pinned image digests, LoadBalancer/Ingress, image pull secret); verify it renders and document the required cluster prerequisites.

## 7. CI and documentation

- [ ] 7.1 Add a CI job that runs `helm lint` and `helm template` for both values files, failing the build on an invalid render.
- [ ] 7.2 Write `docs/deployment/kubernetes-deployment-guide.md` and the chart `README`/`NOTES`: prerequisites (storage class, GHCR pull secret, DNS), install/upgrade/uninstall commands, the dev overlay, the prod overlay, and the manual data-migration path from Compose (database dump/restore, file copy to the bucket).

## 8. Verification

- [ ] 8.1 `helm lint` and `helm template` pass for `values.dev.yaml` and `values.prod.yaml`, and the rendered prod output contains no secrets.
- [ ] 8.2 On a local single-node cluster: install the dev overlay, confirm every pod is ready and MinIO holds the bucket, then log in, load the SPA, upload an image and view a tile; delete the Postgres and a web pod and confirm the data and the uploaded file return.
- [ ] 8.3 Review the rendered prod manifests against the prerequisites checklist (storage class, pull secret, DNS/TLS, external S3) and record the go-live steps in the guide.
