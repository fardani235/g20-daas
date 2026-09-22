# WebODM Plugin Runner

Sandbox service that executes **user analysis plugins** (zip packages uploaded
by an organization) on behalf of Frappe. Part of the WebODM Frappe rework; the
sibling of `services/geospatial`, which hosts the built-in (system) operations.

The service is **stateless**: Frappe stages each run in the shared sandbox
volume (package copy, input copies, empty output slot), calls `POST /run`, and
collects the output afterwards. The run record and all permissions live in
Frappe. See `docs/plugins/user-plugin-guide.md` for the plugin contract.

## API

| Method | Endpoint | Notes |
|---|---|---|
| GET | `/health` | Liveness; reports `sandbox_dir` and `max_concurrent_runs` |
| POST | `/run` | Execute one staged run synchronously |

`POST /run` body:

```json
{
  "package_path": "/sandbox/runs/<id>/package.zip",
  "inputs": {"raster": "/sandbox/runs/<id>/inputs/raster.tif"},
  "params": {"threshold": 120},
  "output_path": "/sandbox/runs/<id>/output.tif",
  "output_kind": "raster",
  "run_dir": "/sandbox/runs/<id>",
  "timeout_seconds": 300
}
```

Every path must be absolute and inside `SANDBOX_DIR` (400 otherwise). Responses:
`200 {output_path, metadata, log}`; `422` when the plugin is malformed, exits
non-zero, times out or writes no/invalid output (`detail` carries the stderr
tail); `400` for a bad request; `500` for a runner fault.

## Isolation

Layered, from the outside in:

1. **Container** (`docker-compose.yml`): unprivileged uid, `read_only` rootfs,
   `cap_drop: ALL`, `no-new-privileges`, `pids_limit`, CPU/memory limits, and
   only the `sandbox` network — an *internal* network shared with the Frappe
   worker alone. The plugin has no route to the internet, the database, Redis,
   NodeODM or the geospatial service, and `frappe_sites` is **not** mounted, so
   other organizations' files are unreachable.
2. **Process** (`app/sandbox.py`): each plugin runs as a child process with a
   scrubbed environment (no service config), `RLIMIT_AS` / `RLIMIT_CPU` /
   `RLIMIT_FSIZE` / `RLIMIT_NPROC`, its own process group and a wall-clock
   kill. Package extraction refuses path traversal, symlinks and zip bombs.
3. **Data**: inputs are *copies* made by Frappe into a per-run directory with an
   unguessable name; the runner deletes everything it created (extracted
   package, scratch, logs) before returning, and Frappe removes the run
   directory after collecting the output.

What this does **not** do: it is not a per-run container. Concurrent plugins of
different organizations share the runner process's uid, which is why
`PLUGIN_MAX_CONCURRENT_RUNS` defaults to `1`.

## Configuration

| Env | Default | Purpose |
|---|---|---|
| `SANDBOX_DIR` | `/sandbox` | Only tree the runner will read or write |
| `PLUGIN_MAX_CONCURRENT_RUNS` | `1` | In-process run slots |
| `PLUGIN_DEFAULT_TIMEOUT` / `PLUGIN_MAX_TIMEOUT` | `300` / `3600` | Seconds |
| `PLUGIN_MEMORY_MB` | `2048` | `RLIMIT_AS` per plugin process |
| `PLUGIN_MAX_OUTPUT_MB` | `4096` | `RLIMIT_FSIZE` |
| `PLUGIN_MAX_PROCESSES` | `128` | `RLIMIT_NPROC` |
| `PLUGIN_MAX_PACKAGE_MB` / `PLUGIN_MAX_EXTRACTED_MB` | `256` / `1024` | Package limits |
| `PLUGIN_PYTHON` | the service interpreter | Interpreter plugins run under |

## Development

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                       # runner tests (also run the example plugin)
SANDBOX_DIR=/tmp/sandbox uvicorn app.main:app --port 5001

# Run a plugin locally, no HTTP, same code path as the sandbox:
python -m app.cli ../../docs/plugins/examples/elevation-mask \
    --input raster=/path/to/dsm.tif --param threshold=120 --output /tmp/out.tif
```

Frappe reads `plugin_runner_url` (default `http://127.0.0.1:5001`) and
`plugin_sandbox_dir` (default `sites/<site>/private/plugin_sandbox`) from site
config; in Docker both are set by `infra/frappe/configure_site.py` from
`PLUGIN_RUNNER_URL` / `PLUGIN_SANDBOX_DIR`.
