# Proposal: Flight planning

## Why

The platform starts when the photos already exist. Everything that decides
whether those photos can be processed into a good map — the height, the
overlap, the flight lines, whether the camera can keep up at that speed,
whether one battery is enough — is decided earlier, in a different tool,
and nothing connects the two. Planning the mission where the project lives
closes that gap: the same project holds the plan, the photos and the
results, and a repeat survey can be planned over the terrain model the
previous one produced.

Drones differ too much for a single "export waypoints" button to be honest.
Some flight apps load a mission file, some only through an undocumented
file swap, some plan their own lines from a boundary, some have no
autonomous flight at all; cameras differ in how fast they can shoot. The
feature has to know this per aircraft and say it.

And a flight plan is a place where a tidy number can mislead. Terrain
clearance from a 30 m elevation model is an estimate; obstacles, weather and
airspace are not known to the platform at all. The plan must make that
distinction visible everywhere it appears.

## What Changes

- **Planner.** A Frappe-free package `webodm_core/flightplan/`: a local
  metric frame, area and corridor line layout, photogrammetry arithmetic,
  heights (constant or terrain following), estimates (distance, time,
  photos, per-battery flights), terrain analysis, and checks against the
  aircraft's and camera's limits. One pure function, `plan_mission`.
- **Equipment as data.** A built-in catalog (`catalog/drones.json`,
  `cameras.json`: 26 aircraft from DJI, Autel, Parrot, Freefly, Wingtra,
  AgEagle and generic PX4 / ArduPilot; 33 cameras) and organization-scoped
  `WebODM Drone Profile` / `WebODM Camera Profile` documents, read through
  the same validation. Each aircraft lists the mission formats its flight
  app loads, with a support level.
- **Plans in projects.** `WebODM Flight Plan` (organization-scoped, linked
  to a project, deleted with it): the definition, the computed mission and
  summary columns. The mission is always computed on the server.
- **Terrain.** Pluggable elevation providers (Open Topo Data, Open-Elevation,
  Open-Meteo; configurable or off), fetched once per site as a cached grid;
  or the DSM / DTM of a completed task of the project, sampled by a new
  additive geospatial endpoint `POST /raster/sample`.
- **Findings with a basis.** Every finding has a severity and says whether
  it was checked against the profile, estimated, advisory or not checked;
  every mission carries a per-topic table of what was and was not checked
  and a fixed notice. An optional operator-supplied GeoJSON zone file gives
  an advisory airspace check; without it airspace is "not checked".
- **Export.** Pluggable exporters — DJI WPML, QGroundControl plan, MAVLink
  waypoint list, Litchi CSV, boundary KML, overview KML, GeoJSON — behind
  one gate: format must apply to the aircraft, errors block mission files,
  warnings need acknowledgement. Missions are split per battery and per
  waypoint limit.
- **API** `webodm_core.api.flightplan`: catalog, geocode, elevation sources,
  preview, plan CRUD and duplicate, export options and download, custom
  equipment.
- **UI.** A planner page (search, draw and edit on the map, linked height /
  resolution, live estimates, terrain profile, findings, export dialog), a
  plan list (global and per project), a drones and cameras page with custom
  profiles; a *Flight Plans* tab, a project sub-tab and a button on the
  project map.
- **Docs**: user guide, equipment, export, configuration, architecture.

## Impact

- New: three DocTypes (run `migrate`), the `flightplan` package, the API
  module, three pages and their components, one geospatial endpoint,
  optional `WEBODM_FLIGHTPLAN_*` environment variables.
- Touched: `hooks.py` and `permissions.py` (three entries per DocType),
  `WebODMProject.on_trash` (cascade), `plugins/geospatial.py` (one client
  function), `infra/frappe/configure_site.py`, `docker-compose.yml`,
  `.env.example`, Helm `values.yaml` (a comment), `nav.js`, `main.js`,
  `MapView.vue` (one button).
- Not touched: tasks, datasets, the processing pipeline, storage, plugins,
  the marketplace.
- By default address search and terrain elevation are looked up from public
  services by the server; both are configurable and can be switched off.
