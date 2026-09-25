# On-Demand Processing — Architecture

The technical description of the pipeline as shipped: where compute lives,
where the bytes live, the seams, the data model and the state machine. SPEC.md
§2 and §5 summarise this; TRD.md §5.3 has the storage table.

## 1. Shape

```
  Upload ──► host landing (EXIF) ──► S3 canonical (inputs)          [assets.sync_inputs, background]
                                       │
                                       └── local serving cache (private/files)
  Start ──► provision node ──► stream input (cache, else S3) ──► NodeODM
  Node done ──► relay assets node ──► S3 raw ──► COG-ify S3 ──► S3 ──► S3 canonical (assets/)
                                                                   └── write-through cache
  Tile / viewer / plugin ──► cache if present, else S3
  Reaper ──► evict idle cache; S3 stays authoritative
  Sweep  ──► destroy instances whose task is done / gone / over budget / never came up; orphans
```

Three services, one new:

| Service | Owns | Talks to |
|---|---|---|
| **Frappe** (web / worker / scheduler) | task pipeline, lifecycle records, cache, org scoping | provisioner (HTTP), geospatial (HTTP), S3 (boto3, writer identity), NodeODM (HTTP) |
| **provisioner** (`services/provisioner`) | providers, provisioning credentials, machine image, bootstrap, readiness probing | cloud API (boto3), nodes (`GET /info`) |
| **geospatial** (`services/geospatial`) | tiles, metadata, COG conversion — local or `s3://` | S3 (GDAL `/vsis3/` + boto3, converter identity) |

Nothing above the provider interface knows which cloud produced a node; nothing
above `webodm_core.storage` knows which vendor provides the bucket.

## 2. Compute

### 2.1 Provider boundary

`services/provisioner/app/providers/base.py`:

```python
class Provider(ABC):
    name: str                                       # handle prefix, e.g. "aws"
    classes: dict[str, ClassSpec]                   # "cpu" -> EC2 type / cost
    def create(spec: InstanceSpec) -> InstanceState   # token, port, lifetime, labels in
    def describe(handle) -> InstanceState           # pending | running | terminated | failed, public_ip
    def destroy(handle) -> None                     # idempotent
    def list_managed() -> list[InstanceState]       # by tag: this deployment's instances
```

The core (`app/core.py`) adds what is the same for every provider: handle
prefixing (`aws:i-0abc…`), readiness (`running` + NodeODM answering `/info` on
the public endpoint — with the per-run token when supplied on
`X-Node-Token`), and the HTTP API. Implementations: `aws` (EC2, CPU classes)
and `fixed` (dev: hands back one configured endpoint). vast.ai is a third
module plus a registry entry; the app, its data model and storage are
untouched.

### 2.2 Provisioner API (internal network only, optional bearer token)

| | Path | Body / result |
|---|---|---|
| `GET` | `/provider` | `{enabled, provider, classes: {name: {flavor, hourly_cost}}, default_class, nodeodm_port, max_lifetime_seconds}` |
| `POST` | `/instances` | in: `{instance_class?, token, labels, max_lifetime_seconds?}`; out: `{handle, provider, instance_class, status: "pending", hourly_cost, …}` |
| `GET` | `/instances/{handle}` | `{status: pending\|ready\|terminated\|failed, hostname, port, launched_at, detail}` |
| `DELETE` | `/instances/{handle}` | `{status: "terminated"}`, idempotent |
| `GET` | `/instances` | every managed instance (for the orphan sweep) |

The service is stateless: every answer comes from the provider API plus a
probe. The app generates the node token, passes it in `create`, stores it
(Password field) and uses it for the probe header and for talking to the node.

### 2.3 App side (`webodm_core.webodm_core.processing.compute`)

* `request_for_task`: caps → `GET /provider` → class from processing options
  (`instance_class_for`) → insert `WebODM Compute Instance` (Requested) and
  commit → `POST /instances` → record handle (Provisioning). The record
  exists before the create call so a crash between the two cannot lose a
  machine (the sweep's timeout rule closes it, the orphan rule catches the
  provider side).
* `check_provisioning` (every minute per Provisioning task): describe → Ready
  (+ dispatch) | wait | fail-and-requeue on failed/terminated/timeout.
* `release_for_task` on every terminal transition; `destroy_instance` is
  best-effort and leaves `Terminating` for the sweep on failure.
* `reap`: the safety net (rules in the [runbook](runbook.md#3-the-sweep-computereap)).
* Provisioner credentials: none in the app. The only secret the app holds for
  compute is the provisioner API token (env, not config).

### 2.4 Node

EC2 instance from `PROVISIONER_AWS_AMI_ID`, launched with user-data rendered
from `app/bootstrap/nodeodm-userdata.sh`: installs Docker if absent (stock
AMI), pulls the image if absent (prebaked AMI has it), runs
`nodeodm --token <per-run>` on `PROVISIONER_NODEODM_PORT`, and schedules
`shutdown -h` at lifetime + 10 min. Launched with
`InstanceInitiatedShutdownBehavior=terminate`, IMDSv2 required, gp3 root
volume, tags `webodm:managed=true`, `webodm:deployment`, `webodm:task`,
`webodm:org`, `webodm:class`. Reachable on the public IP from the app host's
egress address only (security group) and only with the token. Nothing else
runs on it; readiness is observed, not reported.

## 3. Storage

### 3.1 Canonical store and cache

`webodm_core.storage` is the only module that speaks S3 (boto3). With
`storage_bucket` empty it is inert and the pipeline is the host-disk pipeline.
With a bucket:

* **Keys** are org-namespaced (`<prefix>orgs/<slug>/tasks/<task>/…`,
  `…/plugin-runs/<run>/…`); `assert_org_key` is applied on every read/write
  that has an org context and the tile proxy / staging paths only resolve
  through org-checked helpers. There is no code path that constructs a key
  from a user-supplied string.
* **The serving cache is `private/files`.** File documents and
  `/private/files/<name>` URLs are unchanged; the blob may be absent.
  `cache.ensure_local` refills it (atomic `.part` + rename);
  `cache.resolve_source` returns `("local", path)` or `("s3", uri)` for
  consumers that can read S3 themselves (the geospatial service) and kicks
  off a background fill. `serving.materialize_private_file` is a
  `before_request` hook that refills before Frappe's private-file handler
  serves the 3D model or a download.
* **Eviction** (`cache.evict`, hourly): idle rule, then LRU to the size
  budget; only blobs with a `storage_key`; never for tasks in
  Queued/Provisioning/Running or Running plugin runs. Access is tracked by
  bumping mtime on every read through the app.
* **Backfill** (`assets.sync_pending`, every 5 min): host-only inputs,
  outputs and plugin outputs are copied up in bounded batches, so a
  pre-existing site converges to "everything in S3" without a migration step.

### 3.2 Input flow

Upload → `private/files` (EXIF read from the path, unchanged) → task saved →
`assets.enqueue_input_sync` → each image PUT to `inputs/<file name>`,
`storage_key` set on the `WebODM Task Image` row. Dispatch calls
`sync_inputs` again (idempotent) and streams each image to NodeODM from the
cache when present, else directly from S3 (`input_sources` returns a path or
a stream opener; `NodeODMClient.create_task` accepts both). NodeODM only
takes multipart uploads, so the host relays either way — the host copy is
just disposable now.

### 3.3 Output flow (`task_runner._relay_assets_to_storage`)

1. `all.zip` streams node → S3 `raw/all.zip` (multipart from the response
   body; nothing on disk).
2. The archive is opened *in S3* (`storage/s3file.py`: a seekable
   `io.RawIOBase` over range reads, wrapped in an 8 MB `BufferedReader`) and
   each known member streams to its own object: rasters to `raw/<name>`,
   point cloud and model straight to `assets/<name>`.
3. Each raster: `POST /export/cogify {path: s3://…/raw/x.tif, output_path:
   s3://…/assets/x.tif}` — the geospatial service reads through `/vsis3/`,
   builds the COG in container scratch, uploads it, and returns georef +
   header metadata **read from the S3 object**. Raw copy deleted.
4. Every asset is written through to the cache (`save_private_file_from_stream`
   from the S3 body → File doc → `task.<kind>`) and recorded in `task.assets`
   (`WebODM Task Asset`: kind, key, size, etag, `is_cog`).
5. `Completed` only after every output is in S3; then the instance is released.

Every step is idempotent (exists-checks on keys and rows), so a retry resumes.
Because those same checks would mistake a *previous* run's rows and objects
for already-collected output, a restart (`api.task.process_task` on a
Completed / Failed / Cancelled task) first calls `assets.reset_outputs`: it
clears the output fields, extents and CRS, deletes the asset and metadata
rows and the cached `File`s, and deletes the `raw/` and `assets/` objects —
never `inputs/`. A new run therefore always starts from a clean slate.
Transient problems (node, storage, conversion) raise `AssetRelayRetry`, a
`NodeODMTransportError` subclass — the poller's existing tolerance keeps the
task Running and retries next minute. When the poll budget is about to run
out, a raster that still will not convert is stored raw (server-side copy) so
the task completes with a non-COG rather than losing the output. The OBJ →
`model.zip` fallback is built in container scratch (a zip needs a seekable
writer), not on the sites volume.

### 3.4 Serving

* **Tiles / info / volume**: `api.tiles` → `assets.raster_source` → local
  path or `s3://` URI → geospatial. Over S3 a tile is a few range reads
  against the COG (`GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`, merged ranges,
  VSI cache, 32 KB header ingest); that is why conversion to COG is not
  optional.
* **3D viewer / downloads**: `/private/files/<name>` with the
  `before_request` fill; cold = slower, never wrong.
* **Plugins**: `runner.execute_run` stages inputs via `ensure_asset_local`
  (system plugins read the shared volume; user plugins are copied into the
  sandbox, which has no network egress), outputs are saved locally and
  copied to `plugin-runs/<run>/` (`storage_key` on the run).
* **Metadata**: from the cogify response (S3 object) at completion; on
  refresh, read locally or from `s3://` via `/raster/metadata`.

### 3.5 Geospatial service

`app/utils/objectstore.py`: `S3_BUCKETS` allow-list (any other bucket → 400),
`s3://` ↔ `/vsis3/` mapping, `rasterio.Env(session=AWSSession(...))` around
every open, boto3 for `head`/`upload`. Routers accept `s3://` wherever they
accepted absolute paths (`/tiles/*`, `/raster/metadata`, `/volume`,
`/export/cogify` with `output_path`). Analysis ops stay local: Frappe stages
their inputs from the cache.

## 4. Data model

| DocType | New / changed | Purpose |
|---|---|---|
| `WebODM Task` | `status` + **Provisioning**; `compute_instance` (Link); `assets` (Table) | lifecycle link to the ephemeral node; per-output storage bookkeeping |
| `WebODM Task Asset` (child) | new | `kind`, `filename`, `file_url` (cache), `storage_key`, `file_size`, `etag`, `content_type`, `is_cog`, `synced_at` |
| `WebODM Task Image` (child) | `storage_key` | canonical input key |
| `WebODM Plugin Run` | `storage_key` | canonical output key |
| `WebODM Compute Instance` | new, org-scoped | `task`, `organization`, `status` (Requested / Provisioning / Ready / Terminating / Terminated / Failed), `provider`, `handle`, `instance_class`, `hostname`, `port`, `token` (Password), `requested_at`, `ready_at`, `terminated_at`, `max_lifetime_seconds`, `expires_at`, `destroy_attempts`, `last_error`, `estimated_hourly_cost`, `estimated_cost` |
| `WebODM Processing Node` | unchanged | static registry of known endpoints (the fallback) |

`WebODM Compute Instance` is in both permission hook maps (org-scoped reads
for members, everything for platform admins) and in `ignore_links_on_delete`
so a task can be deleted while its history stays; the sweep treats a missing
task as "destroy".

## 5. Task state machine

```
              Start / restart
 Pending ────────────────────► Queued ◄──────────────────────────────────────┐
                                 │                                            │ requeue with backoff
        no provisioner / down    │  provisioner enabled, caps ok              │ (timeout, provider failure)
        ┌────────────────────────┼──────────────────────┐                     │
        ▼                        ▼                      ▼                     │
   static node dispatch     capacity wait          Provisioning ─────────────►┘
        │                   (60 s, no attempt)          │ node ready
        ▼                                               ▼
     Running ◄──────────────────────────────────────────┘
        │ poll
        ├── COMPLETED ── collect outputs (retry on transient) ──► Completed ──► release node
        ├── FAILED on node / poll budget / lifetime budget ──────► Failed    ──► release node
        └── cancel (any of Pending/Queued/Provisioning/Running) ─► Cancelled ──► release node
```

Every terminal state releases the node (best-effort, retried by the sweep).
`Provisioning` is the only new state; it is skipped entirely when no
provisioner is configured, which is what keeps the static path identical to
the previous behaviour.

## 6. Security model

| Identity | Holder | Privilege |
|---|---|---|
| Provisioning (EC2) | provisioner | run/describe/terminate tagged instances only; no S3 |
| Storage writer | Frappe | get/put/delete under `orgs/*` |
| Raster converter | geospatial | get `raw/`, `assets/`, `plugin-runs/`; put `assets/*.tif` |
| Signer (optional) | — | get `assets/*` for presigned URLs; unused by shipped code |
| Node token | app ↔ node | per run, baked at boot, Password field |
| Provisioner API token | app ↔ provisioner | env-only shared secret |

Credentials live in Docker secrets → container env. `configure_site.py`
writes only non-secret values to site config; botocore exceptions are
translated before they can be logged (`StorageError` messages carry an error
code, never a request or key). The provisioner is on the `backend` network
only; nodes accept traffic from the app host's egress address only. Org
isolation: DocType permission hooks for compute records, key-prefix checks
for storage, both enforced in code and covered by tests.

## 7. Not now

Warm pools / autoscaling, spot pricing, multi-region, a node-side pull agent,
browser-to-S3 uploads, a credentials UI, cost accounting beyond the stored
estimate, removing the static path. GPU on vast.ai is the next provider
module behind the same interface.
