# Browser checks

`model-viewer.e2e.mjs` drives the 3D viewer in headless Chrome against a running
stack: login, model load and framing, every toolbar / mouse / keyboard action,
error + retry, not-found, repeated load/leave cycles, mid-download abort, phone
viewport and container resize. It asserts through `window.__modelViewer.snapshot()`,
which the page exposes in dev builds only, so point it at a Vite dev server.

Playwright is not a project dependency; install it in a scratch directory
(`npm i playwright`) and point `E2E_PLAYWRIGHT_DIR` at that directory.

```sh
# 1. dev server proxied to your stack (adjust target/host); from frontend/
cat > vite.local.config.js <<'CFG'
import { mergeConfig } from 'vite'
import base from './vite.config.js'
const proxy = { target: 'https://your-site.local', changeOrigin: true, secure: false, headers: { Host: 'your-site.local' } }
export default mergeConfig(base, { server: { port: 8081, strictPort: true, proxy: { '/api': proxy, '/private': proxy, '/files': proxy } } })
CFG
npx vite --config vite.local.config.js &

# 2. run the checks
E2E_PASSWORD_FILE=../../../../secrets/admin_password.txt \
E2E_PROJECT='My Project' E2E_TASK=abc123 E2E_TASK_TITLE='My Project - Task 1' \
E2E_SHOTS=/tmp/viewer-shots E2E_CHROME=/usr/bin/google-chrome \
E2E_PLAYWRIGHT_DIR=/path/to/scratch node e2e/model-viewer.e2e.mjs
```

The task must be Completed with a `model`. Software WebGL is used automatically
(`--use-angle=swiftshader`); a 34 MB survey model reaches "ready" in roughly 12 s.
Exit code 0 means every check passed.

## Point cloud viewer (mocked backend)

`point-cloud.e2e.mjs` drives the point cloud mode of the same page without a
stack: Playwright intercepts the Frappe API (session, task, `potree_state`,
`tiles.volume`) and serves a converted octree from disk through the
`/private/files/<task>_potree_*` URLs with byte-range support. It checks the
conversion overlay and polling, Z-up loading, range streaming, that only the
cloud's attributes are offered as colour modes, the elevation filter, size /
budget / background persistence, distance / area / volume measurements (the
volume polygon is asserted to be in the cloud's CRS), the elevation profile
tool (line → request in the cloud CRS → dock with chart, legend, stats and
hover readout; width re-request; remembered settings; backend refusal →
retry; close — the profile endpoint is mocked, from `E2E_PROFILE_JSON` when
given, else synthesised), the no-DSM guard, the Failed → Retry path and a
clean dispose.

```sh
# an octree: PotreeConverter <cloud.laz> -o /tmp/octree (or copy one out of a task)
npx vite --port 8082 --host 127.0.0.1 &
E2E_OCTREE_DIR=/tmp/octree E2E_SHOTS=/tmp/pc-shots E2E_CHROME=/usr/bin/google-chrome \
E2E_PLAYWRIGHT_DIR=/path/to/scratch node e2e/point-cloud.e2e.mjs
```

The dev server is enough (no proxy needed) because every backend call is
mocked. A 2 M point octree reaches "ready" in ~20 s under swiftshader, most
of it the simulated Queued → Running → Ready polling.

## Flight planner (real backend)

`flight-planner.e2e.mjs` drives the flight planner against a running stack
(Frappe with the built SPA): sign-in, navigation from the project map,
coordinate and address search, drawing an area by clicking the map,
estimates and their labelling, terrain loading and terrain following, the
takeoff point, the linked height / resolution inputs, five aircraft with
different capabilities, a capability error, save, a blocked export, an
acknowledged export whose downloaded DJI WPML archive is opened and
checked, reload, dragging / inserting / removing corners, a corridor, the
plan list and duplicate, a custom camera and drone and a QGroundControl
export for it, and a phone viewport. It deletes every plan and profile it
created.

```sh
npm run build            # the stack serves the built SPA
E2E_BASE_URL=https://your-site.local \
E2E_USER=pilot@example.org E2E_PASSWORD_FILE=/path/to/password \
E2E_PROJECT='My Project' E2E_SHOTS=/tmp/planner-shots \
E2E_CHROME=/usr/bin/google-chrome E2E_PLAYWRIGHT_DIR=/path/to/scratch \
node e2e/flight-planner.e2e.mjs
```

The user must belong to an organization and the project must exist. By
default the run uses the configured address and elevation services (a site
with relief near Bandung; override with `E2E_SITE="lat, lng"`), so it needs
outbound access from the stack; `E2E_OFFLINE=1` skips those checks and plans
on flat ground. Around 65 checks, about two minutes.


## Flight simulator (real backend, WebGL)

`flight-sim.e2e.mjs` drives the flight simulator against a running stack
(Frappe with the built SPA). It creates its plans through the API, opens
them from the plan list with *Simulate*, and checks: the dock and its
wording (an estimate, not telemetry, no safety claim); play, pause, stop,
restart, speed and scrubbing; the dashboard for a moment on a flight line;
that the 2D map and the 3D scene draw the same state, paused and while
playing; terrain and imagery in 3D; the five cameras (including orbiting
with the mouse); the 2D / split / 3D layouts; the coverage views; a click
on the elevation profile; the comparison with the plan; an edit while
simulating; that after closing, the stored plan and its export options are
identical to before; a three-battery mission; a fixed-wing; landing in
place without elevation data; a mission of some 48 000 photos; and a phone
viewport. It deletes every plan it created.

It asserts through two read-only hooks the simulator puts on its elements:
`document.querySelector('[data-flight-sim]').simSnapshot()` (playback, the
sampled state, where the map layer has put the aircraft) and
`document.querySelector('[data-sim-3d-host]').simSnapshot()` (camera,
aircraft, terrain, imagery, draw calls).

```sh
npm run build            # the stack serves the built SPA
E2E_BASE_URL=https://your-site.local \
E2E_USER=pilot@example.org E2E_PASSWORD_FILE=/path/to/password \
E2E_PROJECT='My Project' E2E_SHOTS=/tmp/sim-shots \
E2E_CHROME=/usr/bin/google-chrome E2E_PLAYWRIGHT_DIR=/path/to/scratch \
node e2e/flight-sim.e2e.mjs
```

| Variable | Effect |
|---|---|
| `E2E_OFFLINE=1` | Plans without elevation data and does not expect map imagery (65 checks). |
| `E2E_GPU=1` | Uses the machine's GPU (`--enable-gpu --use-angle=gl-egl`) instead of software WebGL, and then also requires at least 10 frames per second at 64× on the large mission (a floor against a collapse, not a target). |
| `E2E_DSM_TASK`, `E2E_DSM_CENTER="lat,lng"` | A Completed task of the project with a DSM (and, if it has one, an orthophoto) and a point inside it: also simulates over the survey's own surface model through the geospatial service (4 more checks). |
| `E2E_SITE="lat, lng"` | Another site (default: hills near Bandung). |

69 checks, about two minutes. Software WebGL is the default so the script
runs anywhere; it composites a page of this size at one or two frames per
second whatever is drawn, so judge smoothness only with `E2E_GPU=1` (on an
Intel HD 520: 57–60 frames per second on an ordinary mission; the script
prints the rate it measured on the large one).
