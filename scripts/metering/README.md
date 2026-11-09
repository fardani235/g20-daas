# Checking usage metering

Three tools for checking the usage-metering layer from a checkout. None of
them is needed to run metering; they exist so that the claims in
`docs/metering/README.md` can be re-checked by someone who was not there.

| | |
|---|---|
| `test-site.sh` | A throwaway Frappe site in Docker (PostgreSQL, two Redis, the Frappe image with this checkout's `webodm_core` mounted). `up`, `migrate`, `test [...]`, `py <script>`, `frappe <bench args>`, `down` |
| `seeded_faults.py` | Breaks one guard at a time on a copy of the app — a filter dropped, a sign flipped, a savepoint removed — and reports whether the tests noticed. 61 faults and two controls; about 25 minutes |
| `e2e.py` | One small customer through the two functions the scheduler calls, then the usage API, `verify`, `trace`, a deletion, a midnight, and two timings |

```bash
scripts/metering/test-site.sh up
scripts/metering/test-site.sh test                                   # all of webodm_core
scripts/metering/test-site.sh test --module webodm_core.metering.test_concurrency
scripts/metering/test-site.sh py scripts/metering/e2e.py
python3 scripts/metering/seeded_faults.py
scripts/metering/test-site.sh down
```

`METER_PREFIX` (default `meter`) names the containers, network and volume;
`FRAPPE_IMAGE` picks the image. Both are read by `test-site.sh` and
`seeded_faults.py`.

Notes:

- The tests that need no site run anywhere:
  `cd frappe-bench/apps/webodm_core && python3 -m unittest webodm_core.metering.test_timeutil webodm_core.metering.test_rules`.
- Several metering tests commit (a second database connection has to see
  their rows) and clean up after themselves, committed. If a run is
  interrupted, leftovers are removed by the next run's `setUp`.
- `seeded_faults.py` matches source text. When the code changes, a fault
  whose pattern no longer applies is reported as `NOT APPLIED`: update the
  pattern. A fault that `SURVIVED` is a gap in the tests, not in the script.
- `api/test_plugin_model_output` inserts a project with a fixed name and
  fails if the whole suite is run twice against one site; `down` and `up`
  between full runs. (It predates metering.)
