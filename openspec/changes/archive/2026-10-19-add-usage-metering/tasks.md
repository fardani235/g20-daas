# Tasks

Storage measurement is first on purpose: it is the only part whose delay
loses data for good, and nothing below it is allowed to hold it up.

## 1. Ledger

- [x] 1.1 `metering/timeutil.py` (UTC, whole seconds, local → UTC with the fold rule, day slots, period parsing); `test_timeutil.py`
- [x] 1.2 `metering/rules.py` (pure: `compute/v1`, `analysis_run/v1`, `storage/v1`, canonical JSON, record hash); `test_rules.py`
- [x] 1.3 DocType `WebODM Usage Record` + controller (insert only from the recorder, no update, no delete); hooks + permissions
- [x] 1.4 `metering/ledger.py` (`record` with idempotency key + savepoint, `reverse`), `metering/guard.py`; `test_ledger.py`, `test_guard.py`

## 2. Storage (must not wait for anything else)

- [x] 2.1 `metering/storage.py` (`measure`, `sample`, `sample_all`, `sample_job`), cron entry; `test_storage.py`

## 3. Machine time and analysis runs

- [x] 3.1 `WebODM Compute Instance`: `dispatched_at`, `released_at`, `release_reason`; guarded stamps in `compute.py` / `task_runner.py`; existing compute tests still pass
- [x] 3.2 `metering/sweep.py` (outstanding instances and runs, `record_instance`, `record_run`, `run`, `run_job`), cron entry; `test_sweep.py`
- [x] 3.3 Capture before deletion (`on_trash` doc events for runs and instances)
- [x] 3.4 `test_no_harm.py`: recorder failures (Python and database) during dispatch, teardown and delete
- [x] 3.5 `test_pipeline.py`: the real `task_runner` / `compute` functions on real rows, every way a job can end, through to the record
- [x] 3.6 Compute safety sweep: PostgreSQL-valid filter; `processing/test_compute_database.py`

## 4. Answering and checking

- [x] 4.1 `metering/report.py` (`summary`, `records`, `in_progress`, `unrecorded`, `health`); `test_report.py`
- [x] 4.2 `metering/verify.py`; `test_verify.py`
- [x] 4.3 `api/usage.py` (`summary`, `records`, `record`); `api/test_usage.py` incl. isolation through every route
- [x] 4.4 `test_concurrency.py`: two real connections recording the same facts

## 5. Verification

- [x] 5.1 Full `webodm_core` suite on a fresh site (baseline before: 844 OK)
- [x] 5.2 Seeded faults: remove each guard, confirm a test fails
- [x] 5.3 End to end on the test site through the real scheduler entry points
- [x] 5.4 Independent review of code against the specification; all thirteen findings resolved or recorded (design.md)

## 6. Docs

- [x] 6.1 `docs/metering/README.md` (design record, how to check it yourself, what was not verified), `openspec/specs/usage-metering/spec.md`, `on-demand-processing` requirement, on-demand runbook/architecture, SPEC.md, TODO.md, README.md, AGENTS.md
