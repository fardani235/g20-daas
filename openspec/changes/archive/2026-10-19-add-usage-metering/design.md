## Context

This is the specification written before any code, as asked. It has four
parts: what the code actually does where that differs from the brief
(**Findings**), what is built and why (**Decisions**), what I am not sure
about (**Open questions**), and what could go wrong (**Risks**). The
normative behaviour is in `specs/usage-metering/spec.md`.

Terms: an *organization* is a customer. A *source* is a row the platform
already keeps (`WebODM Compute Instance`, `WebODM Plugin Run`, `File`). A
*usage record* is a row of the new ledger.

## Findings: where the brief and the code disagree

These were checked against the code, not assumed. None of them changes the
goal; each of them changes how it has to be reached.

### F1. Analysis runs do **not** survive to be counted later

The brief says the number of analysis runs "can be worked out later from
records that still exist". It cannot:

- Starting a plugin on a task **deletes the previous run of that plugin on
  that task** (`api/plugins.py: _delete_previous_runs`, called from
  `run_plugin`). A customer who runs change detection five times leaves one
  row.
- Deleting a task deletes its runs (`WebODMTask.delete_plugin_runs`),
  including comparison runs of other tasks that referenced it.
- Members hold `delete` on `WebODM Plugin Run` through the REST resource.

Consequence: every run that was replaced or deleted before this change is
deployed is already unrecoverable — history for analysis runs will be the
runs that happen to still exist, no more. Going forward, the catch-up sweep
alone would lose every run replaced within its interval, so runs need a
third exception to the catch-up shape next to the two the brief names: they
are recorded **when they are about to be deleted** (D9).

### F2. Not every processing job runs on a rented machine the platform records

A `WebODM Compute Instance` exists only when the on-demand provisioner is
configured. A task processed on a static `WebODM Processing Node` leaves no
machine record at all, and `WebODM Task` has no start or finish time (a
re-run overwrites its state). There is nothing to catch up from.

Consequence: processing on a static node is **not metered** by this change.
That is a statement about a gap, not a judgement that it should be free. If
static-node time must be billable, the task needs a durable per-run record
first; that is a separate change.

### F3. The machine record is missing three facts money depends on

`WebODM Compute Instance` has `requested_at`, `ready_at`, `terminated_at`.
It does not record:

1. **whether the machine ever received a job.** A Ready machine whose
   dispatch failed eight times closes exactly like one that processed for
   two hours (`_fail` → `_finish` → `release_for_task` → `Terminated`).
   "Failed before doing any work" cannot be told from the record.
2. **when we stopped needing it.** `terminated_at` is written when the
   provider *confirms* the destroy. If the destroy fails, the row sits in
   `Terminating` and the sweep retries — for minutes or hours. Billing to
   `terminated_at` would charge the customer for our failure to switch a
   machine off.
3. **why it was released** (job completed, failed, cancelled, deleted;
   lifetime budget).

Consequence: three columns are added to the instance and written once by
the pipeline (D7). Instances closed before the columns existed are handled
by explicit fallbacks, and where the facts are missing the time is
**waived, not guessed** (D8).

### F4. "How big every result is" is true only of `File` rows

- `WebODM Task Asset.file_size` exists only when a bucket is configured
  (rows are written by the object-storage relay); on host-disk deployments
  outputs have no asset rows.
- `WebODM Plugin Run` has no size field at all.
- `WebODM Dataset Image.file_size` is a 32-bit `Int` (adequate for photos).
- The one place every stored blob has a size is Frappe's `File` row
  (`file_size`, a 64-bit column): every large write goes through
  `plugins/files.py` and registers a File attached to its dataset, task or
  run. Cache eviction removes the blob and keeps the row.

Consequence: storage is measured from `File` rows (D10).

### F5. Source timestamps are local wall-clock time, not UTC

Frappe's `now_datetime()` is naive time in the site's time zone (System
Settings), and that is what every source timestamp holds. In a zone with
daylight saving, an hour of wall-clock time occurs twice each autumn, so an
interval computed from two naive values can be wrong by an hour.

Consequence: usage records store UTC, record which time zone was used to
convert, and resolve an ambiguous hour in the customer's favour (D6). The
conversion assumes the site time zone has not been changed since the source
row was written (Open question Q6).

### F6. The cost estimates

`estimated_hourly_cost` and `estimated_cost` on the instance are `Float`,
come from an operator-configured table in the provisioner
(`PROVISIONER_AWS_HOURLY_COSTS`), and the runbook sums them. They are what
the brief says they are: approximations. Nothing in this change reads them.
A usage record carries the *identity* of what ran (`provider/class`) so a
price list can price it later, and never a price.

Separately: the user guide tells customers a machine "is billed by the
minute while it exists". That describes what the provider charges us.
What this change records is seconds between a machine becoming useful and
our no longer needing it — a different, smaller number.

### F7. The sources are writable by the customer

`WebODM Plugin Run` grants members `write` and `delete`, and Frappe does
not enforce `read_only` fields on the server: a member can change a run's
`status` or timestamps, or delete it, through `/api/resource`. `File` rows
can be deleted by any member with write access to the document they are
attached to (the object in the bucket then stays, orphaned).

Consequence: this change shrinks the window (capture on deletion, a sweep
every five minutes) but does not close it; a customer determined to dodge a
per-run charge can still edit a run before it is recorded. Closing it means
tightening permissions on existing DocTypes, which changes existing
behaviour and is called out here rather than done silently (Q7).

## Decisions

### D1. One append-only ledger, three meters

`WebODM Usage Record`. A row is one fact about one organization: this
machine was useful for these seconds; this run completed; on this day this
many bytes were held. Considered: a table per meter (three permission
surfaces, three sets of invariants, and "what did they consume" becomes
three queries); roll-up tables per period (a stored total is exactly the
thing that can stop matching its records — see D12).

### D2. Integers in natural units; no money, no floats, no prices

| meter | unit | a row means |
|---|---|---|
| `compute` | `second` | whole seconds one machine was useful to one job |
| `analysis_run` | `run` | one plugin run reached a terminal state |
| `storage` | `byte-day` | bytes held by one organization on one UTC day |

Quantities are 64-bit integers. There is no amount, currency or rate column
and nothing computes one. This is what makes the layer useful with no
prices: every answer is in seconds, runs and byte-days. It is also what
keeps money exact later: a rating layer multiplies an integer by a decimal
price once, per row, and refers to the row.

### D3. `quantity` is what counts; `waived_quantity` is what was measured and deliberately not counted

A machine that never became Ready, a run that failed, a machine we could
not time: each still gets a row, with `quantity = 0`, the measured amount
in `waived_quantity` and a `waived_reason`. Considered: a `billable` flag
beside a single quantity. Rejected because the obvious query — sum the
quantity — would then over-count unless every caller remembered the flag.
With two columns the obvious query is the safe one, and what the platform
absorbed is still on record.

### D4. Recording twice is refused by the database, not avoided by care

Every row has an `idempotency_key` derived from the identity of the fact
(`compute:<instance>`, `analysis_run:<run>`, `storage:<org>:<day>`,
`reversal:<record>`) under a unique index. The recorder looks, inserts
inside a savepoint, and treats the database's refusal of a duplicate key
as "already recorded". Two sweeps running at the same moment both try; the
database lets one in. The first committed row stands and is never
compared, merged or replaced.

Frappe runs PostgreSQL at REPEATABLE READ (see *Found while building*,
B3), so the recorder that loses a race usually cannot *see* the winner's
row afterwards. It does not need to: an insert that collides with an
uncommitted row waits for it and fails only once that row is committed,
so the refusal itself is the proof. The loser reports "recorded" without a
name.

Tried and withdrawn: deriving the record's name from the key (so the loser
would know the winner's name, and the primary key would refuse a duplicate
too). The review pointed out what it costs — a storage record's name
becomes computable from an organization's name and a date, and Frappe's
REST resource answers 403 for a document that exists and 404 for one that
does not, so anyone could test which organizations are customers. Names
are random.

### D5. A record belongs to the period in which it ended

A period is `(start, end]`. A record is in it when
`start < period_end_utc <= end`. Records are never split or pro-rated
across a boundary, so every record is in exactly one period for any choice
of boundaries, and a period's total is a plain sum. A machine that ran
23:00–01:00 across a month end is counted in the month it ended, with both
instants on the row. Considered: clipping records to the period (a machine
then appears in two statements as two partial quantities, and rounding a
price over two parts need not equal rounding it over the whole).

### D6. UTC, whole seconds, rounded toward the customer

Usage records hold UTC. A source's local time is converted with the site
time zone, which is written into the record's evidence. An interval's start
is rounded **up** to the second and its end **down**, so a recorded
duration is never longer than the real one. A local time that occurred
twice (daylight-saving fold) is read as the later instant for a start and
the earlier for an end, for the same reason, and the record says it did so.

### D7. Three lifecycle facts on the compute instance, written once

`dispatched_at` (a job was accepted by the node on this machine),
`released_at` (the first moment the platform asked for the machine to be
destroyed) and `release_reason` (`task:Completed`, `task:Failed`,
`task:Cancelled`, `task-deleted`, `task-reassigned`, `lifetime-exceeded`,
`provisioning-failed`, …). Each is written by its own guarded statement
(savepoint, exception swallowed and logged) so that a failure to write a
metering fact cannot fail a dispatch or a teardown; none is ever read by
the pipeline. This is "capture at the instant" for three facts, but they
are captured **on the source row**, which the catch-up sweep then reads —
the ledger itself is still only ever written by catching up. If a stamp is
missed the result is time waived, never time over-counted.

Considered: deriving "did work" from the task (`node_task_id` set while it
still points at the instance). It is correct while it lasts, but a restart
or delete erases it, so it is kept only as a labelled fallback for
instances that predate the column.

### D8. Compute: what counts

One record per instance, written once the instance is *closed*
(`released_at` or `terminated_at` set). Rule `compute/v1`:

- interval = `ready_at` → `released_at`;
- **counts** when the machine became Ready, a job was dispatched to it,
  the platform neither failed nor killed it, and both ends of the interval
  are known and in order;
- otherwise the measured time is **waived** with a reason:
  `never_ready`, `no_work_dispatched`, `platform_failure` (the instance is
  `Failed`, or its `release_reason` is one the platform gives when it
  pulls the plug itself — today that means the lifetime budget),
  `end_time_not_recorded` (no `released_at` and the destroy was retried,
  so `terminated_at` overstates), `invalid_interval`,
  `deleted_before_release`.

`release_reason`, not `status`, decides "the platform killed it": a failed
destroy overwrites `Failed` with `Terminating`, so the status does not
survive the very situation it is needed for. An instance still in
`Terminating` is recorded straight away — its interval ended at
`released_at`, and the retries are not the customer's time.

The start is `ready_at`, not `dispatched_at`: between the two the machine
is receiving the customer's images, which is work for that customer.

A job that **failed after work started** (the engine rejected the imagery
part-way; the node became unreachable) or was **cancelled** counts. That is
the letter of the brief ("failed *before* doing any work"), not obviously
its spirit ("do not charge for something that produced nothing"). It is
the most important open question (Q1). Either answer is supported without
touching a row: `release_reason` is on the record.

### D9. Analysis runs: every run ends in exactly one record

Rule `analysis_run/v1`: a `Completed` run counts as 1; `Failed`,
`Cancelled` and deleted-before-finishing are waived (1, with the reason).
The sweep records terminal runs; an `on_trash` hook records a run that is
being deleted first, whatever state it is in, with the same idempotency
key — so replacement, task deletion and manual deletion cannot lose a run,
and cannot double it either. The hook follows the same rule as D7: a
savepoint, and nothing it does can stop the delete.

### D10. Storage: one measurement per organization per UTC day, from `File` rows

- **What is measured**: the sizes of the `File` rows attached to the
  organization's datasets, tasks and plugin runs — imagery, ground control
  files, outputs, the viewer octree, analysis outputs. Each distinct file
  URL of a document is counted once (Frappe can create a second `File` row
  for the same attachment). These are *logical* bytes: one object is one
  size whether it is in the bucket, in the cache or both.
- **What is not**: plugin packages, thumbnails, transient relay objects,
  anything in the bucket with no row. Stated on every measurement.
- **Why rows and not a bucket listing**: it works without a bucket, it
  works while the bucket is unreachable (so nothing can hold it up), it is
  one cheap query, and it is traceable to rows. The cost is that it
  measures what the platform *believes* it stores; see Q5.
- **Grain**: the UTC day. The record for day D says "the level measured at
  `sampled_at` stands for the whole of D" and its quantity is that level in
  byte-days. One measurement per organization per day; the job runs every
  ten minutes and takes the day's measurement if it is missing. It never
  writes any day but the current one. Zero is a measurement: an
  organization holding nothing gets a row saying 0.
- **Evidence**: one line per document (dataset / task / run: file count and
  bytes, with its title) inside the record, because once the files are
  deleted that line is the only evidence left. The quantity must equal the
  sum of the lines; `verify` checks it.
- Considered: hourly measurements (24× the rows for precision the first
  price list is unlikely to need; the grain is stored on the row —
  `period_start_utc`/`period_end_utc` — so it can be tightened later
  without redoing anything); per-file evidence (thousands of lines per
  dataset per day).

### D11. Provisional never enters the ledger

Nothing is counted before its interval has ended. Work still running — a
Ready machine, a Queued or Running plugin run — has no usage record at
all; `summary` reports it in a separate `in_progress` list, each item
marked `provisional`, computed at request time from the live source and
**not included in any total**. Today's storage measurement is the one
record that exists before its interval is over: it is listed in
`in_progress` too (marked fixed, with its bytes) and enters the total at
midnight UTC. The answer as a whole says `provisional: true` and why: the
period has not ended; something is still running; finished sources are
waiting for the sweep (counted); storage days of the period are still to
come. A caller cannot mistake a running number for a settled one because
the running number is not in the total at all.

*Provisional* means the totals can still change. A storage day that ended
unmeasured is the opposite — it will never change — so it does not make an
answer provisional; it makes it incomplete, and it is listed under `gaps`.

### D12. Totals are computed from records every time; nothing is stored

There is no stored total anywhere to disagree with its records. `summary`
adds up rows; `records` returns the rows; `verify` re-derives each row's
quantity from the evidence stored on that row, recomputes its hash and
re-adds the totals a second way. `summary(as_of=…)` restricts to rows
recorded up to an instant, which reproduces the answer as it stood then —
rows are never changed, so this is exact.

### D13. Immutable in the application, tamper-evident in the database

The controller refuses every update and delete; inserts are accepted only
from the recorder; no role has write or delete; there is no mutating API.
A mistake is corrected by a **reversal** row (negative quantities,
`reverses` the original, unique per original, with a note and the user who
made it) — never by editing. Each row stores a SHA-256 over its content;
`verify` reports any row that no longer matches.

Not done: database triggers that reject `UPDATE`/`DELETE`. They would make
the guarantee hold against raw SQL, but they break `rename_doc` on an
organization, are engine-specific, and complicate restore and test
tear-down. So the honest statement is: edits through the application are
prevented; edits behind its back are **detected** by `verify`, not
prevented (Q8).

### D14. A record carries its own evidence

Sources get deleted (F1), so a record cannot depend on its source to be
explained. Each row stores, as canonical JSON, every input its rule used —
the source's status and timestamps exactly as read, the time zone, the
rule name and version — plus what was and was not checked. The rules are
pure functions of that evidence, which is what lets `verify` rebuild a
quantity years later with the source long gone. `source_doctype` /
`source_name` are plain text, not links, so a record never blocks the
deletion of the instance or run it came from.

`organization` is the exception and is a real link: an organization that
has usage records cannot be deleted through the document API. That is
deliberate — the records must not be orphaned — and it starts the day the
first storage measurement is taken. Nothing in the application deletes
organizations today.

### D15. Organization scoping

`WebODM Usage Record` has an `organization` and is in both permission hook
maps (the existing coverage test fails otherwise). Members read their own
organization's records; platform admins read all; nobody writes. The API
resolves the organization from the session (`tenancy.require_org`) and
treats an `organization` argument from anyone but a platform admin as an
error, with the same answer whether or not that organization exists.
A record contains only the organization's own identifiers: a run's `task`
and `plugin` are member-writable links, so the project and the plugin's
details are copied only when they belong to (or are shared with) the
run's organization. See Q3 for "members or owners only".

### D16. Two independent scheduled jobs, on by default

`metering.storage.sample_job` (every 10 minutes) and
`metering.sweep.run_job` (every 5 minutes) are separate cron entries and
share nothing but the recorder. Frappe runs each as its own job on the
`default` queue, de-duplicated per job. Storage measurement needs no configuration,
no bucket, no prices and no provisioner, and a failure in the sweep cannot
delay it. Neither job has an on/off setting: measurement starts with the
first scheduler tick after `migrate`.

### D17. Top-ups

Not built — a top-up is a balance fact, and the balance is out of scope.
It is also not usage, so it does not belong in the usage ledger. What this
change leaves for it is the pattern, in `metering.ledger._insert`: a
one-time fact with nothing later to find it from is written when it
happens, under an idempotency key taken from the event's own identity (a
payment reference) with a unique index, inside a savepoint, a refusal
meaning "already recorded" — so a retried request records once — in a
table whose rows are never edited and are corrected by reversal.

## Found while building

Three things the code turned out to do that the specification above did
not know when it was written. Each was found by a test run against a real
database rather than a mock.

**B1. The compute safety sweep never ran on PostgreSQL.**
`compute.reap()` filtered on `{"terminated_at": ["is", "not set"]}`. For a
Datetime column Frappe 16.34 renders that as
`terminated_at IS NULL OR terminated_at = ''`, PostgreSQL rejects the
comparison, the query raises, and `reap_job` catches and logs it — every
minute. So destroy retries for `Terminating` instances, the lifetime
budget, the never-came-up timeout and orphan reaping were not happening;
the existing tests mock `frappe.get_all` and could not see it. Fixed in
this change (`None` instead of `["is", "not set"]`) because metering's
lifecycle tests cannot pass without a working sweep, and covered by
`processing/test_compute_database.py`. It is unrelated to metering and is
called out separately so it can be reviewed on its own. Verified on the
16.34.0 image; not checked on the 16.26.3 the submodule pins.

**B2. A database `SUM` is not exact through Frappe.** `SUM` over a 64-bit
column is a `numeric`, and Frappe's PostgreSQL driver returns `numeric` as
a float — exact only to 2^53. Totals that leave the database do so as text
(`CONCAT(SUM(..), '')`) and are parsed as integers. Nine petabytes is not
a realistic total, but "exact unless large" is not exact.

**B3. REPEATABLE READ.** See D4. Also why the sweep commits after every
record: a record is durable at once, and a deletion hook inside a user's
request never waits on a sweep for longer than one insert.

**B4. Frappe rewrites text on save.** Its HTML sanitizer runs after the
controller's `before_insert` and drops anything that parses as a tag —
`see ticket <OPS-1423>` becomes `see ticket `. A ledger must store what
was hashed, so every text field of the record opts out
(`ignore_xss_filter`) and the controller re-checks the hash in
`after_insert`, on the document exactly as written; a mismatch undoes the
insert.

## What an independent review found

After the code and tests were written, a second reviewer was given the
specification and the code — not the author's conclusions — and asked for
defects with a trigger and a wrong outcome. It found thirteen. All were
real; they are listed because a record of what was wrong is part of the
record.

| # | Finding | Outcome |
|---|---|---|
| R1 | Every destroy attempt stamped `released_at`, so an instance already waiting for a retry when the columns arrived (plausible, given B1) would be counted up to the *retry* — an over-count | Fixed: only the first attempt stamps; such rows keep an empty `released_at` and are waived. Tests added |
| R2 | A reversal note with `<…>` was rewritten by Frappe after hashing; `verify` then reported an untouched row | Fixed (B4). Tests added |
| R3 | `as_of` in the future reported an open period as final and days to come as permanent gaps | Fixed: refused |
| R4 | Today's storage measurement was in the total while its day was still running, against D11 | Fixed: counted from midnight UTC, listed in `in_progress` until then |
| R5 | `as_of` cannot reproduce an answer exactly when a record was written inside a longer transaction (the deletion hook) | Not fixable without a commit timestamp; stated in the spec, the docstring and the documentation |
| R6 | Names derived from keys were computable, and the REST resource distinguishes "exists" from "does not" | Fixed: names are random again (D4) |
| R7 | The spec called unmeasured past days "provisional"; the code calls them gaps | Spec corrected: a gap is permanent, not provisional (D11) |
| R8 | A period from year 1 to year 9999 was accepted (millions of days, or an overflow) | Fixed: periods over 400 days are refused |
| R9 | `trace` and the record API crashed on a row with damaged evidence — the row they exist to explain | Fixed: shown with `evidence_unreadable` |
| R10 | The hash ignored a sub-second change to an instant | Fixed |
| R11 | A lower-case `z` suffix was rejected | Fixed |
| R12 | Usage records block deletion of their organization, against the wording of D14 | Intended; D14 now says so |
| R13 | A run pointed at another organization's task copied that organization's project title into the caller's record | Fixed: lookups are confined to the record's organization. Test added |

R1 is the one that matters most: it was an over-charge, on exactly the
rows B1 makes likely, and none of the author's tests would have caught it.

## Open questions (where I am unsure)

**Q1. Should a job that failed *after* work started count?** Built as: yes
(D8). The brief can be read either way. Changing the default for *future*
records is a one-line rule change (`compute/v2`); already-written records
keep `release_reason`, so a rating layer can waive them without any
rewrite. I would like this decided before prices exist.

**Q2. Daily storage measurements.** A customer can hold a terabyte for 23
hours between two measurements and pay for none of it, or pay for a whole
day of something uploaded a minute before the measurement. Daily is what
the brief asks for ("on a given day"). If this matters, shorten the grain;
nothing else changes.

**Q3. Who in an organization may read usage?** Built as: every member,
because every member can already see the tasks, datasets and compute
instances the records describe. Restricting to owners later is one
condition in one function; widening later would be a disclosure.

**Q4. History of machines closed before this change.** Recorded
automatically by the first sweeps, using fallbacks: "did work" from the
task if it still points at the instance; end time from `terminated_at`
only when the destroy succeeded first time. Where neither holds the time
is waived. So history may be under-counted; it will not be over-counted.
I have no production data to tell how many rows this affects. Because of
B1 there may be instances that have sat in `Terminating` for weeks: the
first working sweep will destroy them, and their time is waived (R1).

**Q5. Records versus the bucket.** Storage is measured from `File` rows.
It will differ from the provider's bill wherever the two differ: orphaned
objects (a failed delete, a `File` row removed through the REST API),
incomplete multipart uploads, bucket versioning, objects written but never
registered. No reconciliation against a bucket listing is built. Every
measurement says so (`not_checked`).

**Q6. Time zone changes.** Converting a source's local time assumes System
Settings' time zone was the same when the source was written. True for
records made within minutes; an assumption for backfilled history.

**Q7. Customer-writable sources** (F7). Not fixed here.

**Q8. Database-level immutability** (D13). Not done.

**Q9. Size of storage evidence.** One line per document per day: an
organization with 1,000 datasets, tasks and runs writes roughly 100 kB a
day. Fine for now; an organization with tens of thousands would need the
lines moved out or de-duplicated between unchanged days.

**Q10. Databases.** Developed and tested on PostgreSQL 16 (what compose
and the chart deploy). The SQL is written to be portable but MariaDB was
not run.

**Q11. `as_of` and commit lag** (R5). A record carries the instant it was
written, not the instant its transaction committed. For the sweep and the
storage job that is the same moment to within milliseconds. For a run
recorded by the deletion hook inside a long request it can be seconds
apart, and an answer taken in between will not include a record that an
`as_of` replay of that instant later does.

## Risks

- **A metering write breaks a job.** Mitigated by D7/D9: every write on a
  pipeline path is its own savepoint with the exception swallowed; the
  sweeps are separate jobs that only read sources. Tested by making the
  recorder raise, and by making it fail *inside the database*, during a
  dispatch, a teardown and a delete.
- **Silent under-recording** (a stamp never written, a sweep not running).
  `report.health` and `summary.unrecorded` exist to make it visible; it
  errs toward the customer.
- **The scheduler is off.** Then nothing is measured and storage days are
  lost for good. `summary` names the missing days; the runbook says how to
  check.
- **Evidence growth** (Q9).
