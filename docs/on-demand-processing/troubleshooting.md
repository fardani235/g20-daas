# On-Demand Processing — Troubleshooting

Symptom → likely cause → what to do. `bench` below means
`docker compose exec frappe-web /workspace/frappe-bench/env/bin/python -m frappe.utils.bench_helper frappe --site webodm.local`.
Error Log titles are in the [runbook](runbook.md#4-what-to-watch).

## Provisioning

### Task sits in Provisioning, then goes back to Queued with *provisioning timed out*

*Cause:* the instance never answered on `hostname:3000` within
`WEBODM_COMPUTE_PROVISION_TIMEOUT_SECONDS`. In order of likelihood:

1. **Security group** does not allow the app host's egress IP (moved host, new
   NAT, IPv6 vs IPv4). Check: `curl -s https://checkip.amazonaws.com` from the
   host vs. the group's inbound rule (`infra/aws/security-group.md`).
2. **Stock AMI on a slow pull**: Docker install + a 1.5 GB image pull can take
   longer than a short timeout. Raise the timeout to 1200 s or bake the AMI.
3. **Subnet without internet route / no public IP**: the node cannot pull the
   image and the provisioner reports `running, no public address yet` forever.
   Use a public subnet with auto-assign, or keep `PROVISIONER_AWS_ASSOCIATE_PUBLIC_IP=true`.
4. **Wrong port**: `PROVISIONER_NODEODM_PORT` differs from the group rule.

*Check:* `bench` → the instance record's `last_error`; `docker compose logs
provisioner` shows each describe with its probe detail (`nodeodm not answering:
ConnectTimeout` = firewall; `nodeodm answered 401` with a token present = token
mismatch — see below). With `PROVISIONER_AWS_KEY_NAME` set you can SSH in and
read `/var/log/webodm-nodeodm-bootstrap.log`.

*Effect:* the instance is destroyed and the task retries with backoff; after 8
attempts it is Failed. Nothing is stuck.

### Task Failed with *provisioning failed: provisioner error: … EC2 UnauthorizedOperation / InvalidAMIID / VPCIdNotSpecified …*

*Cause:* the provisioning identity or the launch parameters. `UnauthorizedOperation`
usually means the policy's `Condition` on `aws:RequestTag` does not match:
`PROVISIONER_DEPLOYMENT` (defaults to `SITE_NAME`) must equal the `DEPLOYMENT`
you substituted into `provisioner-policy.json`. `InvalidAMIID.NotFound` =
AMI from another region. `InsufficientInstanceCapacity` / `VcpuLimitExceeded`
= AWS quota for the instance type; request a quota increase or change the type.

*Do:* `docker compose logs provisioner` shows the EC2 error code (never the
credentials). Fix `.env`/policy, `docker compose up -d provisioner`, press
Start again.

### Tasks never enter Provisioning; they run on the static node

*Cause:* `PROVISIONER_URL` empty, provisioner down, or `PROVISIONER_PROVIDER=none`.
Error Log has `WebODM Compute: … falling back to static node`.

*Check:* `docker compose exec frappe-web curl -s http://provisioner:5002/provider`
→ `"enabled": true`. If `401`, the API token differs between the app and the
provisioner (both read `secrets/provisioner_api_token.txt`; recreate both).

### Task stays Queued with *organization compute cap reached (2/2)*

Not an error. Raise `WEBODM_COMPUTE_MAX_INSTANCES_PER_ORG` /
`WEBODM_COMPUTE_MAX_INSTANCES` or wait. If the cap is "reached" but no
instance is live, records are stuck in Requested/Provisioning/Ready — run
`bench execute webodm_core.webodm_core.processing.compute.reap` and look at
their `last_error`.

### Provisioner container exits at start: *configuration error: PROVISIONER_AWS_… is required*

By design: every AWS value is explicit. Set it in `.env`
([configuration reference](configuration.md)).

## Node problems while running

### Task Running, progress frozen, then Failed with *node unreachable for 15 consecutive polls*

*Cause:* the node died (spot-like interruption, OOM kill of the ODM container,
self-destruct at lifetime budget + 10 min, someone terminated it).

*Do:* the sweep has already destroyed/closed the instance. Check the record's
`last_error` and `terminated_at`. If ODM ran out of memory, use a larger class
(`PROVISIONER_AWS_INSTANCE_TYPES`) or lower quality options; if the lifetime
budget hit (`compute instance exceeded its … lifetime budget`), raise
`WEBODM_COMPUTE_MAX_LIFETIME_SECONDS`. Press Start to retry.

### Console empty during Running on a cloud node

*Cause:* the console proxies to the task's node; `401` means the recorded
token does not match the node (the token is baked in at boot from the create
call). Only happens if the record was edited. Not fatal — processing continues.

### Instance stuck in Terminating; Error Log `WebODM Compute ALERT: destroy attempt N failed`

*Cause:* the provisioner cannot reach AWS (credentials rotated, network) or
`ec2:TerminateInstances` is denied (tags on the instance do not match the
policy condition — did someone change `PROVISIONER_DEPLOYMENT`?).

*Do:* fix the cause; the sweep retries every minute. Meanwhile the node's
self-destruct still fires at budget + 10 min. Worst case:
`aws ec2 terminate-instances --instance-ids <handle without aws:>`; the sweep
closes the record as Terminated on the next pass.

### An instance exists in AWS that Desk does not know about

*Cause:* the create call succeeded but the app died before recording the
handle, or a record was deleted by hand.

*Do:* nothing, if it carries `webodm:managed=true` + this deployment's tag: the
orphan rule destroys it after `WEBODM_COMPUTE_ORPHAN_GRACE_SECONDS`. If it
lacks the tags it was not launched by this deployment — check before killing.

## Object storage

### Uploads succeed but `storage_key` stays empty; Error Log `WebODM Storage: input sync failed … access denied`

*Cause:* the **app** identity's key/policy. `AccessDenied` = policy resource
does not match the key (`PREFIX` substitution, bucket name);
`InvalidAccessKeyId` / `SignatureDoesNotMatch` = wrong key in
`secrets/s3_app_*`; `no storage credentials available` = empty secret files and
no instance role.

*Do:* fix, `docker compose up -d frappe-web frappe-worker frappe-scheduler`.
The 5-minute backfill picks up everything that was skipped; nothing is lost
(host copies are protected from eviction until synced).

### Task Running forever at 100 % after the node completed; Error Log `WebODM Storage` / `WebODM Geospatial` every minute

*Cause:* the relay cannot finish: storage unreachable (`storage unreachable`),
or the COG conversion fails. Each poll retries from where it left off
(`raw/all.zip` is kept), and `poll_failures` counts up.

*Do:* geospatial conversion failing with `object storage: bucket is not in the
allowed list` → `S3_BUCKETS` on the geospatial service does not include the
bucket (compose passes `WEBODM_S3_BUCKET`; a hand-edited override may not).
`access denied` from geospatial → the **geospatial** identity cannot read
`raw/` or write `assets/*.tif` (check `storage-geospatial-policy.json`). After
15 failing polls the task **completes with the raw (non-COG) raster** rather
than failing — tiles work but slower; re-process later to get a COG.

### `cogify failed: cannot open raster` for an S3 source

*Cause:* GDAL cannot reach the endpoint: with MinIO, `S3_ENDPOINT_URL` needs a
scheme (`http://minio:9000`) and `S3_FORCE_PATH_STYLE=true`; with AWS,
`AWS_REGION` must be the bucket's region (a wrong region gives a 301 GDAL
reports as "cannot open").

*Check:* `docker compose exec geospatial python -c "from app.utils import objectstore, raster; print(raster.read_metadata('s3://<bucket>/<key>')['width'])"`.

### Map layer blank; tile requests return transparent PNGs; Error Log `WebODM Tiles: tile fetch failed`

*Cause:* the geospatial service errored on the path it was given. Cold-cache
case: the path is `s3://…` and the geospatial service has no object storage
config (`S3_BUCKETS` empty) → `400 bucket is not in the allowed list`.

*Do:* configure `S3_BUCKETS` etc. on `geospatial` (compose does this from
`WEBODM_S3_*`). The tile proxy also enqueued a cache fill, so the layer
recovers by itself once the blob is back on disk.

### Model viewer / download returns 404 for a task that has a model

*Cause:* the blob was evicted and the `before_request` cache fill failed —
look for `WebODM Storage: cache fill for /private/files/… failed`. Usually
credentials, or the object is gone (deleted by hand / lifecycle rule).

*Do:* fix storage access and reload. If the object is truly gone the task
must be re-processed: press Start.

### *object key is outside the organization's namespace* (403 / OrgBoundaryError)

*Cause:* a row's `storage_key` does not start with `orgs/<this org's slug>/`.
Only possible through direct database edits or an organization slug renamed
**and** a key rewritten by hand. The app refuses the read; it does not
silently serve another org's object.

*Do:* inspect the row (`WebODM Task Asset`, `WebODM Task Image`,
`WebODM Plugin Run.storage_key`) and restore the correct key, or clear it and
let the backfill re-upload from the host copy if it still exists.

### Everything is slow the first time a dataset is opened

Expected with a cold cache: tiles render from S3 via range reads (a few tens of
milliseconds extra per tile on AWS-to-AWS, more across the internet) while the
blob is fetched in the background; the 3D model and downloads wait for the
full fetch. If it is *always* slow, the cache is thrashing: raise
`WEBODM_CACHE_MAX_BYTES` or lower `WEBODM_CACHE_IDLE_SECONDS`'s inverse (i.e.
keep more) and check disk. Scheduler log line `cache eviction: {...}` shows
`evicted` per hour.

### Re-processing a task fails with *task has no readable images*

*Cause:* images are neither on disk nor in S3: the task predates object
storage and its host copies were removed **without** the backfill having
synced them (`storage_key` empty). Error Log has *missing on disk and not in
object storage* per image.

*Do:* re-upload the imagery as a new task. Prevent it: never delete from
`private/files` by hand; let `evict_job` do it (it only evicts synced blobs).

## Frappe-side plumbing

### Scheduler shows `compute reaper failed` / `cache eviction failed`

Both jobs never raise — the log entry contains the exception. Typical: DB
connectivity blip. They run again next minute / hour.

### After enabling storage, `bench migrate` complains about `WebODM Task Asset` / `WebODM Compute Instance`

The DocTypes ship with the app; a migrate (or the init container) creates
them. `docker compose run --rm frappe-init` on an existing volume is a safe
re-migrate.
