# Proposal

## Why

`webodm_core` has been red since 2026-10-19 10:30Z, so no `webodm-frappe` image has been published
since 2026-10-17 (commit `be788c8`) — the image that carries the usage-metering work itself. Every
push to `master` builds the image, fails one metering test, and skips the push step.

The failing test is
`webodm_core.metering.test_no_harm.TestTeardown::test_a_stamp_that_fails_does_not_keep_a_machine_alive`:

```
test_no_harm.py:156
self.assertLessEqual(rec["quantity"], 3600 + 5 * 3600)
AssertionError: 1881654 not less than or equal to 21600
```

The product code is correct. `compute/v1` measured the interval it was given and said so in the
record's own `caveats` ("the instance predates released_at; the interval ends at terminated_at").
The test's premise silently expired: `metering/testing.py:make_instance` pins every fixture timestamp
to **2026-10-19**, while this test nulls `released_at`/`terminated_at` so `destroy_instance` stamps
`terminated_at = now`. The measured interval is therefore `2026-10-19T04:30Z → now`, which grows by
one second per second, and the assertion is against a constant. It last passed at 07:14Z on
2026-10-19 (interval ≈ 2 h 44 m) and has been red since `ready_at + 6 h`, 10:30Z the same day.
Nothing noticed for 21 days because no push to `master` happened in between.

## What Changes

- **Pin `ready_at` near the present in the one failing test**, so it measures what it is named for:
  a machine that was Ready briefly, whose release stamp was lost, is not billed for longer than it
  was alive. The assertion and its constant stay as they are.
- **Keep `make_instance`'s fixed defaults.** Its 2026-10-19 timestamps are load-bearing for 71
  assertion sites across seven metering test files (`test_timeutil` 24, `test_report` 15,
  `test_rules` 10, `test_sweep` 10, `test_ledger` 5, `test_storage` 4, `test_verify` 3), which pin
  specific instants deliberately for period boundaries, `as_of` handling and UTC/time-zone edges.
  Making the fixture relative to `now` would rewrite all of them to fix one.
- **Check the two latent same-shape fixtures** (`test_report.py:290-292`, `test_sweep.py:418/430`).
  Both null the same stamps; neither asserts a duration today, so they cannot rot yet — confirm that
  and leave them alone.
- **No production code changes, no API changes, no behaviour changes.** `compute/v1` and every spec
  requirement it implements stay exactly as they are.

**Not in scope:** the systemic question of whether `make_instance` should default to relative time;
any hardening of the metering rules; the GHCR publishing incident (separate, already resolved by the
attempt-2 re-run); the `security-gate` pipeline authorisation for `development`.

## Capabilities

### New Capabilities

None. This change alters a test fixture, not system behaviour.

### Modified Capabilities

None. `usage-metering` already requires what the test checks — machine time counts only when both
ends are known and in order, otherwise it is waived with a reason, and recording never harms the
pipeline. No requirement text changes.

`skip_specs: true` is set in `.openspec.yaml` for this reason.

## Impact

- `frappe-bench/apps/webodm_core/webodm_core/metering/test_no_harm.py` — one `make_instance(...)`
  call site in `test_a_stamp_that_fails_does_not_keep_a_machine_alive`.
- `frappe-bench/apps/webodm_core/webodm_core/metering/testing.py` — read only; the helper already
  accepts field overrides, so no change is expected.
- **CI**: `.github/workflows/build-and-push.yml`'s `webodm_core` job should go green, which unblocks
  the `webodm-frappe` push. `docker-compose.yml`'s image pins are then stale (they still point at
  October digests) and need `scripts/pin-images.sh`.
- **Residual risk:** the failing run stopped at 419 of the 811 tests in its first category because of
  `--failfast`, so 392 tests have not executed since 2026-10-19. The first green run may surface
  further failures unrelated to this change.
