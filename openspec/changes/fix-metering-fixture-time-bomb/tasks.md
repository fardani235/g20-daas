# Tasks

## 1. Fix the test's time dependence

- [x] 1.1 In `metering/test_no_harm.py`, pass a recent `ready_at` (e.g.
      `add_to_date(now_datetime(), hours=-1)` from `frappe.utils`) to the `make_instance(...)` call in
      `test_a_stamp_that_fails_does_not_keep_a_machine_alive`, leaving `released_at=None`,
      `terminated_at=None` and `release_reason=None` so the scenario is unchanged. Verify: that one
      test passes on a fresh site with
      `frappe run-tests --app webodm_core --module webodm_core.metering.test_no_harm`.
- [x] 1.2 Confirm the test still fails for the original reason when the override is removed.
      Verify: the reverted run reproduces `AssertionError: <measured> not less than or equal to 21600`
      with a measured value close to the elapsed time since 2026-10-19 — proving the failure came from
      the fixture's fixed date and not from the assertion.
- [x] 1.3 Comment the call site in one line: `ready_at` is the only end of the interval this test
      measures, so it must stay near the present, whereas the fixture default is a fixed 2026-10-19.
      Verify: the comment names the failure mode, so a later tidy-up back to the fixture default reads
      as obviously wrong.

## 2. Close the same-shape neighbours

- [x] 2.1 Check `metering/test_report.py:290-292` and `metering/test_sweep.py:418/430`, which null the
      same end stamps: confirm neither compares a measured duration to a constant, so neither can rot
      the same way. Verify: no such assertion exists in those cases; record the finding in the commit
      message so the check is not repeated blindly next time.
- [x] 2.2 If either case does compare against a constant, pin its `ready_at` the same way as 1.1.
      Verify: the affected module passes (`test_report`, `test_sweep`).
      **Not needed:** neither case does (finding in 2.1); `test_sweep.py:418` and `test_report.py:288`
      compute a duration only under the frozen clock, and `TestLifecycleStamps` asserts presence, not an
      interval. `test_timeutil.py:58` is the only other duration assertion and has both ends explicit.

## 3. Verify where it actually broke

- [x] 3.1 Run the whole `webodm_core` suite on a fresh site and judge **both** test categories, not the
      log tail. Verify: each category prints `Ran <n> tests` → `OK` and the process exits 0; the
      expected totals are 811 (unspecified) + 292 (old-frappe-test-class), since the failed run reached
      only 419 + 292 because of `--failfast`.

## Workflow follow-up

- Push the fix and confirm the `webodm_core` CI job is green and its `Push image` step actually ran.
- Confirm a new `16.34.0-<sha>` tag exists in GHCR, then refresh the compose pins with
  `scripts/pin-images.sh` (they still point at October digests).
- Archive the change after review.
