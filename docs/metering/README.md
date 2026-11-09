# Usage metering

The record of what each organization consumed: how long rented machines
worked for it, how many analysis runs it completed, how many bytes it kept.
It is the layer prices, a prepaid balance, billing screens and payment will
sit on. It contains none of them.

This document is the design record and the operator's guide. The normative
behaviour is `openspec/specs/usage-metering/spec.md`; the change, with the
decisions as they were argued, is
`openspec/changes/archive/2026-10-19-add-usage-metering/`.

Contents: [the question it answers](#1-the-question-it-answers) ·
[the ledger](#2-the-ledger) · [the three meters](#3-the-three-meters) ·
[time and periods](#4-time-and-periods) ·
[what is not in a total](#5-what-is-not-in-a-total) ·
[what changed in existing code](#6-what-changed-in-existing-code) ·
[where the brief and the code disagreed](#7-where-the-brief-and-the-code-disagreed) ·
[decisions](#8-decisions) · [checking a figure yourself](#9-checking-a-figure-yourself) ·
[operations](#10-operations) · [what was measured](#11-what-was-measured) ·
[what was not verified, and what is assumed](#12-what-was-not-verified-and-what-is-assumed) ·
[for the layers that come next](#13-for-the-layers-that-come-next) ·
[where the code is](#14-where-the-code-is)

---

## 1. The question it answers

*What did this customer consume in this period?*

```bash
# a member, for their own organization (session cookie)
GET /api/method/webodm_core.api.usage.summary?start=2026-10-01&end=2026-11-01

# an operator, for any organization
bench --site <site> execute webodm_core.metering.report.summary \
  --kwargs '{"organization": "Acme", "start": "2026-10-01", "end": "2026-11-01"}'
```

`start` and `end` are instants, and the period is `(start, end]`. Write a
bare date (`2026-10-01` = midnight UTC) or a date-time with `Z` or an offset
(`2026-10-01T00:00:00+07:00`). A date-time without an offset is refused: it
does not say which instant it means.

The answer — real output of `scripts/metering/e2e.py` on the test site,
abridged (names and times differ from run to run):

```json
{
 "organization": "E2E Meter Co",
 "period": {"start_utc": "2026-10-18T00:00:00Z", "end_utc": "2026-10-20T00:00:00Z",
            "counted_until_utc": "2026-10-19T05:15:13Z"},
 "record_count": 4,
 "meters": {
  "compute": {"unit": "second", "quantity": 3600, "waived_quantity": 900, "records": 2,
   "by_dimension": [{"dimension": "aws/cpu", "quantity": 0, "waived_quantity": 900, "records": 1},
                    {"dimension": "aws/cpu-large", "quantity": 3600, "waived_quantity": 0, "records": 1}],
   "waived_by_reason": {"never_ready": 900}},
  "analysis_run": {"unit": "run", "quantity": 1, "waived_quantity": 1, "records": 2,
   "waived_by_reason": {"failed": 1}},
  "storage": {"unit": "byte-day", "quantity": 0, "records": 0,
   "days": {"in_period": 2, "measured": 0, "in_progress": ["2026-10-19"],
            "before_first_measurement": ["2026-10-18"], "not_measured": [],
            "pending": [], "not_started": [], "first_measured_day": "2026-10-19"}}
 },
 "in_progress": [
  {"meter": "compute", "source_name": "2ediq6j1et", "state": "Ready", "elapsed_so_far": 1201,
   "work_dispatched": true, "provisional": true,
   "note": "running; counts only once released, and only if a job was dispatched to it"},
  {"meter": "storage", "day": "2026-10-19", "quantity_when_the_day_ends": 364348,
   "counts_from_utc": "2026-10-20T00:00:00Z", "provisional": false,
   "note": "fixed; it enters the storage total when the UTC day ends"}
 ],
 "unrecorded": {"compute": 0, "analysis_run": 0},
 "provisional": true,
 "provisional_reasons": [
  {"code": "period_open", "detail": "the period ends at 2026-10-20T00:00:00Z, after now"},
  {"code": "work_in_progress", "detail": "1 item(s) still running; they are not in any total"},
  {"code": "storage_day_in_progress", "detail": "2026-10-19 is measured and counts once the day has ended"}
 ],
 "gaps": {"storage_days_not_measured": ["2026-10-18"],
          "note": "These days have no storage measurement and never will; nothing is counted for them."},
 "display": {"note": "rounded for reading; use the integer quantities for any calculation",
             "compute": "1 h 0 min 0 s of machine time counted"}
}
```

How to read it:

- **`meters`** are totals of usage records whose interval has ended.
  `quantity` is what counts. `waived_quantity` was measured and deliberately
  not counted — here a machine that never came up (900 s) and a run that
  failed.
- **`in_progress`** is not in any total. A machine that is still up is
  *provisional*: 1201 s is what it would count if released this second.
  Today's storage measurement is the opposite — fixed, but standing for a
  day that is not over; it enters the total at midnight UTC.
- **`unrecorded`** counts finished machines and runs the sweep has not
  recorded yet. Their quantities are unknown until it does.
- **`gaps`** are storage days that ended without a measurement. They count
  nothing and never will. Nothing is interpolated.
- **`provisional`** says whether the totals can still change, and why.
- **`display`** is for a person. Never compute from it.

The records behind a total, and one record with its evidence:

```bash
GET /api/method/webodm_core.api.usage.records?start=…&end=…&meter=compute&with_evidence=1
GET /api/method/webodm_core.api.usage.record?name=<record>
```

Add `as_of=<instant>` to `summary` or `records` to get the answer as it
stood at an earlier moment (see §4).

## 2. The ledger

One DocType, `WebODM Usage Record`. One row is one fact about one
organization.

| Field | Meaning |
|---|---|
| `organization` | whose usage |
| `meter` | `compute` \| `analysis_run` \| `storage` |
| `dimension` | what kind: `aws/cpu-large`, a plugin id, `standard`. What a price list will key on. Never a price |
| `quantity`, `unit` | what counts, a whole number of `second` \| `run` \| `byte-day` |
| `waived_quantity`, `waived_reason` | what was measured and not counted, and why |
| `period_start_utc`, `period_end_utc` | the interval the fact covers — UTC, whole seconds |
| `recorded_at_utc`, `recorded_by` | when the row was written (later than the period when catching up) |
| `source_doctype`, `source_name` | the row it came from (plain text: it outlives its source) |
| `rule` | which rule derived the quantity: `compute/v1`, `analysis_run/v1`, `storage/v1`, `reversal/v1` |
| `evidence` | canonical JSON: every source fact the rule used, exactly as read; what was derived; what was and was not checked |
| `idempotency_key` | the identity of the fact; unique in the database |
| `entry_type`, `reverses`, `note` | `usage`, or a `reversal` of one earlier record |
| `caveats` | anything a reader should know before relying on the row |
| `record_hash` | SHA-256 over the row's content |

Properties, and where each is enforced:

- **Integers only.** Both quantities are 64-bit integers. There is no
  float, currency, amount or rate column. (`test_sweep`:
  `test_the_cost_estimates_never_reach_a_record` asserts the schema.)
- **Recorded once.** The unique index on `idempotency_key` refuses a second
  row for the same fact, whichever process tries and however many try at
  once (`ledger._insert`; `test_concurrency`).
- **Never changed.** The controller refuses `save`, `db_set`, rename and
  delete; only `ledger.record` may insert; no role has write or delete; the
  permission hook denies every write, for platform admins too. A mistake is
  corrected by `ledger.reverse(name, note)`, which adds the negation as a new
  row. A write that goes around the document API (raw SQL) is not prevented;
  it is *detected*, by the hash (§9).
- **Self-explaining.** The rules are pure functions of the evidence stored
  on the row, so a quantity can be rebuilt from the row alone with its
  source deleted (`rules.rederive`).
- **Organization-scoped** like every other tenant DocType (both permission
  hook maps; `api/test_usage.py`).

## 3. The three meters

### 3.1 `compute` — seconds a rented machine was useful to a job

Source: `WebODM Compute Instance`. One record per instance, written by the
sweep once the instance is *closed*.

- Interval: `ready_at` → `released_at`. From the machine becoming useful to
  the platform first asking for it back. Booting is not counted; the time
  the platform spends failing to destroy a machine is not counted.
- Rounding: start up, end down, to the second.
- Dimension: `<provider>/<instance class>`.

| Situation | Outcome |
|---|---|
| Became Ready, a job was dispatched, released when the job ended | counts |
| Never became Ready | waived `never_ready` |
| Ready, but no job was ever accepted by the node | waived `no_work_dispatched` |
| Killed by the lifetime budget, or closed as `Failed` | waived `platform_failure` |
| No `released_at` and the destroy was retried (rows from before this change) | waived `end_time_not_recorded` |
| End before start (bad data, or wholly inside a repeated clock hour) | waived `invalid_interval` |
| Deleted by hand while still up | waived `deleted_before_release` |

A job **cancelled**, or **failed after work started**, counts. See §8, the
first open question.

Processing on a **static** node is not metered: no machine record exists
(§7, F2).

### 3.2 `analysis_run` — plugin runs

Source: `WebODM Plugin Run`. Every run ends in exactly one record.

| Run | Outcome |
|---|---|
| `Completed` | `quantity = 1` |
| `Failed` | waived `failed` |
| `Cancelled` | waived `cancelled` |
| Deleted while `Queued` / `Running` | waived `deleted_before_finishing` |

The dimension is the plugin id. The evidence also keeps the plugin's type,
origin (upload or marketplace), product, release and version, and the run's
start and end — enough to price by run or by time later.

A run is recorded by the sweep, **or as it is deleted** (an `on_trash`
hook): starting a plugin on a task deletes that plugin's previous run on it,
so the sweep alone would lose runs. Both routes use the same key.

### 3.3 `storage` — bytes held on a UTC day

No source row: storage is measured. `metering.storage.sample_job` runs every
ten minutes and, for each organization that has no record for the current
UTC day, takes one.

- **What is measured:** the `file_size` of every `File` row attached to the
  organization's datasets, tasks and plugin runs — imagery, ground control
  files, outputs, the viewer octree, analysis outputs. Each distinct file
  URL of a document counts once. Logical bytes: an object is one size
  whether it is in the bucket, in the serving cache or both.
- **What is not:** plugin packages, thumbnails, transient relay objects,
  and anything in the bucket that no row describes. The bucket itself is not
  listed. Every measurement states this in `evidence.not_checked`.
- **The record:** `quantity` = bytes, in byte-days ("this level stands for
  this day"); `evidence.inputs.lines` = one line per document with its file
  count, bytes and title. The quantity must equal the sum of the lines.
- **Zero is a measurement.** An organization holding nothing gets a record
  saying 0. No record means *not measured*.
- **Only today.** `storage.sample` has no parameter for a day. A missed day
  stays missed.

Average level over a period = `quantity / days.measured` bytes. One
GB-month at 30 measured days = 30 × 10⁹ byte-days.

## 4. Time and periods

- **UTC, whole seconds.** Source timestamps are local wall-clock time in the
  site's time zone (Frappe's `now_datetime()`); records convert them and
  keep the zone used in the evidence.
- **Doubt goes to the customer.** Interval start rounds up, end rounds down.
  A local time that happened twice (the hour repeated when daylight saving
  ends) is read as the later instant for a start and the earlier for an
  end; the record gets a caveat.
- **A record belongs to the period in which it ended:**
  `start < period_end_utc <= end`. Records are never split. A machine that
  ran 23:00–01:00 across a month end is in the later month, whole. Adjacent
  periods share nothing and lose nothing, for any choice of boundary
  (`test_report`: `TestPeriods`).
- **Nothing counts before its interval has ended.** With an open period the
  totals run to now (`counted_until_utc`), not to `end`.
- **Storage days are UTC days.** The record for the 19th ends at
  2026-10-20T00:00:00Z, so it is in October whichever way October is cut.
- **`as_of`** restricts to records written up to an instant and treats that
  instant as now. Records never change, so this reproduces an earlier
  answer — exactly, for every record whose transaction had committed by
  then (limits: §12). `as_of` in the future is refused.
- Periods longer than 400 days are refused; ask in parts.

## 5. What is not in a total

| | Where it appears | Why it is not counted |
|---|---|---|
| A machine that is up; a run that is queued or running | `in_progress`, `provisional: true` | not finished; no record exists |
| Today's storage measurement | `in_progress`, `provisional: false` | its day has not ended |
| A closed machine or finished run the sweep has not reached | `unrecorded` (a count) | its quantity is not known yet |
| A storage day with no measurement | `gaps`, `days.not_measured` | there is no number, and none is made up |
| Usage the platform absorbed | `waived_quantity` on the record | measured, deliberately not counted |

## 6. What changed in existing code

Metering reads. It writes to exactly one existing table, three columns:

**`WebODM Compute Instance`** gained `dispatched_at`, `released_at`,
`release_reason`. `compute.stamp_once` writes each at most once, in its own
savepoint, swallowing any error; nothing in the pipeline reads them.

- `task_runner.process_task` calls `compute.mark_dispatched(node)` after the
  task is already `Running`.
- `compute.destroy_instance(inst, reason=None)` calls `mark_released` on the
  **first** attempt only (a retry is later than the moment the machine
  stopped being needed, so a retry never writes a release time).
  `release_for_task`, `reap` and `check_provisioning` pass the reason.

**Hooks** (`hooks.py`): `on_trash` for `WebODM Plugin Run` and
`WebODM Compute Instance` → `metering.hooks` (record before deletion; cannot
fail the delete); two cron entries; the new DocType in both permission maps.

**The compute safety sweep was broken and is fixed** (§7, B1). This is not
metering, and is called out so it can be reviewed separately: one filter in
`compute.reap`, and `processing/test_compute_database.py`.

Nothing else behaves differently. `test_no_harm.py` asserts the pipeline
modules do not import the metering package.

## 7. Where the brief and the code disagreed

Checked against the code. Detail in the change's `design.md`.

| | The brief says | The code does | Consequence |
|---|---|---|---|
| F1 | The number of analysis runs can be worked out later from records that still exist | Starting a plugin on a task **deletes the previous run** (`api/plugins._delete_previous_runs`); deleting a task deletes its runs; members can delete runs | Runs replaced or deleted before deployment are gone. From deployment on, a run is recorded as it is deleted |
| F2 | Every processing job runs on a rented machine the platform records | Only with the provisioner. A task on a static node leaves no machine record, and a task has no start/finish time | Static-node processing is not metered |
| F3 | The platform records how long each machine existed | It does not record whether the machine got work, when we stopped needing it (`terminated_at` is the *confirmed* destroy), or why | Three columns added; old rows fall back or are waived |
| F4 | It records how big every result is | Only `File` rows do, on every deployment. Asset rows exist only with a bucket; plugin runs have no size | Storage is measured from `File` rows |
| F5 | — | Source timestamps are local time, not UTC | Records store UTC and the zone used |
| F6 | The cost estimates are rough and must not become billable | They are floats from an operator-set table; nothing in metering reads them | Asserted by tests |
| F7 | — | Members can edit or delete a `WebODM Plugin Run` through `/api/resource`; Frappe does not enforce `read_only` server-side | A member can alter a run before it is recorded. Not fixed here |

Found while building, by tests against a real database:

| | |
|---|---|
| **B1** | **`compute.reap()` raised on every run on PostgreSQL.** `{"terminated_at": ["is", "not set"]}` on a Datetime column becomes `terminated_at = ''`, which PostgreSQL rejects. `reap_job` caught and logged it each minute. Destroy retries, the lifetime budget, the never-came-up timeout and orphan reaping were therefore not happening. The existing tests mock `frappe.get_all`. Fixed. Verified on the 16.34.0 image; not checked on 16.26.3 |
| **B2** | A database `SUM` comes back from Frappe's PostgreSQL driver as a float (exact to 2⁵³). Totals leave the database as text and are parsed as integers |
| **B3** | Frappe runs PostgreSQL at REPEATABLE READ: a recorder that loses a race cannot see the winner's row. The database's refusal is taken as the proof |
| **B4** | Frappe's HTML sanitizer rewrites text after `before_insert` (`<OPS-1423>` disappears). The record's text fields opt out and the hash is re-checked after the insert |

## 8. Decisions

Each is argued in `design.md` (D1–D17); the short form:

| | Decision | Instead of |
|---|---|---|
| D1 | One append-only ledger for all meters | a table per meter; stored roll-ups |
| D2 | Integers in natural units, no money | amounts computed at record time |
| D3 | `quantity` and `waived_quantity` as two columns | a `billable` flag every caller must remember |
| D4 | A unique key in the database; random record names | care in application code; names derived from the key (withdrawn — they were guessable) |
| D5 | A record belongs to the period in which it ended | clipping records at period boundaries |
| D6 | UTC, whole seconds, rounded toward the customer | local time; fractional seconds |
| D7 | Three lifecycle facts written once on the instance | deriving them later from the task, which forgets |
| D8 | Counts when Ready, given work, not killed by us, both ends known | counting whatever the instance row says |
| D9 | Every run ends in one record; capture on deletion | sweep only |
| D10 | Storage: daily, from `File` rows, per-document evidence | hourly; a bucket listing; per-file evidence |
| D11 | Nothing counts before its interval ends; provisional is separate; gaps are not provisional | a running total |
| D12 | Totals computed from records every time | stored totals |
| D13 | Immutable in the application, tamper-evident in the database | database triggers |
| D14 | A record carries its own evidence | relying on the source row |
| D15 | Organization-scoped; nothing copied from another organization | — |
| D16 | Two independent scheduled jobs, on by default | one job; a switch |
| D17 | Top-ups not built (a balance fact, not usage); the ledger's write pattern is the one to reuse for one-time facts | — |

**Open questions** — things I chose and am not sure of:

1. **Does a job that failed after work started count?** Built: yes (also a
   cancelled one). The brief says "failed *before* doing any work", under a
   heading that says "do not charge for something that produced nothing".
   Changing it for future records is a new rule version; records already
   written keep `release_reason` (`task:Failed`, `task:Cancelled`, …), so a
   rating layer can waive them without rewriting anything. Decide before
   prices exist.
2. **Daily storage.** Between two measurements a customer can hold data for
   23 hours and pay for none of it, or pay a day for something uploaded a
   minute before the measurement. The grain is on the record, so it can be
   shortened without redoing anything.
3. **Who may read usage.** Every member, like the tasks and datasets the
   records describe. Owners-only is one condition in
   `permissions.has_usage_record_permission`.
4. **History.** The first sweeps record every closed machine and finished
   run automatically, with fallbacks for facts that did not exist yet;
   where a fact is missing the time is waived. History may be
   under-counted. It is not over-counted.

## 9. Checking a figure yourself

Nothing here needs to be taken on trust.

The tooling used for §11 is in the repository — `scripts/metering/`: a
throwaway test site, the end-to-end script and the seeded-fault script
(`scripts/metering/README.md`).

**Does the ledger agree with itself?**

```bash
bench --site <site> execute webodm_core.metering.verify.verify
bench --site <site> execute webodm_core.metering.verify.verify \
  --kwargs '{"organization": "Acme", "start": "2026-10-01", "end": "2026-11-01"}'
```

For every record in scope it recomputes the content hash; runs the rule
named on the row over the evidence stored on the row and compares quantity,
waived quantity, reason, interval and dimension; checks a storage quantity
equals the sum of its lines; checks the idempotency key matches the source;
checks a reversal exactly negates its original; then adds the totals up in
Python and again in the database and compares. `{"ok": true, "problems":
[]}` is the only good answer.

**Where did this number come from?**

```bash
bench --site <site> execute webodm_core.metering.verify.trace --kwargs '{"name": "<record>"}'
```

The record, the quantity re-derived from its evidence, and — if the source
row still exists — which of its facts differ *now* from when it was
recorded (a record is not revised when its source changes later; this tells
you that it changed).

**By hand, in SQL** — the total, with no application code in between:

```sql
SELECT meter, SUM(quantity), SUM(waived_quantity), COUNT(*)
FROM "tabWebODM Usage Record"
WHERE organization = 'Acme'
  AND period_end_utc >  '2026-10-01 00:00:00'
  AND period_end_utc <= '2026-11-01 00:00:00'
GROUP BY meter;
```

**One machine, by hand.** Take the record's `evidence.inputs`: convert
`ready_at` and `released_at` from `site_time_zone` to UTC, round the first
up and the second down, subtract. That is `quantity`.

**One storage day, by hand.** Add `bytes` over `evidence.inputs.lines`.

**That nothing is recorded twice:**

```sql
SELECT idempotency_key, COUNT(*) FROM "tabWebODM Usage Record" GROUP BY 1 HAVING COUNT(*) > 1;   -- no rows
SELECT indexdef FROM pg_indexes WHERE tablename = 'tabWebODM Usage Record';                      -- a UNIQUE index on idempotency_key
```

**What `verify` cannot see:** a row deleted behind the application leaves
nothing to check — deletion is prevented (controller, permissions), not
detected. The sweep will notice the source has no record and record it
again; a storage day deleted that way is simply gone.
(`test_verify`: `test_what_verification_cannot_see`.)

## 10. Operations

**Deploy.** `bench migrate` (one table; three nullable columns), then
restart workers and the scheduler. There is no setting and no switch:
measurement starts with the first scheduler tick. The first sweeps record
history (every closed instance, every finished run that still exists).

**Schedule.**

| Job | Every | Does |
|---|---|---|
| `webodm_core.metering.storage.sample_job` | 10 min | today's storage measurement for each organization that lacks one |
| `webodm_core.metering.sweep.run_job` | 5 min | records closed machines and finished runs that have no record; at most 2000 records or 120 s per run |

Both run on the `default` queue as separate jobs; neither raises.

**Is it keeping up?**

```bash
bench --site <site> execute webodm_core.metering.report.health
```

- `storage.not_measured_today` should be empty a few minutes after 00:00 UTC.
  An organization still listed late in the UTC day means the storage job is
  not running — **and a day that ends unmeasured cannot be recovered.**
- `unrecorded` should be zero or small, and not growing.
- Errors are in the Error Log under **WebODM Metering** (a source that
  cannot be recorded is logged at most hourly and retried every run).

**If the scheduler was down.** Machine time and surviving runs are recorded
when it comes back. Storage days that passed are gaps; `summary` names
them. Do not write rows for them.

**To measure one organization now** (same code the job runs):

```bash
bench --site <site> execute webodm_core.metering.storage.sample_all --kwargs '{"organizations": ["Acme"]}'
```

**To correct a record** (platform admin; adds a row, changes none):

```bash
bench --site <site> execute webodm_core.metering.ledger.reverse \
  --kwargs '{"name": "<record>", "note": "why", "ignore_permissions": true}'
```

**Things that will surprise you once:**

- An organization with usage records cannot be deleted (the records link
  to it). Intended.
- The first working compute sweep (B1) will destroy instances that have been
  stuck in `Terminating`, and reap orphans. That is the sweep doing, for the
  first time, what it was written to do. The time of such instances is
  waived, not counted.
- Desk shows the `*_utc` fields as if they were local time. They are UTC.
- `docs/on-demand-processing/user-guide.md` tells customers a machine "is
  billed by the minute while it exists". What is recorded here is seconds
  from Ready to release. Reword it when prices are set.

## 11. What was measured

On a throwaway site: PostgreSQL 16 (PostGIS image), the
`webodm-frappe:16.34.0` image with this worktree's `webodm_core` mounted,
site time zone `Asia/Kolkata`.

**Tests.** `webodm_core` before: 844 passing. After: 1103 passing, 0
failing, 8 skipped as before (fresh site). 259 new:

| File | Tests | What it pins |
|---|---|---|
| `metering/test_timeutil.py` | 19 | rounding, the repeated and skipped hour, unambiguous instants, UTC days |
| `metering/test_rules.py` | 45 | every way a machine or run can end; storage sums past 2⁵³; evidence → quantity; hash |
| `metering/test_ledger.py` | 28 | recorded once; immutable by every route; reversals; text stored exactly |
| `metering/test_guard.py` | 9 | isolation: swallow, undo, usable transaction |
| `metering/test_storage.py` | 21 | adds up; once a day; never another day; evidence outlives files; nothing holds it up; isolation |
| `metering/test_sweep.py` | 36 | sources → records; history; deletion capture; the lifecycle stamps |
| `metering/test_pipeline.py` | 11 | the real `task_runner` / `compute` functions on real rows, every ending, to the record |
| `metering/test_no_harm.py` | 17 | metering broken on purpose at each touchpoint; the job still ends as it would |
| `metering/test_report.py` | 20 | totals = sum of records; period boundaries; provisional; gaps; `as_of` |
| `metering/test_verify.py` | 23 | each kind of tampering is reported; what is not |
| `metering/test_concurrency.py` | 4 | recorders on separate connections: forced interleaving and plain races |
| `api/test_usage.py` | 21 | the API; isolation through every route, including real HTTP requests |
| `processing/test_compute_database.py` | 5 | the compute sweep on a real database |

The four failure modes named in the brief, and the tests that would catch
each:

- *A double count* — `test_concurrency` (8 sweeps at once over 24 sources;
  8 storage jobs at once; a sweep racing a delete; one recorder holding an
  uncommitted row while another records the same fact), `test_ledger`
  (`test_the_database_itself_refuses_a_second_row_for_the_key`),
  `test_storage` (two `File` rows for one URL).
- *A broken job* — `test_no_harm` (Python exceptions, failed SQL statements
  and an unimportable package, during dispatch, teardown and delete),
  `test_guard`, `test_pipeline`.
- *A figure leaking between customers* — `api/test_usage.py` (usage API,
  `get_list`, counts and aggregates, `frappe.client`, Desk list and link
  search, `/api/resource` and `/api/method` over HTTP as a member),
  `test_storage` and `test_report` isolation tests, `test_sweep`
  (a run pointed at another organization's task).
- *A total that cannot be rebuilt* — `test_report`
  (`test_totals_are_the_sum_of_the_records`: paged records, and a `SUM` in
  the database), `test_rules` (`TestEvidence`), `test_verify`.

**Seeded faults.** `scripts/metering/seeded_faults.py` applies 61
deliberate bugs, one at a time, to a copy of the code — a guard removed, a
sign flipped, a filter dropped, a savepoint taken out — and runs the tests
that should notice. 59 were caught. The other two are the two halves of one
double filter in the storage query (the organization is restricted in the
subquery and again in the join); removing either alone changes no result,
and removing both — a fault of its own, among the 59 — is caught. Two no-op
changes are included to check the harness and are reported as surviving, as
they should be.

The exercise found two things the tests did not cover, both since fixed:
the permission hook's refusal of writes was only ever tested together with
role permissions (a direct test was added), and a re-raising `guard` was
invisible to the delete tests because the hook catches a second time (a
test file for `guard` itself was added).

**Independent review.** A second reviewer, given the specification and the
code but not the author's reasoning, found thirteen defects — one of them
an over-count on old `Terminating` rows that no test of mine covered. All
are fixed or recorded; see "What an independent review found" in
`design.md`.

**End to end** (`scripts/metering/e2e.py`), through the two functions the
scheduler calls (`storage.sample_job`, `sweep.run_job`), each called twice:
5 records after the first pass, 5 after the second. The summary in §1 is
that script's output.
The member's totals equalled the sum of the records returned to them, per
meter. `verify` reported no problems before and after the task and dataset
were deleted; the storage record still listed all three documents. With the
clock moved past midnight UTC, the same request counted the day
(0 → 364,348 byte-days).

The `bench execute` commands in §9 and §10 (`report.health`,
`verify.verify`, `report.summary`, `storage.sample_all`) were run as
written on the test site.

**Timings** (one laptop, one site; indications, not benchmarks — the last
run of `scripts/metering/e2e.py`):

| | |
|---|---|
| Storage measurement, one organization, 200,000 files in 400 datasets (1.6 TB) | 3.5 s (three runs: 3.48–3.56). Earlier runs took 0.8–2.1 s with a different query plan, and 9 s once, immediately after bulk-loading the rows. Exact to the byte; 46 kB of evidence |
| Sweep, backlog of 2,000 closed machines | 25 s (81 records/s), one transaction each |
| Sweep with nothing to do | 6 ms |
| `summary` over 2,000 records | 46 ms |
| `verify` over 2,000 records | 0.33 s |

## 12. What was not verified, and what is assumed

Not verified:

- **No production data.** Everything ran on a throwaway site with
  constructed rows. How many existing instances are stuck in `Terminating`,
  how many lack the facts metering needs, and what the first sweep will do
  to them in volume, I do not know.
- **No real cloud.** The provisioner and NodeODM were replaced by in-memory
  stand-ins in `test_pipeline.py`. No machine was rented; no real destroy
  failed.
- **No real scheduler.** The two job functions were called directly (and the
  scheduler was confirmed to have registered both, with the right cron
  expressions, on the `default` queue). They were not left to run on a
  schedule for days, across a UTC midnight, or under a busy worker.
- **The bucket.** Storage is measured from `File` rows. It was never
  compared with a bucket listing or a provider's bill. Orphaned objects,
  incomplete multipart uploads and object versions are invisible to it.
- **MariaDB.** PostgreSQL only.
- **Frappe 16.26.3**, which the submodule pins. Tests ran on 16.34.0.
- **Daylight saving on a live site.** The rule is tested on constructed
  times; the test site's zone has no daylight saving.
- **Scale beyond the timings above.** In particular evidence size for an
  organization with tens of thousands of documents (one line each per day).
- **The security-gate workflow** (`.github/workflows/security-gate.yml`):
  it needs the control plane and was not run. The new raw SQL interpolates
  only identifiers taken from constants and binds every value as a
  parameter — the same pattern the migration patches use — but a scanner
  that flags formatted SQL will flag six statements (`storage.measure`,
  `sweep.outstanding_*`, two in `verify`, `compute.stamp_once`).
- **The frontend.** None was written.

Assumed:

- The site's time zone (System Settings) has not changed since the source
  rows were written. True for anything recorded within minutes; an
  assumption for history.
- `File.file_size` is the size of the stored object. It is what the
  platform writes when it registers a file; nothing re-checks it.
- A machine that became Ready and was given a job did work for the customer
  from `ready_at` (which includes uploading their images to it).
- A day's storage level is fairly represented by one measurement.
- Members do not edit their plugin runs to dodge a count (F7). Nothing
  stops them.

Known limits:

- `as_of` cannot reproduce an answer exactly for a record written inside a
  long request: the record carries the time it was written, and it became
  visible when that request committed.
- A record deleted behind the application is not detected (§9).
- An interval that touches a repeated clock hour is shortened; one wholly
  inside it is waived.
- If no storage tick runs during a whole UTC day, that day is lost.

## 13. For the layers that come next

- **Rating.** Price a record, do not re-measure it: `amount = price(meter,
  dimension, period_end_utc) × quantity`, one row of yours per usage record,
  referring to it by name. Records are never split across periods, so a
  rated amount belongs to one period too. Never sum `waived_quantity`.
  For storage, decide what a byte-day costs; the days that were not
  measured cost nothing.
- **Late records.** A record can be written long after its interval
  (catch-up, history). `recorded_at_utc` says when. An invoicing layer has
  to decide what to do with a record that lands in a period it has already
  invoiced; `summary(as_of=…)` gives it the answer it invoiced from.
- **Policy changes.** A new rule is a new version (`compute/v2`) next to
  the old one, effective for records written afterwards. Old records keep
  verifying against the rule that made them.
- **Balance and top-ups.** A top-up has nothing later to find it from: write
  it when it happens, the way `ledger._insert` writes — an idempotency key
  taken from the payment's own identity under a unique index, inside a
  savepoint, a refusal meaning "already recorded". It is not usage and does
  not belong in this table.
- **Refusing work at zero balance** must not read `in_progress` as a charge:
  it is an estimate of something that may yet be waived.
- **Screens.** Show `provisional` and `gaps` wherever a total is shown.

## 14. Where the code is

```
frappe-bench/apps/webodm_core/webodm_core/
├── metering/
│   ├── timeutil.py     UTC, whole seconds, local→UTC, UTC days            (no Frappe)
│   ├── rules.py        compute/v1, analysis_run/v1, storage/v1; hash       (no Frappe)
│   ├── ledger.py       record(), reverse() — the only writer
│   ├── guard.py        isolated(): savepoint, swallow, log
│   ├── storage.py      measure(), sample(), sample_all(), sample_job()
│   ├── sweep.py        outstanding_*(), record_instance(), record_run(), run(), run_job()
│   ├── hooks.py        on_trash capture (imports nothing at load)
│   ├── report.py       summary(), records(), in_progress(), unrecorded(), health()
│   ├── verify.py       verify(), trace()
│   ├── testing.py      fixtures for the tests
│   └── test_*.py
├── api/usage.py        summary, records, record (read-only)
├── webodm_core/doctype/webodm_usage_record/
├── webodm_core/processing/compute.py        stamp_once, mark_dispatched, mark_released; reap fix
├── webodm_core/processing/task_runner.py    one call to mark_dispatched
├── permissions.py, hooks.py

scripts/metering/        test-site.sh, e2e.py, seeded_faults.py — how §11 was produced
docs/metering/README.md  this file
openspec/specs/usage-metering/spec.md
```
