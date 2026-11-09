## Why

The platform is going to charge pay-as-you-go against a prepaid balance. That
decision is made. Nothing underneath it exists: the platform knows when a
rented machine came up and went away, how big every stored file is and when
each analysis run started and finished, but it never turns any of that into a
durable record of what an organization consumed. "What did this customer use
last month?" has no answer today.

One part cannot wait. Machine time and (most) analysis runs can be worked out
later from rows that still exist. Storage cannot: nothing remembers how much
an organization was storing on a given day, and deleting a file deletes the
only evidence of the space it occupied. Every day without a measurement is a
day that can never be billed and never be reconstructed.

This change builds the measuring layer only: the record of what was consumed,
per organization, in exact natural units, that cannot be edited afterwards
and is never silently re-derived. Prices, the balance, screens and payment
come later and sit on top of it unchanged.

## What Changes

- **A usage ledger**: new DocType `WebODM Usage Record`. One row per fact,
  append-only, organization-scoped, integers only. Three meters:
  `compute` (seconds a rented machine was useful to a job), `analysis_run`
  (completed plugin runs) and `storage` (bytes held, one measurement per
  organization per UTC day, in byte-days). Each row carries the facts it was
  derived from, the rule that derived it, a unique idempotency key and a
  content hash.
- **Catch-up recording**: a scheduled sweep finds closed machines and
  finished runs that have no ledger row yet and records them. The same code
  fills in history; running it twice, or twice at once, records nothing
  twice.
- **Storage measurement**: an independent scheduled job measures every
  organization once per UTC day. It reads database records only, so it
  works with or without a bucket, with object storage unreachable and with
  no prices configured. It never writes a day other than today.
- **Three facts added to `WebODM Compute Instance`** (`dispatched_at`,
  `released_at`, `release_reason`), written once by the pipeline, read only
  by metering. Without them the platform cannot tell a machine that did work
  from one that never received any, nor when we stopped needing it.
- **Capture before deletion** for analysis runs (and compute instances): a
  run is deleted whenever its plugin is re-run on the same task, so the
  catch-up sweep alone would lose it.
- **Answering the question**: `webodm_core.metering.report.summary` and the
  API `webodm_core.api.usage.{summary,records,record}` return, for an
  organization and a period, totals per meter computed from ledger rows
  (never stored), the rows behind them, what is still in progress
  (provisional, never added to totals), what is finished but not yet
  recorded, and which storage days were not measured.
- **Checking it**: `webodm_core.metering.verify.verify` re-derives every row
  from its own evidence, recomputes hashes and re-adds totals;
  `report.health` shows whether measurement is keeping up.
- **Corrections** are new rows (`entry_type = reversal`), never edits.

## Out of scope (deliberately)

- Prices, rating, currency, tax. No amount is stored anywhere.
- The prepaid balance, top-ups, refusing work at zero balance.
- Screens (the mocked *Billing history* page stays as it is).
- Taking money.
- Metering processing done on a **static** processing node (no record of it
  exists — see design.md, finding F2).
- Storage occupied by plugin packages, thumbnails, the serving cache as
  such, transient relay objects and anything in the bucket that no database
  row describes.
- Reconciling the ledger against the cloud provider's own bill.
- Tightening who may edit or delete the source rows metering reads
  (design.md, finding F7).

## Impact

- Specs: new `usage-metering`; one requirement added to
  `on-demand-processing` (lifecycle facts for metering).
- Code: new package `webodm_core/metering/`, new DocType
  `WebODM Usage Record`, new API module `api/usage.py`; three guarded
  writes in `processing/compute.py` + one call in
  `processing/task_runner.py`; `hooks.py` (two cron entries, two
  `on_trash` doc events, permission maps), `permissions.py`.
- Data: one new table; three nullable columns on
  `tabWebODM Compute Instance`. `bench migrate` creates them. No existing
  row is rewritten.
- Deploy: migrate, then restart workers and the scheduler. Measurement
  starts at the first scheduler tick; there is no switch to turn on and no
  configuration it waits for.
- Behaviour of existing features: unchanged. Metering reads; the three new
  instance columns are never consulted by the pipeline.
