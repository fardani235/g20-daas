# Flight planning: configuration

For operators. Flight planning works out of the box; everything here is
optional and concerns the three things the planner looks up outside the
platform: addresses, terrain elevation and (if you supply it) airspace
zones.

## What leaves the platform

| Lookup | Default service | What is sent | When |
|---|---|---|---|
| Address search | the public Nominatim (`nominatim.openstreetmap.org`) | the text the user typed | when a user searches for something that is not a coordinate |
| Terrain elevation | the public Open Topo Data (`api.opentopodata.org`, dataset `mapzen`) | the coordinates of a grid over the survey site | the first time a site is planned with the global model |

Both requests are made by the Frappe web workers, never by the browser, and
both are cached (addresses for a day, elevation grids for 30 days), so the
services see each site once. Coordinates typed by hand are parsed locally,
and a project's own DSM / DTM is read through the platform's geospatial
service: neither involves an outside lookup.

If the location of a survey must not be disclosed to a third party, run your
own instances (both projects publish self-hosting guides) or switch the
lookups off. With both off, users enter coordinates and plan against a
previous survey's DSM / DTM or flat ground.

The public services are shared and rate limited (Nominatim: one request per
second; Open Topo Data: one request per second, 100 points each, 1000 a
day). The planner respects that: it paces its requests, asks for at most
`WEBODM_FLIGHTPLAN_ELEVATION_MAX_POINTS` grid nodes per site, and each user
is limited to 30 uncached address searches and 20 uncached elevation
fetches a minute. For more than light use, self-host.

## Settings

Set as environment variables on the Frappe services (compose:
`.env`; Helm: `frappe.env`). `infra/frappe/configure_site.py` writes them to
`common_site_config.json` at container start as the `flightplan_*` keys.
An empty value means the built-in default.

| Variable | Site config key | Default | Meaning |
|---|---|---|---|
| `WEBODM_FLIGHTPLAN_GEOCODER` | `flightplan_geocoder` | `nominatim` | `nominatim`, or `none` to switch address search off. |
| `WEBODM_FLIGHTPLAN_GEOCODER_URL` | `flightplan_geocoder_url` | `https://nominatim.openstreetmap.org` | Base URL of a Nominatim-compatible service (`/search` is appended). |
| `WEBODM_FLIGHTPLAN_ELEVATION_PROVIDER` | `flightplan_elevation_provider` | `opentopodata` | `opentopodata`, `open-elevation`, `open-meteo`, or `none`. |
| `WEBODM_FLIGHTPLAN_ELEVATION_URL` | `flightplan_elevation_url` | the provider's public URL | Base URL, e.g. `http://opentopodata:5000/v1`. |
| `WEBODM_FLIGHTPLAN_ELEVATION_DATASET` | `flightplan_elevation_dataset` | `mapzen` | Open Topo Data dataset name. |
| `WEBODM_FLIGHTPLAN_ELEVATION_RESOLUTION_M` | `flightplan_elevation_resolution_m` | per provider | Native resolution of your data, shown to users and used as the smallest grid spacing. |
| `WEBODM_FLIGHTPLAN_ELEVATION_ACCURACY_M` | `flightplan_elevation_accuracy_m` | per provider | Nominal vertical accuracy (±m), shown to users and used for the clearance margin warning. |
| `WEBODM_FLIGHTPLAN_ELEVATION_MAX_POINTS` | `flightplan_elevation_max_points` | `400` | Grid nodes requested per site. Raise it for a self-hosted service to sample large sites more finely. |
| `WEBODM_FLIGHTPLAN_CONTACT` | `flightplan_contact` | – | Contact address added to the `User-Agent` of both lookups. |
| `WEBODM_FLIGHTPLAN_AIRSPACE_FILE` | `flightplan_airspace_file` | – | GeoJSON zone file (below). Absolute path inside the Frappe containers, or relative to the site directory. |
| `WEBODM_FLIGHTPLAN_AIRSPACE_LABEL` | `flightplan_airspace_label` | `Operator zone file` | What the file is called in the UI. |

`WEBODM_FLIGHTPLAN_CONTACT` must be a real address. The public Nominatim
refuses requests whose `User-Agent` carries a placeholder (an
`example.com`-style address answers HTTP 403); leave the variable empty
rather than inventing one.

### Elevation providers

| Provider | Data | Resolution / nominal accuracy told to users | Notes |
|---|---|---|---|
| `opentopodata` | whichever dataset the instance serves; public default `mapzen` (global) | 30 m / ±10 m | Self-hostable with any DEM (SRTM, Copernicus, national models). Set the resolution and accuracy variables to match your data. |
| `open-elevation` | SRTM | 30 m / ±16 m | Self-hostable. The public instance is best-effort. |
| `open-meteo` | Copernicus GLO-90 | 90 m / ±4 m | A surface model. The free tier is for non-commercial use. |
| `none` | – | – | No global model. Projects can still use their own surveys. |

A plan whose site is larger than the point budget allows at native
resolution is sampled more coarsely, and says so ("terrain was sampled every
N m"). Elevation is never fatal: if the service fails, the plan is computed
on flat ground and carries a warning.

The accuracy figure is what users are told; it is not measured. If you serve
a better model, say so with the two variables — the clearance margin warning
depends on it.

### Project surveys as elevation data

No configuration. A completed task's DSM or DTM appears in the planner's
*Elevation data* list for plans in the same project and is sampled by the
geospatial service (`POST /raster/sample`), from the serving cache or
straight from object storage. Requirements: the geospatial service is
reachable (`GEOSPATIAL_URL`), and on S3 deployments its identity can read
the task's `assets/` objects, which it already can for tiles.

### Airspace zone file

The platform ships no airspace data. If you have zones you want plans
flagged against — published UAS geographical zones, aerodrome buffers, a
client's no-fly areas — install them as a GeoJSON file and point
`WEBODM_FLIGHTPLAN_AIRSPACE_FILE` at it:

```json
{
  "type": "FeatureCollection",
  "as_of": "2026-10-01",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "name": "City heliport",
        "type": "aerodrome",
        "lower_m": 0,
        "upper_m": 120,
        "message": "Coordinate with the heliport operator."
      },
      "geometry": {"type": "Polygon", "coordinates": [[[107.60, -6.91], [107.62, -6.91], [107.62, -6.89], [107.60, -6.89], [107.60, -6.91]]]}
    }
  ]
}
```

`Polygon` and `MultiPolygon` features are read; all properties are optional
(`name`, `type`, `lower_m`, `upper_m`, `message`). `as_of` (or, without it,
the file's modification date) is shown to users. The file is re-read when it
changes; no restart is needed.

A plan whose boundary, route or takeoff point touches a zone gets a warning
naming it, which the user must acknowledge before exporting a mission file.
The result is always labelled **Advisory**: the file may be incomplete or
stale and holds no temporary restrictions or NOTAMs, and a plan that touches
no zone is explicitly *not* described as cleared. Without a file, airspace
is shown as **Not checked**. Keep the file current, or do not install one.

## Data model and permissions

Three DocTypes, all organization-scoped like projects and datasets (stamped
at insert, filtered in lists, checked on every endpoint):

| DocType | Holds |
|---|---|
| `WebODM Flight Plan` | A plan in a project: definition (shape, takeoff point, equipment ids, parameters, elevation source), the computed mission and summary columns. Deleted with its project. |
| `WebODM Drone Profile` | An organization's own aircraft. |
| `WebODM Camera Profile` | An organization's own camera. |

Run `bench migrate` (or deploy the new image, whose init job migrates) to
create them. There is nothing to backfill and no change to existing
DocTypes.

Every member of an organization can create, edit and delete its plans and
profiles. An exported mission file adds a comment to the plan with the
user, the format and the warnings they acknowledged (visible in Desk).

## Operations

| Symptom | Check |
|---|---|
| Address search answers "unavailable (the service answered HTTP 403)" | A placeholder in `WEBODM_FLIGHTPLAN_CONTACT`, or the public service has blocked the deployment for heavy use. Fix the contact or self-host. |
| "Elevation data could not be loaded (… rate limit …)" | The public daily or per-second limit. Self-host, or lower `…_MAX_POINTS`. |
| Terrain is always "sampled every N m" with a large N | Sites are large for the point budget: raise `…_MAX_POINTS` on a self-hosted provider. |
| A survey DSM cannot be used ("not available") | The geospatial service is down, or the raster is neither in the serving cache nor readable in object storage. |
| Stale terrain after replacing a DEM | Grids are cached for 30 days under `flightplan:terrain:*` in the Redis cache; clear those keys. |
| Zone file ignored | See the Error Log (title *WebODM Flight Planning*): unreadable path or invalid GeoJSON. |

Outbound requests time out after 15 s (elevation) and 10 s (address search)
and run in the web worker; a slow provider delays only the request that
needed it, and its failure is reported in the plan rather than raised.
