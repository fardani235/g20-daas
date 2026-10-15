# Flight planning: architecture

For developers and reviewers: how the module is put together, the models it
uses, the decisions behind it, how to extend it and how it was tested.

- [Shape of the module](#shape-of-the-module)
- [From a drawing to a mission](#from-a-drawing-to-a-mission)
- [Models](#models)
- [Findings: severity and basis](#findings-severity-and-basis)
- [Export](#export)
- [Extending it](#extending-it)
- [Decisions](#decisions)
- [API](#api)
- [Testing](#testing)
- [Limits and what is not done](#limits-and-what-is-not-done)

## Shape of the module

```
browser                         Frappe (webodm_core)                    outside
┌──────────────────────┐  POST  ┌───────────────────────────────┐
│ FlightPlanner.vue    │ ─────► │ api/flightplan.py             │ ──► Nominatim (address search)
│  draws the shape,    │        │  permissions, documents,      │ ──► elevation service (global DEM)
│  shows the mission   │ ◄───── │  custom profiles, caching,    │ ──► geospatial /raster/sample
│ lib/flightplan.js    │ mission│  config, downloads            │      (a task's DSM / DTM)
└──────────────────────┘        └──────────────┬────────────────┘
                                               │ plain data in, plain data out
                                ┌──────────────▼────────────────┐
                                │ flightplan/  (no Frappe)      │
                                │  profiles + catalog/*.json    │  equipment as data
                                │  photogrammetry, survey       │  GSD, footprint, line layout
                                │  terrain, elevation           │  elevation grid, providers
                                │  mission.plan_mission         │  altitudes, estimates
                                │  checks, issues, airspace     │  findings
                                │  exporters/                   │  one module per file format
                                └───────────────────────────────┘
```

Three properties were designed in:

1. **The planner is one pure function.** `plan_mission(definition, drone,
   camera, terrain, zones) -> mission` has no I/O, no Frappe and no
   third-party dependency. The preview while drawing, the save and the
   export all go through it, so what the user sees is what is stored and
   what is exported.
2. **Equipment is data.** Aircraft and cameras are JSON
   (`flightplan/catalog/`) or an organization's documents; both pass through
   the same `normalize_*` functions and reach the planner as the same
   `Drone` / `Camera` objects. Nothing in the planner, the checks or the
   exporters names a manufacturer or a model.
3. **Formats are plugins.** An exporter is a class that renders one route;
   which aircraft may use it, and how well supported that is, is a line in
   the drone's profile.

The core processing workflow is untouched. The changes outside the new
files are: three hook entries per new DocType, a three-line cascade in
`WebODMProject.on_trash`, one client function in `plugins/geospatial.py`,
one additive endpoint in the geospatial service, the navigation entries and
one button on the project map, and optional environment variables.

### Files

| Path | Role |
|---|---|
| `webodm_core/flightplan/geo.py`, `geometry.py` | Local transverse Mercator frame; planar geometry (scan-line clipping, sweep direction, polyline offset, profile simplification). |
| `…/profiles.py`, `catalog/{drones,cameras,regulations}.json` | `Drone` / `Camera`, validation, the built-in catalog, flight app families for custom drones. |
| `…/photogrammetry.py` | GSD, footprint, line and photo spacing, overlap over rising ground, motion blur. |
| `…/params.py` | The one table of flight parameters: names, ranges, defaults, help. |
| `…/survey.py` | Line layout: `area_layout`, `corridor_layout` → `Leg`s in flight order. |
| `…/terrain.py`, `elevation.py`, `httpjson.py` | `TerrainModel` (a cached grid), grid sizing, elevation providers. |
| `…/mission.py` | `plan_mission`: layout + altitudes + estimates + flights + terrain analysis. |
| `…/checks.py`, `issues.py`, `airspace.py` | Findings and the "what was checked" table. |
| `…/exporters/` | `base.py` (routes, splitting), `dji_wpml.py`, `mavlink.py`, `litchi_csv.py`, `kml.py`, `geojson.py`, `__init__.py` (registry and the export gate). |
| `…/geocode.py` | Coordinate parsing; Nominatim-compatible search. |
| `webodm_core/api/flightplan.py` | Whitelisted endpoints; the only Frappe-aware code. |
| `webodm_core/webodm_core/doctype/webodm_{flight_plan,drone_profile,camera_profile}/` | DocTypes. Controllers validate through the API module's functions. |
| `services/geospatial/app/utils/elevation.py`, `routers/raster.py` | `POST /raster/sample`. |
| `webodm_frontend/frontend/src/lib/flightplan.js` | Client + pure helpers (definition building, formatting, vertex editing). |
| `…/src/composables/useShapeEditor.js`, `lib/planLayers.js` | Drawing / editing on Leaflet; the mission's map layers. |
| `…/src/pages/{FlightPlanner,FlightPlans,Equipment}.vue`, `components/flightplan/` | Pages and their parts. |

## From a drawing to a mission

1. The page keeps a small state (vertices, type, corridor width, takeoff
   point, drone id, camera id, parameters, elevation source) and turns it
   into a **definition**. Parameters still at their default are left out, so
   a saved plan records what the user chose.
2. 300 ms after the last change it calls `preview` twice at most: first with
   `terrain: "cached"`, which answers in milliseconds from whatever
   elevation grid is cached and sets `terrain_pending` if it had to skip the
   terrain; then, only in that case, with `terrain: "fetch"`. A sequence
   number discards stale answers. Dragging a corner recomputes once, when
   the drag ends.
3. `api.flightplan.compute` resolves the profiles (built-in or the
   organization's own; a saved plan's snapshot stands in for a deleted
   profile), obtains a `TerrainModel`, loads the zone file if configured and
   calls `plan_mission`.
4. `plan_mission`:
   - projects the shape into a local metric frame centred on it;
   - derives height ↔ GSD, footprint, line spacing and photo spacing;
   - lays out the legs (`survey.py`);
   - assigns heights: constant, or following the terrain;
   - estimates distances, times, photos and splits the mission into
     per-battery flights;
   - analyses the route against the terrain (height above ground,
     clearance, the effect on GSD and overlap, a profile for the chart);
   - runs the checks.
5. The mission dict is returned to the page, stored with the plan on save,
   and read by the exporters. It contains its own equipment snapshot.

## Models

### Local frame

A transverse Mercator projection centred on the site, scale factor 1
(`geo.LocalFrame`, Snyder's series). Round trips are exact to a millimetre;
30 km from the centre a length is stretched by about 11 ppm. Plans are
limited to 60 km across. A tangent-plane approximation was rejected: its
east–west scale error grows with latitude and site size.

### Photogrammetry

For a nadir photo from height *H* above ground:

```
GSD            = sensor_width × H / (focal_length × image_width)
footprint      = (sensor_width × H / f) × (sensor_height × H / f)
line spacing   = footprint_across × (1 − side_overlap)
photo spacing  = footprint_along  × (1 − front_overlap)
photo interval = photo spacing / speed
```

Landscape orientation puts the long side of the sensor across the line.
The frontend repeats only the first line (for the linked height / GSD
inputs); `lib/flightplan.test.js` checks it against the same reference
values as the backend tests (Phantom 4 Pro and Mavic 3E: H / 36.5 cm/px;
Zenmuse P1 35 mm: H / 80).

### Area layout

- **Direction.** *Auto* flies parallel to the hull edge across which the
  shape is narrowest (rotating calipers over the convex hull): the fewest
  lines. A user bearing overrides it.
- **Lines.** `ceil(height / spacing)` scan lines, centred, so the outermost
  line is within half a spacing of the edge and the edge is inside its
  swath. Each scan line is clipped with the even-odd rule over all rings:
  concave outlines yield several legs per line, holes are skipped. Gaps
  shorter than two photo spacings (or 10 m) are bridged.
- **Order.** Boustrophedon from the corner nearest the takeoff point. A
  double grid appends a second layout at right angles, starting where the
  first ended.

### Corridor layout

`passes = max(min_passes, ceil((width − swath) / spacing) + 1)` offset
curves of the centre line, `spacing` apart and centred on it, flown
alternately. Offsets are mitred (bevelled past a mitre limit of 2) and
backward runs on the inside of a tight bend are removed, so a pass always
advances with the centre line.

### Heights

Every waypoint height is **relative to the takeoff point**, which is what
DJI, Litchi and MAVLink relative-altitude missions fly. With terrain:

- *constant*: `alt = H`; height above ground at a point is
  `ground(takeoff) + H − ground(point)`;
- *terrain following*: each leg is sampled every half grid spacing; the
  target is `ground(point) + H − ground(takeoff)`; a Douglas-Peucker pass
  over (distance, target) keeps the samples needed to stay within the
  following tolerance (default 5 % of *H*, clamped to 2–10 m). The planned
  heights are in the mission, not delegated to the autopilot's own terrain
  database.

Absolute datum offsets cancel because everything is relative to the same
elevation source, which is why a survey's DSM (whose absolute heights may
be off by metres) is a valid source.

### Terrain model

The planner asks an elevation source once for a **grid** over the site
(padded, snapped to 0.001° so small edits reuse it) and interpolates
bilinearly from it. Node spacing is never finer than the data's resolution
and the node count never exceeds the budget (400 for a global service,
10 000 for a project raster). Grids are cached in Redis by source and
extent. A failure to obtain data is a finding, not an error.

For a project raster the geospatial service resamples the DSM / DTM with
the statistic that fits its meaning — **max** for a surface model, **mean**
for a terrain model — into cells half the grid spacing wide, reading real
pixels from the finest overview within a 30 Mpx budget, and each node takes
the statistic of the 3 × 3 cells around it. A 4 m wide, 30 m tall mast in a
0.5 m DSM is therefore still 30 m tall on a 20 m grid, wherever the grid
falls (`tests/test_elevation_sample.py`).

### Flight time and batteries

```
leg time    = length / speed  [+ 2 × (speed / 2.5 m/s² + 2 s) for a multirotor: brake, turn, accelerate]
turn        = distance / speed [+ π × turn radius for a fixed-wing]
transit     = 20 s + first height / climb rate + distance out / transit speed
              + distance back / transit speed + return height / descent rate
usable time = rated time × endurance factor × (1 − reserve)
```

Legs are packed greedily into flights so that each flight, including its
own transit out and back, fits the usable time; flights break only at leg
ends. A single leg that cannot fit is a blocking finding. Wind, temperature
and battery age are not modelled, and the plan says so.

## Findings: severity and basis

Every finding carries two independent labels (`issues.py`):

| Severity | Effect |
|---|---|
| `error` | Not flyable as specified with this equipment. Blocks mission export. |
| `warning` | Needs the pilot's decision. Mission export requires acknowledgement. |
| `info` | Context. |

| Basis | Meaning | Examples |
|---|---|---|
| `checked` | Arithmetic against the profile and the plan's numbers | speed limit, photo interval, motion blur |
| `estimated` | From a model with real uncertainty | terrain clearance, overlap over high ground, climb rate, batteries |
| `advisory` | Compared with data that may be incomplete or stale | rule preset, zone file |
| `not_checked` | The platform has no data | no elevation data |
| `note` | Guidance on using the plan or file; not a check | heights are relative to the launch position |

Alongside the findings every mission has an **assurance** table, one line
per topic — capability, terrain, obstacles, battery, regulation, airspace,
weather — each with a status from the first four bases. Obstacles and
weather are always `not_checked`; airspace is `not_checked` without a zone
file and `advisory` with one; nothing is ever labelled verified, safe or
cleared (`test_mission.TestAssurance`). The same notice text is attached to
every mission, shown in the planner and the export dialog, and written into
the overview KML, the GeoJSON and the README of a multi-file export.

Checks never guess in the comfortable direction: an unknown aircraft figure
skips its check and the specification list shows "not published"; an
unknown photo interval assumes a slow 2 s and downgrades the finding to a
warning that says it is assumed.

## Export

`exporters.export_options(mission)` and `exporters.export(mission, format)`
share one gate (`_gate`), so the dialog shows exactly what the download will
do:

1. a *mission* format is available only if the drone's profile lists it;
2. any `error` finding of the plan, or of the format's own `check`, blocks
   it;
3. `warning` findings require `acknowledged=True`;
4. *boundary* and *reference* formats are always available.

`build_routes` turns the mission into routes: one per battery flight, split
again at leg ends when the format or the aircraft's app caps the waypoint
count (a leg longer than the cap is cut and resumed from the cut point).
The *waypoint* trigger rebuilds each leg as one point per exposure, keeping
corridor bends. One route renders to one file; several are zipped with a
README.

The stored mission is authoritative. `WebODMFlightPlan.validate` recomputes
it from the definition on every save that did not come from the API (Desk,
`/api/resource`), so a client cannot plant a mission with its findings
removed (`test_flightplan.test_the_mission_is_computed_on_the_server_whoever_saves`).

Format notes:

- **DJI WPML**: the `waypoint` template (explicit waypoints, so the aircraft
  flies the planned lines), `relativeToStartPoint` heights, one
  `multipleDistance` / `multipleTiming` action group per leg or a
  `reachPoint` `takePhoto` per exposure. Only elements in DJI's published
  WPML reference are written. Aircraft and payload ids come from the
  profiles with their source (`dji-docs` or `community`).
- **MAVLink**: one command list for both files — takeoff, speed, mount,
  then per leg waypoint / `DO_SET_CAM_TRIGG_DIST` on … waypoint / off, RTL
  or land — in `MAV_FRAME_GLOBAL_RELATIVE_ALT`.
- **Litchi**: the 46-column CSV that Mission Hub exports; distance interval
  set on the waypoint that starts a leg.

## Extending it

### Adding an aircraft or a camera

Edit `catalog/drones.json` / `cameras.json`; no code. See
[Drones and cameras](equipment.md#adding-a-model-to-the-built-in-catalog).

### Adding an export format

1. Create `flightplan/exporters/<name>.py` with a subclass of `Exporter`:
   `id`, `label`, `extension`, `mime`, `kind` (`mission` / `boundary` /
   `reference`), `description`, `instructions`, and for a mission format
   `triggers` (the first is the default) and `max_waypoints`.
2. Implement `render(ctx, route) -> bytes` (mission) or
   `render_plan(ctx) -> bytes`, and `check(ctx, routes) -> [Issue]` for
   anything the format cannot express (an `error` there refuses the export).
3. Add an instance to `_EXPORTERS` in `exporters/__init__.py`.
4. List the format in the profiles of the aircraft that can use it
   (`mission.formats`), and, if custom drones should be able to choose it,
   in a flight app family in `profiles.ECOSYSTEMS`.

Routes, splitting, the gate, the dialog and the download need no change.

### Adding an elevation provider

Subclass `ElevationProvider` in `elevation.py`, implement
`_batch(points) -> [elevation | None]`, set `id`, `label`, `batch_size`,
nominal `resolution_m` / `vertical_accuracy_m` and a `note`, and add it to
`PROVIDERS`. Gridding, caching and everything downstream are unchanged.

### Adding a mission type

Add a layout function in `survey.py` returning `Leg`s, a branch in
`plan_mission` and the type in `params.PLAN_TYPES`. Heights, estimates,
checks and exports work on legs and do not care how they were laid out.

### Adding a rule preset or a parameter

A preset is an entry in `catalog/regulations.json`. A parameter is a `Spec`
in `params.py` (the form reads its range, default and help from the
catalog endpoint) plus its use in the planner.

## Decisions

| Decision | Why |
|---|---|
| Plan on the server, not in the browser | One implementation for preview, save and export; testable without a browser; the server can refuse an export on its own computation. The cost is a round trip per edit, hidden by the debounce and the cached-terrain first answer (a preview without a terrain fetch takes a few milliseconds). |
| No geometry library | The Frappe image carries none, and the needed geometry (scan lines, hull, offsets) is small and well tested. Adding `shapely` would mean an image rebuild for every deployment. |
| Heights relative to the takeoff point, computed here | It is what the target flight apps fly; it makes any elevation source usable regardless of its absolute datum; and the pilot sees the heights before flying, instead of trusting an autopilot's terrain database. The price — the pilot must launch from the planned point — is stated on every export. |
| A grid, fetched once and cached | Public elevation services are rate limited. One grid serves every later edit of the same site; the two-phase preview keeps the editor responsive on a cold cache. |
| Max, not mean, for surface models | A route is cleared against the tops of things. |
| Equipment figures may be unknown | Published specifications are incomplete. A missing figure skips a check visibly rather than inventing a limit. |
| Support levels per aircraft and format | "Exports KMZ" would be false for most DJI consumer aircraft and misleading for aircraft whose apps plan their own lines. `official` / `community` / `boundary` says what will actually happen. |
| No bundled airspace data | Authoritative airspace data is national, licensed and changes daily. A stale bundled layer would be worse than none. The operator-supplied zone file is opt-in and always labelled advisory; its absence is shown as "not checked". |
| Errors block, warnings need acknowledgement | A plan the aircraft cannot fly should not leave the platform as a mission file; a plan that is flyable but exceeds a preset (a 150 m flight under an authorisation) is the pilot's call, on the record. |
| Saved plans keep an equipment snapshot | A catalog or profile change must not silently alter a plan that has been reviewed; duplicating or re-saving picks up current figures. |

## API

`webodm_core.api.flightplan` (all POST, session-authenticated, organization
required):

| Method | Purpose |
|---|---|
| `catalog()` | Drones, cameras, formats, flight app families, parameter specs, rule presets, what is configured. |
| `geocode(query)` | Coordinates parsed locally, otherwise the configured geocoder. |
| `elevation_sources(project)` | Global model, the project's completed surveys, none. |
| `preview(definition, project, terrain, plan)` | `{ok, mission}` or `{ok: false, error: {message, field}}`. |
| `list_plans(project?)`, `get_plan(name)` | Summaries; one plan with definition and mission. |
| `save_plan(project, title, definition, name?, description?)` | Create or update; always recomputes. |
| `duplicate_plan(name, project?, title?)`, `delete_plan(name)` | Reuse; remove. |
| `export_options(name, triggers?)` | Per format: support, file count, findings, verdict. |
| `export_plan(name, format, trigger?, acknowledged?, firmware?)` | The file (or zip) as a download. |
| `save_camera(profile)`, `delete_camera(docname)`, `save_drone(profile)`, `delete_drone(docname)` | The organization's own equipment. |

Geospatial service: `POST /raster/sample {path, points: [[lng, lat]…],
spacing_m, statistic}` → `{values, resolution_m, cell_m, statistic,
covered}`.

## Testing

| Suite | What it covers | Run |
|---|---|---|
| `webodm_core/flightplan/test_*.py` (233, no Frappe) | Projection accuracy; geometry; catalog integrity and custom profile validation; photogrammetry against vendor figures; area and corridor layout (coverage, concave shapes, holes, direction, ordering); estimates; battery splitting; every capability, imagery, terrain, obstacle and rule check; terrain model and providers (faked HTTP); every exporter (files parsed back), compatibility per aircraft, the gate, splitting; coordinate parsing; zone checks | `cd frappe-bench/apps/webodm_core && python -m pytest webodm_core/flightplan` |
| `webodm_core/api/test_flightplan.py` (55, Frappe) | Catalog; custom equipment and its isolation between organizations; preview; terrain caching, failure and budgets; a survey's DSM as source; plan CRUD, server-side recompute, project scoping, cascade, duplicate, profile snapshot; export download, gate, audit note; geocoder; zone file | `frappe --site <site> run-tests --app webodm_core --module webodm_core.api.test_flightplan` |
| `services/geospatial/tests/test_elevation_sample.py` (8) | Sampling statistics on real GeoTIFFs, nodata, overviews and budgets, foot-based rasters, the endpoint | `cd services/geospatial && python -m pytest tests/test_elevation_sample.py` |
| Frontend vitest (94 new) | Client and helpers; map layer construction; the shape editor; findings panel; export dialog; planner, list and equipment pages | `cd frappe-bench/apps/webodm_frontend/frontend && npm test` |
| `e2e/flight-planner.e2e.mjs` (66 checks) | The whole flow in a real browser against a running stack | see `e2e/README.md` |

The planner suite is also discovered by Frappe's test runner, so CI's
existing `run-tests --app webodm_core` step runs it; the geospatial and
frontend jobs pick up their new tests without workflow changes.

The frontend fixture (`src/lib/__fixtures__/flightplan.json`) is generated
by the real planner (`make_flightplan_fixture.py`), so the UI tests exercise
the shapes the server returns.

Sixteen deliberate bugs (swapped coordinates in an exporter, a disabled
gate, a flipped clearance sign, an off-by-one in scan-line clipping, …) were
each introduced once and each failed the planner suite.

### What was exercised during development

- **A matrix of 180 missions** (three plans — a 20 ha area, a 100 ha area, a
  3 km corridor — × twelve aircraft and camera combinations × flat ground,
  a 10 % slope and a 60 m ridge × constant altitude and terrain following):
  all planned; every available export rendered and its archive verified;
  terrain-following height above ground stayed within tolerance; no flight
  exceeded the usable battery time; per-flight photo counts summed to the
  total.
- **Real services**: address search through the public Nominatim; a site in
  the hills north of Bandung planned against the public Open Topo Data
  (ground 1385–1545 m under a 500 × 350 m block: constant altitude was
  rightly flagged, terrain following held 95–105 m above ground with 69
  waypoints); the cached-then-fetch path timed at 0.06 s and 7 s cold, 0.04 s
  warm.
- **A survey's DSM end to end**: Frappe → geospatial `/raster/sample` → a
  0.25 m GeoTIFF, 0.7 s for a 10 000-node grid; a 20 m wide, 35 m tall
  structure in the DSM was found as the lowest-clearance point.
- **The browser run** (headless Chrome against a throwaway stack with the
  worktree's backend and built SPA): sign-in, navigation, coordinate and
  address search, drawing, estimates, terrain, takeoff point, linked height
  and GSD, five aircraft, a capability error, save, blocked and
  acknowledged exports with the downloaded files opened, reload, corner
  drag / insert / remove, corridor, list, duplicate, custom camera and
  drone, phone viewport.

## Limits and what is not done

- **Not flight-tested, and not imported into the vendor apps.** The files
  follow the published format references and are parsed back by the tests;
  no export was loaded into DJI Pilot 2, DJI Fly, QGroundControl, Mission
  Planner or Litchi, or flown, while building this. See
  [Exporting missions](export.md#what-has-and-has-not-been-verified).
- **Catalog figures are nominal** and were entered from manufacturers'
  published specifications; a few are derived (sensor sizes from equivalent
  focal lengths) and some are not published at all. DJI WPML ids outside
  DJI's public tables are marked as such.
- **Wind, obstacles, NOTAMs, weather** are not modelled; airspace only
  through an operator's zone file.
- **Oblique capture** is not modelled: a gimbal pitch other than −90° is
  exported, but coverage and overlap assume nadir (the plan says so).
- **Fixed-wing turns** are approximated (line overshoot and an arc per
  turn), not planned as flyable curves.
- **Flight lines do not avoid holes or no-fly zones** by rerouting; a hole
  is skipped by the camera, a zone is flagged.
- **No boundary import** (KML / GeoJSON / shapefile) into the planner, no
  multi-polygon missions, no per-line manual editing, no imperial units.
- **No real-time link**: this is planning and export; nothing is uploaded
  to an aircraft or tracked in flight.
- **Sites across the antimeridian** are planned without elevation data.
