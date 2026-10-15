# Design

## Context

See `proposal.md`. The full record — models, formulas, extension points,
evidence, limits — is `docs/flight-planning/architecture.md`; this is the
short form of the decisions.

Shaping facts: the Frappe image carries no geospatial library; the SPA
draws on Leaflet and talks to whitelisted methods over a same-origin,
CSRF-protected session; the geospatial service already reads task rasters
from the serving cache or object storage; tenant-owned DocTypes are stamped
and filtered by hooks that a structural test enforces; public elevation and
geocoding services are rate limited.

## Decisions

### D1. The planner runs on the server, as one pure function

`plan_mission(definition, drone, camera, terrain, zones)` has no I/O and no
Frappe. Preview, save and export all use it, so the route on screen is the
route stored and exported, and the server can refuse an export on its own
computation. Rejected: planning in the browser (two implementations or an
untrusted one; nothing to test without a browser). The round trip per edit
is hidden by a 300 ms debounce and a two-phase preview.

### D2. No geometry dependency

Scan-line clipping, a convex hull, polyline offsets and a transverse
Mercator projection are a few hundred tested lines. `shapely` / `pyproj`
would have meant rebuilding the Frappe image for every deployment.

### D3. Equipment is data; capabilities are stated per aircraft

Catalog JSON and custom documents go through the same `normalize_*` and
reach the planner as `Drone` / `Camera`. A figure may be unknown, and then
its check is skipped visibly. Mission formats are listed per aircraft with a
level — `official`, `community`, `boundary` — because "supports waypoint
export" is true of few aircraft without qualification. Custom drones answer
one question (the flight app family), which expands to the generic mission
block.

### D4. Heights are relative to the takeoff point and computed here

That is what the target apps fly, it makes any elevation source usable
regardless of datum (a survey's own DSM included), and the pilot sees the
heights before flying. Terrain following is a Douglas-Peucker pass over the
sampled target profile, bounded by a tolerance. The cost — launch from the
planned point — is stated on every export. Rejected: leaving terrain
following to each autopilot's terrain database (invisible, inconsistent,
absent on most aircraft).

### D5. Elevation is a grid fetched once and cached

One request for a padded, snapped grid serves every later edit of the same
site. `preview(terrain="cached")` answers immediately and says when the
terrain is pending; the page then asks again with `fetch`. Failure is a
finding, never an error. For a task raster the geospatial service takes the
**max** in each cell of a surface model and the mean of a terrain model.

### D6. Findings carry severity and basis; nothing is "verified"

Severity decides the export gate; basis (`checked`, `estimated`,
`advisory`, `not_checked`, `note`) says how far to trust the statement. A
per-topic assurance table accompanies every mission, with obstacles and
weather always `not_checked`. Rejected: a single "plan is valid" indicator,
which would read as a safety statement.

### D7. No bundled airspace data

Authoritative airspace data is national, licensed and changes daily; a
stale bundled layer is worse than none. An operator may install a GeoJSON
zone file, whose result is always `advisory`; without it airspace is
`not_checked`. Rule presets are a few numeric limits, labelled as not legal
advice.

### D8. One export gate; the stored mission is authoritative

`export_options` and `export` share `_gate`: the format must be listed for
the aircraft, errors block mission files, warnings need acknowledgement,
boundary and reference files are always available. The controller
recomputes the mission on any save that did not come from the API, so a
client cannot plant one. Mission exports are noted on the plan.

### D9. Exporters render routes; splitting is shared

`build_routes` splits by battery flight and by waypoint cap at leg ends;
an exporter renders one route. Only documented elements are written to DJI
WPML; ids not in DJI's public tables are marked `community` and flagged on
export.

### D10. Outbound lookups are proxied, cached, paced and budgeted

The browser never calls a third party (the CSP stays as it is). Address
results are cached a day, grids 30 days; the public services' one-request-
per-second rule is respected; each user has a per-minute budget of uncached
lookups. Both lookups can be pointed at self-hosted services or switched
off.

## Risks

- Exports were validated against published format references and by
  parsing them back, not by importing them into each vendor app or flying
  them. Documented; the boundary KML is the fallback.
- Catalog figures are nominal and partly derived. Documented; custom
  profiles exist for exactly this.
- Public services may throttle or change. Mitigated by caching, budgets and
  configuration; failure degrades to flat-ground planning with a warning.
