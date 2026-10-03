# WebODM on Kubernetes

Deploy the stack to a Kubernetes cluster with the Helm chart in
[`infra/helm/webodm`](../../infra/helm/webodm/README.md). One chart, two
overlays:

- `values.dev.yaml`: a single-node cluster (minikube), fully self-contained.
- `values.prod.yaml`: a real cluster with a public domain, TLS and external S3.

Docker Compose is still how you develop locally and is still a supported way
to run a single VM ([production guide](production-deployment-guide.md),
[runbook](../runbook.md)). Nothing here changes it.

**Not covered:** highly available Postgres, autoscaling, a service mesh,
External Secrets, running the on-demand EC2 nodes inside the cluster, and
automatic migration of Compose data. A fresh install is the supported path;
[existing data moves by hand](#6-moving-existing-data-across).

## Contents

1. [What you get](#1-what-you-get)
2. [Prerequisites](#2-prerequisites)
3. [Development install (minikube)](#3-development-install-minikube)
4. [Production install](#4-production-install)
5. [Upgrade, roll back, uninstall](#5-upgrade-roll-back-uninstall)
6. [Moving existing data across](#6-moving-existing-data-across)
7. [Operations](#7-operations)
8. [Troubleshooting](#8-troubleshooting)

## 1. What you get

| Compose service | Kubernetes |
|---|---|
| `frappe-web`, `frappe-worker`, `frappe-scheduler`, `frappe-socketio` | Four Deployments of the one Frappe image (`FRAPPE_ROLE` selects the role) |
| `frappe-init` | A Job; the Frappe pods wait for it in an init container |
| `caddy` | Deployment + Service (NodePort / LoadBalancer / behind an Ingress) |
| `geospatial`, `plugin-runner`, `provisioner`, `nodeodm` | Deployment + ClusterIP Service each |
| `postgres`, `redis-queue` | StatefulSets with a PersistentVolumeClaim |
| `redis-cache` | StatefulSet, no volume (a cache) |
| `backup` | CronJob that runs the Frappe image and writes to its own claim |
| `minio` (dev override) | Deployment + claim + bucket Job, dev overlay only |
| `secrets/*.txt` | One Kubernetes Secret, same key names |
| `frontend` / `backend` / `sandbox` networks | NetworkPolicies for the plugin sandbox, geospatial and the provisioner |

What Kubernetes adds over the VM: rollouts gated on health probes, automatic
restarts, data volumes that follow a rescheduled pod, and credentials in a
Secret instead of files on a host.

Service names inside the cluster are the Compose names (`postgres`,
`frappe-web`, `nodeodm`, ...). Use a dedicated namespace and one release per
namespace.

## 2. Prerequisites

| Need | Dev (minikube) | Prod |
|---|---|---|
| Kubernetes 1.27+ and `kubectl` | `minikube start --cpus 4 --memory 8g --disk-size 60g` | Your cluster |
| Helm 3.14+ | yes | yes |
| A StorageClass that provisions volumes | minikube's default `standard` | A default class, or set `storageClass`. Prefer one with volume expansion |
| Registry access | The images pull anonymously while the GHCR packages are public | A pull secret (below) if they are private |
| DNS | `/etc/hosts` entries | `A`/`CNAME` records for `<domain>` **and** `admin.<domain>` |
| Object storage | In-cluster MinIO (chart) | An S3 bucket and two key pairs or IAM roles, see [`docs/on-demand-processing/deployment.md`](../on-demand-processing/deployment.md) |
| NetworkPolicy enforcement | Optional | A CNI that enforces it (Calico, Cilium, ...). Without one the policies are accepted and ignored, and the plugin sandbox can reach the network |

Sizing: the dev overlay requests about 1 CPU and 3 GiB in total and its
images need about 12 GB of disk. NodeODM is the heavy one; real processing
needs far more memory than it requests. See [the switches](#a-tight-node).

### Registry pull secret

```bash
kubectl create namespace webodm
kubectl -n webodm create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=<github-user> \
  --docker-password=<token with read:packages>
```

`values.prod.yaml` references it as `imagePullSecrets: [{name: ghcr-pull}]`.
For dev, add `--set 'imagePullSecrets[0].name=ghcr-pull'` if the packages are
private.

### Credentials Secret

The chart reads every credential from one Secret, `webodm-secrets` by default.
Create it with random values:

```bash
scripts/k8s-init-secrets.sh webodm          # [namespace] [secret-name]
```

This generates the seven keys the stack always needs plus MinIO and S3 key
pairs for the dev overlay, and never overwrites an existing Secret. The full
key list is in the [chart README](../../infra/helm/webodm/README.md#secrets).
For production add the cloud credentials you use:

```bash
kubectl -n webodm patch secret webodm-secrets --type merge -p '{"stringData":{
  "s3_app_access_key_id": "AKIA...",        "s3_app_secret_access_key": "...",
  "s3_geospatial_access_key_id": "AKIA...", "s3_geospatial_secret_access_key": "..."}}'
```

(The script generates dev-only values for those four keys; overwrite them, or
remove them to use an IAM role instead.) Manage this Secret however you
manage others: Sealed Secrets, SOPS, your CI. The alternative,
`secrets.create=true` with `secrets.values`, puts the values into the Helm
release, so keep such a values file out of git.

## 3. Development install (minikube)

```bash
minikube start --cpus 4 --memory 8g --disk-size 60g

scripts/k8s-init-secrets.sh webodm
helm upgrade --install webodm infra/helm/webodm -n webodm \
  -f infra/helm/webodm/values.dev.yaml
```

Watch it come up. The first install pulls several GB of images, then the
bootstrap Job creates the site (a few minutes); the four Frappe pods sit in
`Init:0/1` until it completes:

```bash
kubectl -n webodm get pods -w
kubectl -n webodm logs -f -l app.kubernetes.io/component=frappe-init
```

Open it:

```bash
echo "$(minikube ip) webodm.local admin.webodm.local" | sudo tee -a /etc/hosts
# App:   https://webodm.local:32443/
# Desk:  https://admin.webodm.local:32443/
kubectl -n webodm get secret webodm-secrets -o jsonpath='{.data.admin_password}' | base64 -d; echo
```

The certificate comes from Caddy's internal CA, so the browser warns once.
Use the HTTPS port directly: the plain-HTTP NodePort (32080) redirects to port
443, which a NodePort does not serve. With the Docker driver on macOS or
Windows the node IP is not routable; use
`kubectl -n webodm port-forward svc/caddy 32443:443` and map the hostnames to
`127.0.0.1`.

What the overlay runs: Postgres, both Redis instances, MinIO (bucket
`webodm-dev`, created by a Job, with separate MinIO users for the app and
geospatial), NodeODM, the plugin runner, the provisioner (idle,
`provider: none`), a nightly backup CronJob and Caddy on NodePorts 32080/32443.

### A tight node

```bash
helm upgrade --install webodm infra/helm/webodm -n webodm \
  -f infra/helm/webodm/values.dev.yaml \
  --set nodeodm.enabled=false --set backup.enabled=false
```

`nodeodm.enabled=false` drops the 3 GB processing node (everything except
running a task still works); `backup.enabled=false` drops the CronJob and its
volume. `pluginRunner.enabled` and `provisioner.enabled` exist too.

## 4. Production install

1. **Namespace, pull secret, credentials** as in [Prerequisites](#2-prerequisites).

2. **Site values.** Do not edit `values.prod.yaml`; put your site's settings
   in a file of your own and layer it on top:

   ```yaml
   # my-site.yaml
   site:
     domain: webodm.example.com
     adminWhitelist: "203.0.113.4 198.51.100.0/24"   # who may reach admin.<domain>
   objectStorage:
     bucket: my-webodm-bucket
     region: eu-central-1
   caddy:
     tls:
       email: ops@example.com
   ```

3. **Install.**

   ```bash
   helm upgrade --install webodm infra/helm/webodm -n webodm \
     -f infra/helm/webodm/values.prod.yaml -f my-site.yaml
   ```

4. **DNS.** Point `<domain>` and `admin.<domain>` at the load balancer:

   ```bash
   kubectl -n webodm get svc caddy    # EXTERNAL-IP
   ```

   Caddy requests certificates as soon as the names resolve to it and ports
   80/443 are reachable. Certificates and the ACME account are kept on the
   `caddy-data` claim.

5. **Verify.**

   ```bash
   kubectl -n webodm get pods                         # all Running/Ready, Jobs Completed
   curl -sI https://webodm.example.com/ | head -1     # 301 to the SPA
   curl -s -o /dev/null -w '%{http_code}\n' https://admin.webodm.example.com/   # 403 unless whitelisted
   kubectl -n webodm exec deploy/geospatial -- wget -qO- http://localhost:5000/health   # lists the bucket
   ```

### What the production overlay sets

- **Pinned digests** for every image, the same builds `docker-compose.yml`
  pins. When `scripts/pin-images.sh` moves the Compose pins, copy the new
  digests into `values.prod.yaml`.
- **External S3**, no MinIO. With static keys, the `s3_app_*` and
  `s3_geospatial_*` Secret keys hold the two least-privilege identities
  ([`infra/aws/iam`](../../infra/aws/iam)). Leave those keys out to use node
  or pod IAM roles.
- **Resource requests** on everything and memory limits on most.
- **A LoadBalancer Service** with `externalTrafficPolicy: Local`.
- **`imagePullSecrets: ghcr-pull`**.
- Two `frappe-web` and two `frappe-worker` replicas; one scheduler, one
  SocketIO.

### The edge and the real client IP

Caddy stays the edge inside the cluster: it enforces the `admin.<domain>` IP
whitelist, fixes up the SocketIO `Host` handling, and sets the security headers
and content security policy. The whitelist only works if Caddy sees the real
client address, so pick one of:

| Setup | Values |
|---|---|
| LoadBalancer that preserves the source IP (default) | `caddy.service.type: LoadBalancer`, `externalTrafficPolicy: Local` |
| LoadBalancer that does not (or to be explicit): **PROXY protocol** | Enable it on the load balancer through `caddy.service.annotations` (AWS NLB: `service.beta.kubernetes.io/aws-load-balancer-proxy-protocol: "*"`) **and** set `caddy.proxyProtocol.enabled: true` with `caddy.proxyProtocol.allow` listing the load balancer's CIDRs. A load balancer sending the header to a Caddy that does not expect it breaks every connection, so enable the Caddy side first. Caddy ignores the header from addresses outside `allow` |
| Ingress controller in front (TLS at the controller) | `caddy.tls.mode: "off"`, `caddy.service.type: ClusterIP`, `caddy.ingress.enabled: true` with `className` and `tlsSecretName`, and `caddy.trustedProxies` set to the controller's pod CIDR so its `X-Forwarded-For` is believed. The Ingress must allow large uploads and WebSockets (ingress-nginx: `nginx.ingress.kubernetes.io/proxy-body-size: "0"`) |

If certificates cannot be issued over ports 80/443, use DNS-01:
`caddy.tls.dnsProvider: cloudflare` (Secret key `cloudflare_api_token`) or
`route53` (`route53_access_key_id`, `route53_secret_access_key`).

`externalTrafficPolicy: Local` means the load balancer only routes to the node
that runs the Caddy pod; its health check takes the other nodes out.

### Shared volumes

The Frappe site directory (`frappe-sites`: site config, and the serving cache
when S3 is on), the service data directory (`frappe-data`) and the plugin
sandbox (`plugin-sandbox`) are each mounted by several pods. They are
ReadWriteOnce by default, which a volume can only be on one node at a time,
so `persistence.colocate: true` gives those pods an affinity that keeps them on
one node. Task inputs and outputs do not live there in production: they are in
S3, and the volume is a refillable cache.

To spread the Frappe pods over several nodes, use a ReadWriteMany class (EFS,
Filestore, CephFS, NFS) for all three claims and turn the affinity off:

```yaml
persistence:
  colocate: false
  sites:   {accessModes: [ReadWriteMany]}
  data:    {accessModes: [ReadWriteMany]}
  sandbox: {accessModes: [ReadWriteMany]}
```

Access modes cannot be changed on an existing claim; decide before installing.

### On-demand compute

The provisioner Deployment runs in the cluster; the EC2 nodes it creates stay
outside. To enable it, set `provisioner.provider: aws`, put the
`PROVISIONER_AWS_*` settings from
[`configuration.md`](../on-demand-processing/configuration.md) under
`provisioner.env`, and add `aws_provisioner_access_key_id` /
`aws_provisioner_secret_access_key` to the Secret. The security group for the
nodes must allow TCP 3000 from the cluster's egress address.

## 5. Upgrade, roll back, uninstall

**Upgrade** resolves the current digest of every image the chart deploys and
applies them to the release, so no digest is edited by hand.
`scripts/k8s-upgrade.py` wraps `helm upgrade`, taking the same values layering
plus any extra helm flags:

```bash
scripts/k8s-upgrade.py webodm infra/helm/webodm -n webodm \
  -f infra/helm/webodm/values.prod.yaml -f my-site.yaml
kubectl -n webodm get pods -w
```

It resolves every `images.<name>` (community images included) with
`docker buildx imagetools inspect`, writes the digests to a temporary,
highest-precedence values file, and runs `helm upgrade --install`. No tracked
file is modified, and the digests that were applied are recoverable afterwards
with `helm -n webodm get values webodm` (they appear as
`images.<name>.digest`).

Prerequisites on the deploying machine: Python 3 with PyYAML, `docker` (with
buildx) and `helm`.

| Option | Effect |
|---|---|
| `--check` | Resolve and print `repository`, `tag`, `digest` per image; no helm and no cluster change. Exits non-zero if an image cannot be resolved. |
| `--dry-run[=MODE]` | Forward `--dry-run[=MODE]` to helm to review the rendered manifests with the resolved digests. |
| `--skip NAME` | Leave image `NAME` floating instead of pinning it, e.g. `--skip nodeodm`. |
| `--no-install` | Do not add `--install` to the helm command. |

Anything else after the release and chart is passed through to `helm upgrade`,
e.g. `--atomic` or `--timeout 10m`.

### Upgrading when an image tag changes

The chart deploys each `images.<name>` as `repository:tag` (optionally
`@digest`). The **tag selects the build**; `k8s-upgrade.py` resolves that tag to
its current digest at deploy time and applies it. So to roll out a new build,
point the values at the tag you want *first*, then run the script:

```bash
# values.prod.yaml: images.frappe.tag: "16.35.0"   (or pass --set below)
scripts/k8s-upgrade.py webodm infra/helm/webodm -n webodm \
  -f infra/helm/webodm/values.prod.yaml -f my-site.yaml
```

Preview with `--check` first: it prints each `repository:tag` and the digest it
resolves to without touching the cluster. A digest committed in the values file
is only a fallback — for every image it resolves the script writes the fresh
digest at the highest precedence, so the committed digest does not need editing
for the script path (keep it in step for the manual fallback below).

CI publishes each image on the default branch after its tests pass:

| Image | Tags |
|---|---|
| `webodm-frappe` (also the `backup` CronJob) | `<frappe-version>`, `<frappe-version>-<sha>`, `latest` |
| `webodm-geospatial`, `webodm-plugin-runner`, `webodm-provisioner` | `1`, `1-<sha>`, `latest` |
| `webodm-caddy` | `2`, `2-<sha>`, `latest` |

`<frappe-version>` is the `frappe-bench/apps/frappe` submodule pin
(`scripts/frappe-version.sh`). The bare `<frappe-version>` / `1` / `2` / `latest`
tags are moving: a later CI push repoints them, so re-running the script without
a tag change already picks up the newest build. Use a `<...>-<sha>` tag to pin a
specific commit.

The community images the chart also deploys (`nodeodm`, `postgres`, `redis`,
`minio`, `mc`, `kubectl`) come from their upstream registries; move those by
bumping their `tag` in the values.

**Dev (minikube).** `values.dev.yaml` inherits its tags from `values.yaml`, so
the same command works; add `--set` to bump one:

```bash
scripts/k8s-upgrade.py webodm infra/helm/webodm -n webodm \
  -f infra/helm/webodm/values.dev.yaml \
  --set images.frappe.tag=16.35.0
```

**A local build that is not in the registry.** Build it, load it into the
cluster, and keep the script from trying to resolve it:

```bash
docker buildx build --load -t ghcr.io/fardani235/webodm-frappe:my-build \
  -f frappe-bench/apps/Dockerfile \
  --build-context sites=./frappe-bench/sites --build-context root=. frappe-bench/apps
minikube image load ghcr.io/fardani235/webodm-frappe:my-build

scripts/k8s-upgrade.py webodm infra/helm/webodm -n webodm \
  -f infra/helm/webodm/values.dev.yaml \
  --skip frappe \
  --set images.frappe.tag=my-build \
  --set images.frappe.digest= \
  --set images.frappe.pullPolicy=IfNotPresent
```

`--skip frappe` leaves that image out of digest resolution (there is nothing in
the registry to resolve); `images.frappe.digest=` clears a digest pinned by an
earlier release so the tag is used. On a real cluster, push the image to your
registry instead of `minikube image load` and drop the `--skip`/`pullPolicy`
overrides so the script pins its digest normally.

A changed Frappe image or site setting produces a new `frappe-init-<hash>`
Job, which runs `migrate`. New Frappe pods wait for it before starting and old
pods keep serving until the new ones are Ready. An upgrade that changes neither
does not re-run it; to force a run: `--set bootstrap.runId=$(date +%s)`.

**Fallback — edit the committed digests.** `values.prod.yaml` still pins digests
by hand, which works without the script:

```bash
# set the new tag/digest for each changed image in values.prod.yaml first
helm upgrade --install webodm infra/helm/webodm -n webodm \
  -f infra/helm/webodm/values.prod.yaml -f my-site.yaml
```

Keep those committed digests aligned with the compose pins after
`scripts/pin-images.sh` runs.

**Roll back** the release with `helm rollback webodm <revision> -n webodm`.
This restores the manifests, not the database: a migration that already ran is
not undone, so restore a backup if the old code cannot read the new schema.

**Uninstall.**

```bash
helm uninstall webodm -n webodm
```

This removes the workloads but keeps the data: the Postgres and Redis queue
volumes, the claims marked `helm.sh/resource-policy: keep` (site directory,
service data, backups, MinIO, Caddy certificates) and the Secret. Installing
again into the same namespace reuses them, and the bootstrap Job runs
`migrate` instead of creating a site. Only the scratch claims (plugin sandbox,
NodeODM) go.

To remove everything, data included: `kubectl delete namespace webodm`.
Deleting the Secret while keeping `data-postgres-0` locks you out of the
database; keep or delete them together.

## 6. Moving existing data across

There is no automatic migration from a Compose deployment. Install fresh,
confirm it works, then move the database and the files by hand in a
maintenance window. The commands assume the Compose project runs from its
checkout and the release is `webodm` in namespace `webodm`.

Keep the same `site.name` as the old `SITE_NAME` if you can; file paths under
the site directory contain it.

### 6.1 Stop writers on both sides

```bash
# Old host: stop everything that writes, keep the database up.
docker compose stop caddy frappe-web frappe-worker frappe-scheduler frappe-socketio backup

# Cluster: stop the Frappe pods and the backup schedule.
kubectl -n webodm scale deploy frappe-web frappe-worker frappe-scheduler frappe-socketio --replicas=0
kubectl -n webodm patch cronjob backup -p '{"spec":{"suspend":true}}'
```

### 6.2 Dump and restore the database

```bash
# Old host
docker compose exec -T postgres pg_dump -U webodm -d webodm -Fc > webodm.dump

# Cluster: replace the empty database the bootstrap created.
kubectl -n webodm cp webodm.dump postgres-0:/tmp/webodm.dump
kubectl -n webodm exec -i postgres-0 -- bash -ec '
  dropdb   -U webodm --if-exists webodm
  createdb -U webodm -O webodm webodm
  pg_restore -U webodm -d webodm --no-owner --role=webodm /tmp/webodm.dump
  rm /tmp/webodm.dump'
```

Adjust `-U`/`-d` if the old deployment used other `DB_USER`/`DB_NAME` values;
`database.user` and `database.name` in the chart must match what you restore
into. The application's database password is the cluster's `db_password`, not
the old one: the dump carries no role passwords.

### 6.3 Carry over the encryption key

Frappe encrypts stored passwords and API secrets with the `encryption_key` in
`site_config.json`. Without the old key those values cannot be decrypted. (A
site that never stored an encrypted value has no key yet; then skip the
`set-config`.)

```bash
# Old host (g20-daas is the Compose project name)
docker run --rm -v g20-daas_frappe_sites:/sites:ro alpine \
  grep encryption_key /sites/webodm.local/site_config.json

# Cluster: a throwaway pod on the site volume (the Frappe pods are scaled
# down). It reuses the backup CronJob's pod spec with the command replaced.
kubectl -n webodm create job --from=cronjob/backup site-shell --dry-run=client -o yaml \
  | kubectl patch --local -f - --type=json -o yaml \
      -p '[{"op":"replace","path":"/spec/template/spec/containers/0/args","value":["sleep","3600"]}]' \
  | kubectl -n webodm apply -f -
kubectl -n webodm wait --for=condition=ready pod -l job-name=site-shell --timeout=5m

kubectl -n webodm exec job/site-shell -- bash -c 'cd /workspace/frappe-bench/sites &&
  /workspace/frappe-bench/env/bin/python -m frappe.utils.bench_helper frappe \
    --site webodm.local set-config encryption_key "<old key>"'
```

Leave `site-shell` running for the next step. (Without the backup CronJob,
`backup.enabled=false`, mount the `frappe-sites` claim in any pod of the
Frappe image instead.)

### 6.4 Copy the stored files

Which case applies depends on where the old deployment kept task files.

**Old deployment already used S3 (`WEBODM_S3_BUCKET` set).** S3 is the
canonical store and the site directory only a cache.

- Same bucket for the new install: point `objectStorage.bucket`/`prefix` at it.
  Nothing to copy.
- New bucket: copy bucket to bucket, keeping keys unchanged, then use the same
  `objectStorage.prefix`:

  ```bash
  aws s3 sync s3://old-bucket s3://new-bucket
  ```

**Old deployment kept files on the host volume only.** Copy the site's file
directories onto the `frappe-sites` claim. The app's backfill job
(`storage.assets.sync_pending`, every five minutes) then uploads them into the
bucket in batches; watch the `Error Log` for `WebODM Storage` entries.

```bash
# Old host
docker run --rm -v g20-daas_frappe_sites:/sites:ro -v "$PWD":/out alpine \
  tar -C /sites/webodm.local -cf /out/site-files.tar public/files private/files

# Cluster
kubectl -n webodm exec -i job/site-shell -- \
  tar -C /workspace/frappe-bench/sites/webodm.local -xf - < site-files.tar
```

If the old deployment also used the `frappe_data` volume (`/data`), copy it
the same way into a pod that mounts `frappe-data` (`frappe-web` does).

To copy files straight into the dev MinIO bucket instead, forward its port and
use any S3 client with the `s3_app_*` credentials from the Secret:

```bash
kubectl -n webodm port-forward svc/minio 9000:9000 &
aws --endpoint-url http://127.0.0.1:9000 s3 sync s3://old-bucket s3://webodm-dev
```

### 6.5 Migrate and start

```bash
kubectl -n webodm delete job site-shell
kubectl -n webodm patch cronjob backup -p '{"spec":{"suspend":false}}'

# Re-runs the bootstrap Job (the site exists, so it runs `migrate`) and
# restores the replica counts; the Frappe pods start once it completes.
helm upgrade --install webodm infra/helm/webodm -n webodm \
  -f infra/helm/webodm/values.prod.yaml -f my-site.yaml \
  --set bootstrap.runId=$(date +%s)
```

Log in with the **old** Administrator password (it came over with the
database), check a few tasks and their files, then update DNS and retire the
old host. Update the `admin_password` Secret key to match, for the record.

## 7. Operations

```bash
# Status
kubectl -n webodm get pods,jobs,cronjobs,pvc

# Logs
kubectl -n webodm logs deploy/frappe-web --tail=100 -f
kubectl -n webodm logs deploy/frappe-worker -c frappe-worker --tail=100

# bench, inside a running pod (it must run from the sites directory)
kubectl -n webodm exec -it deploy/frappe-web -c frappe-web -- bash -c 'cd /workspace/frappe-bench/sites &&
  /workspace/frappe-bench/env/bin/python -m frappe.utils.bench_helper frappe --site <site> <command>'

# Restart everything after changing the Secret
kubectl -n webodm rollout restart deploy

# Back up now, and list backups
kubectl -n webodm create job --from=cronjob/backup backup-manual-$(date +%s)
kubectl -n webodm logs -f -l app.kubernetes.io/component=backup --tail=50
```

**Backups.** The CronJob (default 03:00 daily, `backup.schedule`) runs
`bench backup --with-files` in the Frappe image and writes one timestamped
directory per run to the `backup-storage` claim, pruning those older than
`backup.retentionDays`. Set `backup.s3.bucket` (and `backup_s3_access_key` /
`backup_s3_secret_key` in the Secret, or an IAM role) to copy each run to a
bucket as well. That claim lives in the same cluster as the data it protects,
so the bucket copy is what makes it a backup. With object storage on, task
inputs and outputs are in S3 and are **not** in these archives; protect the
bucket with versioning or replication.

**Restore** a database dump from a backup directory like
[6.2](#62-dump-and-restore-the-database): scale the Frappe Deployments to
zero, load the `*-database.sql.gz` with `gunzip -c | psql -U webodm -d webodm`
into a recreated database, then run the upgrade command with a new
`bootstrap.runId`.

**Scaling.** `frappe.web.replicas` and `frappe.worker.replicas` scale freely.
Keep `frappe-scheduler` at one (the chart enforces it) and `frappe-socketio` at
one unless you have checked that client-IP affinity holds through your edge.

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Frappe pods stay in `Init:0/1` | They are waiting for the bootstrap Job. `kubectl -n webodm get jobs`, then read its logs. It retries on failure (`bootstrap.backoffLimit`); after fixing the cause, re-run with a new `bootstrap.runId` |
| Pod `CreateContainerConfigError`: `secret "webodm-secrets" not found` or `couldn't find key` | Create the Secret (`scripts/k8s-init-secrets.sh`) or add the missing key |
| `ImagePullBackOff` | Private registry: create `ghcr-pull` and reference it in `imagePullSecrets` |
| Pods `Pending` with `unbound immediate PersistentVolumeClaims` | No default StorageClass: set `storageClass` |
| Pods `Pending` with `didn't match pod affinity rules` / volume `Multi-Attach` errors | The node holding the shared ReadWriteOnce claims is full or gone. Free capacity on it, or move to ReadWriteMany ([Shared volumes](#shared-volumes)) |
| `admin.<domain>` returns 403 for everyone | Caddy does not see real client IPs, or your address is not in `site.adminWhitelist`. Check `kubectl -n webodm logs deploy/caddy` and [the edge options](#the-edge-and-the-real-client-ip) |
| TLS errors on every connection right after enabling PROXY protocol on the load balancer | `caddy.proxyProtocol.enabled` is not set, or the load balancer's addresses are not in `caddy.proxyProtocol.allow` |
| `helm upgrade` fails with `field is immutable` on a StatefulSet | A volume size or StorageClass changed. Those cannot be edited in place: expand the claim directly, or recreate the StatefulSet with `kubectl delete sts <name> --cascade=orphan` and upgrade again |
| Bootstrap fails with `password authentication failed for user "frappe_admin"` | The Postgres volume was initialised with a different Secret (for example a reinstall after deleting the Secret but not `data-postgres-0`). Restore the old Secret, or delete the claim to start empty |
| Certificate warnings in dev | Expected: `caddy.tls.mode: internal` |
