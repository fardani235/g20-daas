# On-Demand Processing — Operations Runbook

The lifecycle and its safety nets, what to watch, and what to do by hand when
you have to. Assumes the stack from the [deployment guide](deployment.md).
General stack operations are in [`docs/runbook.md`](../runbook.md).

## 1. The lifecycle, minute by minute

```
 user presses Start
   │  api.task.process_task: Pending/Failed/Cancelled/Completed -> Queued, enqueue process
   ▼
 process_task (worker, queue "long")                      every minute: process_pending_tasks re-enqueues Queued tasks
   ├─ ready compute instance linked?  ──yes──► dispatch to it ──► Running
   ├─ task already Provisioning?      ──yes──► return (sweep below drives it)
   ├─ provisioner configured?
   │     ├─ caps ok  ──► WebODM Compute Instance (Requested) ──► POST /instances ──► (Provisioning), task -> Provisioning
   │     ├─ cap hit  ──► task waits 60 s (no dispatch attempt spent)
   │     └─ provisioner down / no provider ──► fall through to static node
   └─ static WebODM Processing Node?  ──yes──► dispatch ──► Running;  no ──► backoff (1,2,4… min; Failed after 8)

 update_provisioning_tasks (scheduler, every minute) -> check_provisioning per Provisioning task
   ├─ GET /instances/<handle> says ready ──► instance Ready (hostname/port) ──► enqueue process_task ──► Running
   ├─ says pending                       ──► wait
   ├─ says failed/terminated             ──► instance Failed + destroyed, task -> Queued with backoff
   ├─ requested_at + provision timeout passed ──► same as failed ("provisioning timed out")
   └─ provisioner unreachable            ──► wait (bounded by the timeout above)

 update_running_tasks (every minute) -> poll_task: unchanged; polls the task's own node
   ├─ COMPLETED ──► collect outputs (node -> S3 -> COG -> S3, write-through cache) ──► Completed ──► release instance
   ├─ FAILED / CANCELED on node ──► Failed / Cancelled ──► release instance
   └─ node unreachable ──► poll_failures++ (Failed after 15 in a row) ──► release instance

 reap_job (every minute) — the safety net, see §3
 evict_job (hourly, :17) — serving-cache eviction, see §5
 sync_pending_job (every 5 min) — S3 backfill of host-only blobs
```

Every terminal state (Completed, Failed, Cancelled, task deleted) calls
`compute.release_for_task`, which asks the provisioner to destroy the instance.
That call is best-effort: on failure the record goes to **Terminating** with
`destroy_attempts` incremented and the sweep retries it every minute. A failed
destroy never changes the task's outcome.

## 2. Concurrency limits

| Limit | Setting | Default | Counts |
|---|---|---|---|
| Global | `WEBODM_COMPUTE_MAX_INSTANCES` | 5 | instances in Requested / Provisioning / Ready |
| Per organization | `WEBODM_COMPUTE_MAX_INSTANCES_PER_ORG` | 2 | same, for the task's org |

A task that hits a cap stays **Queued** with `last_error` = *global compute cap
reached (5/5)* or *organization compute cap reached (2/2)* and is retried every
minute without consuming its dispatch attempts. Raise the limits in `.env`
and `docker compose up -d frappe-worker frappe-scheduler frappe-web`; they are
read from site config on every dispatch, no migration needed.

Also bounding the bill:

| Guard | Setting | Default | Effect |
|---|---|---|---|
| Provision timeout | `WEBODM_COMPUTE_PROVISION_TIMEOUT_SECONDS` | 900 | a node not ready in time is destroyed; the task retries with backoff |
| Lifetime budget | `WEBODM_COMPUTE_MAX_LIFETIME_SECONDS` | 43200 (12 h) | an instance older than this is destroyed and its task **Failed** ("exceeded its lifetime budget") |
| In-box self-destruct | (derived) | budget + 10 min | the node schedules its own `shutdown`, launched with shutdown = terminate — survives a dead control plane |
| Orphan grace | `WEBODM_COMPUTE_ORPHAN_GRACE_SECONDS` | 600 | a provider-side instance with our tags but no live record, older than this, is destroyed |

Set the lifetime budget above the longest task you expect; a 1 000-image
ultra-quality run can take 6–8 hours on `c6i.8xlarge`.

## 3. The sweep (`compute.reap`)

Runs every minute in the scheduler; idempotent. Rules, in order, for every
record in Requested / Provisioning / Ready / Terminating (plus Failed records
whose destroy was never confirmed):

1. task deleted, or Completed / Failed / Cancelled → **destroy**
2. task now linked to a different instance (restart) → **destroy** the old one
3. `expires_at` passed → **destroy** and mark the task **Failed**
4. Requested/Provisioning longer than the provision timeout → **destroy**, task back to Queued (backoff)
5. Terminating → **retry destroy**
6. provider-side listing (`GET /instances`): a `webodm:managed` instance of this
   deployment with no live record and older than the orphan grace → **destroy**,
   logged as *reaper: destroyed orphan …*

Instances are therefore gone within one sweep (≤ 1 minute) of a terminal
state, orphans within grace + 1 minute. Destroy failures are logged as
`WebODM Compute`; after 5 consecutive failures on one record the title becomes
**`WebODM Compute ALERT`** — that is the signal to look at the provider by hand
(§7).

## 4. What to watch

**Desk → WebODM Compute Instance.** One row per provisioned node: status,
handle, task, organization, requested/ready/terminated timestamps, hourly rate
and the cost estimate filled at teardown. Healthy steady state: nothing in
Provisioning for longer than a few minutes, nothing in Ready without a Running
task, nothing in Terminating.

**Error Log** titles:

| Title | Meaning |
|---|---|
| `WebODM Compute` | provisioner errors, destroy retries, fallbacks to the static node, orphan destroys |
| `WebODM Compute ALERT` | a destroy has failed 5+ times, or an orphan could not be destroyed — a machine may be running unbilled-for |
| `WebODM Storage` | object storage errors (sync, relay, cache fill, backfill) |
| `WebODM Geospatial` | COG conversion failures (transient ones retry; the final fallback stores the raw raster) |
| `WebODM Processing` | dispatch/poll events as before |

**Queries** (run inside `frappe-web`; alias `bench='… frappe --site webodm.local'`):

```bash
# live instances
bench execute frappe.client.get_list --kwargs '{"doctype":"WebODM Compute Instance","filters":{"status":["in",["Requested","Provisioning","Ready","Terminating"]]},"fields":["name","task","organization","status","handle","requested_at"]}'
# tasks waiting on a node or capacity
bench execute frappe.client.get_list --kwargs '{"doctype":"WebODM Task","filters":{"status":["in",["Queued","Provisioning"]]},"fields":["name","status","last_error","next_attempt_at","compute_instance"]}'
# spend estimate, last 30 days
bench execute frappe.client.get_list --kwargs '{"doctype":"WebODM Compute Instance","filters":{"terminated_at":[">","2026-08-25"]},"fields":["sum(estimated_cost) as est"]}'
# cache stats: run one eviction pass with the configured limits (dry: pass huge limits)
bench execute webodm_core.storage.cache.evict --kwargs '{"max_bytes":10000000000000,"idle_seconds":10000000000}'
```

**Provider side** (should match the Desk list, and nothing else):

```bash
aws ec2 describe-instances --filters Name=tag:webodm:managed,Values=true Name=instance-state-name,Values=pending,running \
  --query 'Reservations[].Instances[].{id:InstanceId,task:Tags[?Key==`webodm:task`]|[0].Value,launched:LaunchTime,type:InstanceType}' --output table
```

**Bill.** EC2 hours for `webodm:managed` instances (tag-based cost allocation
works: activate the `webodm:deployment` and `webodm:org` tags as cost
allocation tags) and S3: storage, requests, and internet egress if the app
host is off AWS. A sudden jump in egress usually means a cold cache being
refilled repeatedly — check `WEBODM_CACHE_MAX_BYTES` against the working set.

## 5. The serving cache

`sites/<site>/private/files` is the cache. Every task image, task output and
plugin output keeps its `/private/files/<name>` URL; the blob behind it may be
absent and is re-fetched from S3 on demand. The hourly `evict_job`:

1. drops blobs not used for `WEBODM_CACHE_IDLE_SECONDS` (7 days),
2. then, if the cache still exceeds `WEBODM_CACHE_MAX_BYTES` (50 GiB), drops
   least-recently-used blobs until it fits.

It never touches: blobs without an S3 copy (`storage_key` empty — legacy or
not yet synced), blobs of tasks that are Queued / Provisioning / Running, or
outputs of Running plugin runs. "Used" means read through the app (tiles,
viewer, download, plugin staging); access time is bumped explicitly, so
`noatime` mounts are fine.

Sizing: the cache should hold the datasets people are actively looking at.
Orthophoto COGs are 0.5–5 GB, LAZ 0.2–2 GB, GLB 0.05–2 GB per task. Start at
50 GiB on a 100 GiB host, watch `bytes_after` in the scheduler log.

Force-evict everything evictable (e.g. disk emergency):

```bash
bench execute webodm_core.storage.cache.evict --kwargs '{"max_bytes":0,"idle_seconds":0}'
```

Nothing is lost: S3 is authoritative; the next view refills. The backfill
(`sync_pending_job`, every 5 min, 20 items per run) is what makes legacy
host-only blobs evictable — until a row has a `storage_key`, it is protected.

## 6. Falling back to a static node

The static path is always there. To stop provisioning:

```bash
# .env: PROVISIONER_URL=
docker compose up -d frappe-web frappe-worker frappe-scheduler
```

New Starts go to `WebODM Processing Node` rows (the seeded `Local NodeODM` →
the `nodeodm` service) immediately. Tasks currently **Provisioning** keep
being checked (`check_provisioning` reads `provisioner_url`; with it empty the
describe call fails as *unavailable*, the timeout expires, the task goes back
to Queued and dispatches to the static node). Tasks **Running** on a cloud node
finish there and are released normally — keep the `provisioner` container
running until the last one is gone, or destroy by hand (§7).

To pause **the sweep's provider access** too (provisioner container down): the
records stay, `WebODM Compute` errors accumulate, and the in-box self-destruct
still terminates nodes at budget + 10 min. Prefer keeping the provisioner up.

## 7. Finding and killing an instance by hand

From the app (goes through the provisioner, updates the record):

```bash
bench execute webodm_core.webodm_core.processing.compute.reap        # run the sweep now
# destroy one record (takes the document, so use the console):
bench console
>>> from webodm_core.webodm_core.processing import compute
>>> compute.destroy_instance(frappe.get_doc("WebODM Compute Instance", "<name>")); frappe.db.commit()
```

Directly at the provisioner (from inside the stack; add
`-H "Authorization: Bearer $(cat secrets/provisioner_api_token.txt)"`):

```bash
docker compose exec frappe-web curl -s http://provisioner:5002/instances            # what the provider has
docker compose exec frappe-web curl -s -X DELETE http://provisioner:5002/instances/aws:i-0abc…
```

Directly at AWS (last resort; the sweep then closes the record as
*terminated*):

```bash
aws ec2 terminate-instances --instance-ids i-0abc…
```

Never `stop` an instance: a stopped node is not billed for compute but its
volume is, and the app treats stopped as gone. `terminate` only.

## 8. Rotating credentials

* **S3 keys**: create the new key, write the secret files, `docker compose up -d`
  (recreates the containers that mount them), then delete the old key. In-flight
  relays fail with *access denied* once the old key dies and retry on the next
  poll with the new one.
* **Provisioning key**: same, for `aws_provisioner_*`; only the `provisioner`
  container needs recreating.
* **Provisioner API token**: regenerate `secrets/provisioner_api_token.txt`,
  recreate `provisioner` and the three Frappe services together.
* **Node tokens** are per run and die with the node.

## 9. Upgrading NodeODM on the nodes

`PROVISIONER_NODEODM_IMAGE` → new digest; with a prebaked AMI also re-run
`infra/aws/bake-nodeodm-ami.sh` and update `PROVISIONER_AWS_AMI_ID` (otherwise
every node pulls the new image at boot, which works but is slow). Running
tasks are unaffected; the next provisioned node uses the new engine.
