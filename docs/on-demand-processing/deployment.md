# On-Demand Processing — Deployment and Setup

How to stand up object storage and on-demand compute for an existing WebODM
Compose deployment. Everything is opt-in: with none of it configured the stack
behaves exactly as before (host disk as the store, the `nodeodm` service as
the engine).

Related: [configuration reference](configuration.md) ·
[operations runbook](runbook.md) · [troubleshooting](troubleshooting.md) ·
[architecture](architecture.md) · [AWS artefacts](../../infra/aws/README.md).

## 0. What you are deploying

```
                     ┌──────────────┐  internal HTTP  ┌──────────────┐  EC2 API   ┌─────────┐
  frappe-worker ────►│ provisioner  │────────────────►│   AWS        │──────────►│ node    │
        │            └──────────────┘                 └──────────────┘           │ NodeODM │
        │ multipart upload / poll / download all.zip  (public IP:3000, token)     └────┬────┘
        ▼                                                                             │
  ┌───────────┐   inputs, all.zip, assets        ┌───────────────┐   read raw, write COG
  │  S3       │◄─────────────────────────────────│  geospatial   │◄────────────────────┘
  │  bucket   │──── range reads for tiles ──────►│  (/vsis3/)    │
  └───────────┘                                  └───────────────┘
        ▲  serving cache fill (private/files)
  frappe-web
```

Three new pieces:

| Piece | Where | Identity it uses |
|---|---|---|
| **Object storage** (S3 or S3-compatible) | your account | *app writer* (Frappe) and *raster converter* (geospatial) — two separate keys |
| **`provisioner`** service | `docker-compose.yml`, built from `services/provisioner` | *provisioning* key (EC2 only), plus a shared API token the app presents |
| **On-demand nodes** | EC2 instances created and destroyed per task | none (the node has no AWS identity unless you give it one) |

## 1. Local / development path (MinIO + fixed provider)

Use this to see the whole lifecycle before touching a cloud account. It needs
nothing but Docker.

```bash
scripts/init-secrets.sh                       # creates the new (empty) secret files too
# MinIO root credentials double as the app identity in dev:
printf 'minioadmin' > secrets/s3_app_access_key_id.txt
openssl rand -hex 24 | tr -d '\n' > secrets/s3_app_secret_access_key.txt
chmod 600 secrets/*.txt

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

`docker-compose.dev.yml`:

* starts **MinIO** (`minio`) and creates the bucket `webodm-dev` (`minio-init`);
  the console is on <http://127.0.0.1:9001>;
* points Frappe (`WEBODM_S3_*`) and the geospatial service (`S3_*`) at it;
* runs the provisioner with the **`fixed`** provider, which "provisions" the
  stack's own `nodeodm` container. Tasks go Queued → **Provisioning** → Running
  exactly as they would on AWS; the instance record has a `fixed:` handle and a
  zero cost estimate.

Then upload a dataset and press Start. Watch the lifecycle:

```bash
docker compose logs -f frappe-worker provisioner
docker compose exec frappe-web /workspace/frappe-bench/env/bin/python -m frappe.utils.bench_helper frappe --site webodm.local \
  execute frappe.client.get_list --kwargs '{"doctype":"WebODM Compute Instance","fields":["name","task","status","handle","provider"]}'
```

and the objects: MinIO console → bucket `webodm-dev` → `orgs/<org-slug>/tasks/<task>/…`.

To exercise the **cold cache**, delete a cached blob and reload the map:

```bash
docker compose exec frappe-web sh -c 'rm /workspace/frappe-bench/sites/webodm.local/private/files/<task>_orthophoto.tif'
```

Tiles keep rendering (from MinIO, through the geospatial service) and the blob
reappears on the next request. `docker-compose.dev.yml` is an override only;
nothing in it belongs in production.

## 2. Object storage on AWS S3

### 2.1 Bucket

```bash
export AWS_REGION=eu-central-1 BUCKET=webodm-prod-<unique>
aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" \
  --create-bucket-configuration LocationConstraint="$AWS_REGION"
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
sed "s/BUCKET/$BUCKET/g" infra/aws/iam/bucket-policy.json > /tmp/bucket-policy.json
aws s3api put-bucket-policy --bucket "$BUCKET" --policy file:///tmp/bucket-policy.json
# optional but recommended: abort stale multipart uploads (a crashed relay leaves parts behind)
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration \
  '{"Rules":[{"ID":"abort-mpu","Status":"Enabled","Filter":{"Prefix":""},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":2}}]}'
```

Private bucket, no public access, encryption at rest, TLS enforced by policy.
Versioning is optional (the app never overwrites an object of a task except on
re-processing, which is a deliberate replace).

### 2.2 Layout

Everything is namespaced by organization. There is no key outside an
organization's prefix, and the app refuses to read or write one.

```
<prefix>orgs/<org-slug>/tasks/<task>/inputs/<image>             uploaded imagery (canonical)
<prefix>orgs/<org-slug>/tasks/<task>/raw/all.zip                node output archive (transient)
<prefix>orgs/<org-slug>/tasks/<task>/raw/<asset>.tif            pre-COG raster (transient)
<prefix>orgs/<org-slug>/tasks/<task>/assets/orthophoto.tif      canonical outputs: COGs,
<prefix>orgs/<org-slug>/tasks/<task>/assets/dsm.tif             LAZ and GLB as-is
<prefix>orgs/<org-slug>/tasks/<task>/assets/dtm.tif
<prefix>orgs/<org-slug>/tasks/<task>/assets/georeferenced_model.laz
<prefix>orgs/<org-slug>/tasks/<task>/assets/model.glb   (or model.zip for the OBJ fallback)
<prefix>orgs/<org-slug>/plugin-runs/<run>/<file>                analysis plugin outputs
```

`<prefix>` is `WEBODM_S3_PREFIX` (empty, or e.g. `prod/`). `<org-slug>` is the
organization's slug; the key is also stored on every row, so a slug change only
affects new objects. Deleting a task deletes its prefix.

### 2.3 Identities

Two IAM users (or roles), least privilege, same bucket:

| Identity | Policy | Used by | Can |
|---|---|---|---|
| `webodm-app` | `infra/aws/iam/storage-app-policy.json` | frappe-web / worker / scheduler | get/put/delete under `orgs/*` |
| `webodm-geospatial` | `infra/aws/iam/storage-geospatial-policy.json` | geospatial | get `raw/*`, `assets/*`, `plugin-runs/*`; put `assets/*.tif` only; no delete; no `inputs/` |

```bash
for who in app geospatial; do
  sed -e "s/BUCKET/$BUCKET/g" -e "s|PREFIX||g" infra/aws/iam/storage-$who-policy.json > /tmp/$who.json
  aws iam create-user --user-name webodm-$who
  aws iam put-user-policy --user-name webodm-$who --policy-name webodm-storage-$who --policy-document file:///tmp/$who.json
  aws iam create-access-key --user-name webodm-$who --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text
done
```

Write each key pair into the secret files (no trailing newline is required,
but is tolerated):

```
secrets/s3_app_access_key_id.txt              secrets/s3_app_secret_access_key.txt
secrets/s3_geospatial_access_key_id.txt       secrets/s3_geospatial_secret_access_key.txt
```

If the app host is itself an EC2 instance with an instance role, leave the
files **empty**: both services fall back to the default credential chain. (You
then lose the app/geospatial privilege split unless you use separate roles, so
prefer keys off-AWS and roles-per-container on AWS.)

A third, read-only *signer* policy (`storage-signer-policy.json`) exists for
presigned output URLs. The shipped code serves everything through the app's
own session-authenticated routes and does not need it; it is there so the
privilege model is complete if you enable presigned downloads later
(`storage_presign_ttl` caps their lifetime, 15 minutes by default).

### 2.4 Configure the stack

In `.env`:

```
WEBODM_S3_BUCKET=webodm-prod-<unique>
WEBODM_S3_REGION=eu-central-1
WEBODM_S3_PREFIX=
WEBODM_S3_ENDPOINT_URL=
WEBODM_S3_FORCE_PATH_STYLE=0
WEBODM_CACHE_MAX_BYTES=53687091200      # 50 GiB serving cache
WEBODM_CACHE_IDLE_SECONDS=604800        # evict after 7 idle days
```

`docker compose up -d` restarts the Frappe services and geospatial with the
new settings (`configure_site.py` writes the non-secret values into
`common_site_config.json`; the keys stay in the container environment only).

Check:

```bash
docker compose exec geospatial wget -qO- http://localhost:5000/health   # "object_storage": ["webodm-prod-…"]
docker compose exec frappe-web /workspace/frappe-bench/env/bin/python -m frappe.utils.bench_helper frappe --site webodm.local \
  execute webodm_core.storage.configured                                 # True
```

From this point on: uploads are copied to S3 in the background, new outputs
land in S3 as COGs, and the every-5-minutes backfill (`storage.assets.sync_pending`)
copies **existing** host-only tasks up gradually (20 per run). Nothing needs
migrating by hand; watch `Error Log` for `WebODM Storage` entries.

### 2.5 MinIO / other S3-compatible services in production

Same as above with:

```
WEBODM_S3_ENDPOINT_URL=https://minio.internal:9000
WEBODM_S3_FORCE_PATH_STYLE=1
WEBODM_S3_REGION=us-east-1        # any value MinIO accepts
```

Create two MinIO users with the equivalent policies (MinIO policies use the
same JSON grammar; replace the ARNs' bucket names and drop the `Condition`
blocks it does not support).

## 3. On-demand compute on AWS

### 3.1 Network

* A **VPC subnet with a route to the internet** and public IPs
  (`PROVISIONER_AWS_SUBNET_ID`). Nodes need to pull the NodeODM image (unless
  prebaked) and must be reachable from the app host.
* A **security group** allowing TCP `PROVISIONER_NODEODM_PORT` (3000) from the
  app host's egress IP only — `infra/aws/security-group.md` has the commands.
  This is *the* firewall rule of the design: public endpoint, one source,
  plus a per-run bearer token.

### 3.2 Provisioning identity

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
sed -e "s/REGION/$AWS_REGION/g" -e "s/ACCOUNT_ID/$ACCOUNT_ID/g" -e "s/DEPLOYMENT/webodm.local/g" \
    -e "s/AMI_ID/$AMI_ID/g" -e "s/SUBNET_ID/$SUBNET_ID/g" -e "s/SECURITY_GROUP_ID/$SG/g" \
    infra/aws/iam/provisioner-policy.json > /tmp/provisioner.json
aws iam create-user --user-name webodm-provisioner
aws iam put-user-policy --user-name webodm-provisioner --policy-name webodm-provisioner --policy-document file:///tmp/provisioner.json
aws iam create-access-key --user-name webodm-provisioner --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text
```

`DEPLOYMENT` must equal `PROVISIONER_DEPLOYMENT` (compose sets it to
`SITE_NAME`). The policy lets this identity launch instances **only** when it
tags them `webodm:managed=true` + this deployment, and terminate **only**
instances carrying those tags. It has no S3 access. Put the key pair in
`secrets/aws_provisioner_access_key_id.txt` /
`secrets/aws_provisioner_secret_access_key.txt`.

### 3.3 Machine image

Two options; the bootstrap (`services/provisioner/app/bootstrap/nodeodm-userdata.sh`)
handles both, so you are never blocked on image work:

| | `PROVISIONER_AWS_AMI_ID` | `PROVISIONER_AWS_AMI_PREBAKED` | Boot to ready |
|---|---|---|---|
| **Prebaked** (recommended) | output of `infra/aws/bake-nodeodm-ami.sh` | `true` | ~1 min |
| **Stock** Ubuntu 24.04 | Canonical's current AMI (see below) | `false` | 3–5 min (installs Docker, pulls ~1.5 GB) |

```bash
# stock AMI id for the region
aws ssm get-parameter --region $AWS_REGION --query Parameter.Value --output text \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
# or bake (needs the subnet + a group with outbound internet; takes ~10 min)
AWS_REGION=$AWS_REGION SUBNET_ID=$SUBNET_ID SECURITY_GROUP_ID=$SG infra/aws/bake-nodeodm-ami.sh
```

Re-run the bake to pick up a newer NodeODM; the AMI is tagged with the image
it contains. Pin `PROVISIONER_NODEODM_IMAGE` to a digest in production so the
stock path and the prebaked path run the same engine.

### 3.4 Configure the stack

`.env`:

```
PROVISIONER_URL=http://provisioner:5002
PROVISIONER_PROVIDER=aws
PROVISIONER_AWS_REGION=eu-central-1
PROVISIONER_AWS_AMI_ID=ami-0abc…            # from 3.3
PROVISIONER_AWS_AMI_PREBAKED=true
PROVISIONER_AWS_INSTANCE_TYPES={"cpu": "c6i.2xlarge", "cpu-large": "c6i.8xlarge"}
PROVISIONER_AWS_HOURLY_COSTS={"cpu": 0.34, "cpu-large": 1.36}
PROVISIONER_AWS_DEFAULT_CLASS=cpu
PROVISIONER_AWS_SUBNET_ID=subnet-0abc…
PROVISIONER_AWS_SECURITY_GROUP_IDS=sg-0abc…
PROVISIONER_AWS_ROOT_VOLUME_GB=200
PROVISIONER_NODEODM_IMAGE=opendronemap/nodeodm:latest@sha256:…
WEBODM_COMPUTE_MAX_INSTANCES=5
WEBODM_COMPUTE_MAX_INSTANCES_PER_ORG=2
WEBODM_COMPUTE_PROVISION_TIMEOUT_SECONDS=900
WEBODM_COMPUTE_MAX_LIFETIME_SECONDS=43200
```

`scripts/init-secrets.sh` already generated `secrets/provisioner_api_token.txt`
(the shared secret the app presents to the provisioner). Then:

```bash
docker compose build provisioner       # until CI publishes the image
docker compose up -d
docker compose exec provisioner python3 -c "import urllib.request;print(urllib.request.urlopen('http://localhost:5002/health').read())"
# {"status":"ok","service":"webodm-provisioner","provider":"aws"}
```

A misconfigured provisioner **refuses to start** (missing AMI, subnet, types…)
rather than accepting tasks it cannot place; `docker compose logs provisioner`
names the variable.

Sizing the instance types: ODM's memory use scales with image count and
`feature-quality`; `c6i.2xlarge` (8 vCPU/16 GB) handles a few hundred 20 MP
images at default quality, `c6i.8xlarge` (32 vCPU/64 GB) is the safe choice for
ultra quality or 500+ images. `PROVISIONER_AWS_ROOT_VOLUME_GB` must hold the
imagery plus ODM's intermediates — roughly 10× the input size.

### 3.5 First task

Upload, Start, and watch the instance appear in **Desk → WebODM Compute
Instance** (Provisioning → Ready → Terminated with a cost estimate) and in the
AWS console (tagged `webodm:managed`, `webodm:task`, `webodm:org`). The task
goes Queued → Provisioning → Running → Completed; outputs land under
`assets/` as COGs; the instance is gone within a minute of completion.

## 4. Where the bucket sits (egress cost)

Data moves: **inputs** host → node (public internet or AWS, depending where the
host is), **outputs** node → host → S3 (the relay streams through the worker),
**COG conversion** S3 → geospatial → S3, **serving** S3 → host.

* App host **on AWS**, nodes on AWS, bucket in the **same region**: all of it is
  intra-region, S3↔EC2 transfer is free; you pay S3 requests and storage only.
* App host **off AWS** (the Byteplus VM in the production guide): every S3
  read from the host is internet egress from S3 (~$0.09/GB, first 100 GB/month
  free). Put the bucket in the region closest to the host for latency; egress
  price is region-dependent but similar. Nodes are on AWS, so node → host is
  AWS egress too, and host → node is your host's egress.
* **GPU nodes outside AWS** (vast.ai, next phase): they never touch S3
  directly — the worker relays — so the bucket choice is unaffected by the
  compute provider; what matters is bucket ↔ app host. Keep the bucket close
  to the app host.

Rule of thumb: the bucket lives where the *app host* is; if the host is on
AWS, in its region. Record the decision in your deployment notes; the
[runbook](runbook.md) lists what to watch on the bill.

## 5. Falling back / turning it off

* **No cloud, S3 only:** leave `PROVISIONER_URL` empty. Tasks run on `nodeodm`;
  storage is still canonical S3.
* **No on-demand for a while:** `PROVISIONER_URL=` and `docker compose up -d`.
  Running instances are still reaped (the sweep keeps its records and the
  provisioner service keeps running) — see the runbook.
* **Back to host-only storage:** empty `WEBODM_S3_BUCKET`. Existing rows keep
  their `storage_key`; blobs already evicted from the cache are **not**
  refetched, so only do this while the cache is complete (i.e. never in
  practice; keep S3).
