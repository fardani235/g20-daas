# Drones and cameras

Flight plans are computed for a specific aircraft and camera: the camera
decides the ground resolution and the spacing of lines and photos, the
aircraft decides how fast and how long the mission can be flown and which
mission files it can be given. This page lists the built-in equipment,
explains how to add your own, and (for developers) how to add a model to
the built-in catalog.

- [What the figures mean](#what-the-figures-mean)
- [Built-in aircraft](#built-in-aircraft)
- [Built-in cameras](#built-in-cameras)
- [Your own equipment](#your-own-equipment)
- [Adding a model to the built-in catalog](#adding-a-model-to-the-built-in-catalog)

## What the figures mean

The built-in catalog holds the manufacturers' **published nominal figures**
as of the date shown on the *Drones and cameras* page. They are a starting
point, not a measurement of your aircraft: firmware, payload, propellers,
temperature, altitude and battery age all change them. Where a manufacturer
publishes no figure the catalog says *not published* and the planner **skips
the check that needs it** rather than guessing; the one exception is the
camera's shortest photo interval, where a cautious 2 s is assumed and the
plan says so.

| Figure | Used for |
|---|---|
| Rated flight time, endurance factor | Usable time per battery = rated time × endurance factor × (1 − reserve). The factor (0.7–0.85 in the catalog) allows for payload and real conditions; the reserve is set per plan (25 % by default). |
| Speed limit in missions | The highest speed the plan may ask for. |
| Climb / descent rate | Takeoff and landing time; whether the aircraft can follow the terrain at the planned speed. |
| Service ceiling | Checked against the highest point of the route above sea level, when elevation data is available. |
| Height limit above takeoff | A firmware limit (500 m on DJI consumer aircraft). |
| Wind limit | Shown for context only. Wind is not modelled. |
| Turn radius, minimum airspeed | Fixed-wing and VTOL: lines are extended past the boundary by the turn radius. |
| Sensor size, image size, focal length | Ground resolution (GSD), the footprint of a photo, and from them line and photo spacing. |
| Shortest photo interval | The highest speed at which the camera can still take every photo the overlap needs. |
| Shutter type | An electronic (rolling) shutter gets a note about rolling-shutter correction when flown fast. |

**Mission files** says what each aircraft's flight app can be given:

| Level | Meaning |
|---|---|
| supported | The named app documents importing this file. |
| unofficial | It works through a procedure the manufacturer does not document (DJI Fly: replacing a saved mission file). Expect it to break with app updates. |
| boundary import | The app imports only the survey boundary and plans its own flight lines, altitudes and photo triggering. The lines shown in the planner are then an estimate of that mission. |

Details per format and per app: [Exporting missions](export.md).

## Built-in aircraft

¹ Usable time per battery with the default 25 % reserve.

| Aircraft | Type | Rated time | Usable¹ | Mission speed | Wind limit | Cameras | Mission files |
|---|---|---|---|---|---|---|---|
| AgEagle (senseFly) eBee X | fixed-wing | 59 min | 38 min | 30 m/s | 12.8 m/s | S.O.D.A. 3D, Aeria X | boundary KML — boundary import (eMotion) |
| Autel Robotics EVO II Pro V3 | multirotor | 40 min | 22 min | 15 m/s | 12 m/s | EVO II Pro camera | boundary KML — boundary import (Autel Explorer) |
| Autel Robotics EVO Max 4T | multirotor | 42 min | 25 min | 15 m/s | 12 m/s | EVO Max 4T wide camera | boundary KML — boundary import (Autel Enterprise) |
| DJI Air 2S | multirotor | 31 min | 17 min | 15 m/s | 10.7 m/s | Air 2S camera | Litchi CSV — supported (Litchi (third-party app, Mission Hub import)) |
| DJI Air 3 | multirotor | 46 min | 26 min | 15 m/s | 12 m/s | Air 3 wide camera (12 MP), Air 3 wide camera (48 MP) | DJI WPML — unofficial (DJI Fly (Waypoint Flight)) |
| DJI Matrice 300 RTK | multirotor | 55 min | 29 min | 15 m/s | 12 m/s | Zenmuse P1 (35 mm), Zenmuse P1 (24 mm), Zenmuse P1 (50 mm), Zenmuse H20 wide camera, Zenmuse H20T wide camera | DJI WPML — supported (DJI Pilot 2 / FlightHub 2); boundary KML — boundary import (DJI Pilot 2) |
| DJI Matrice 30T | multirotor | 41 min | 25 min | 15 m/s | 12 m/s | Matrice 30T wide camera | DJI WPML — supported (DJI Pilot 2 / FlightHub 2); boundary KML — boundary import (DJI Pilot 2) |
| DJI Matrice 350 RTK | multirotor | 55 min | 29 min | 15 m/s | 12 m/s | Zenmuse P1 (35 mm), Zenmuse P1 (24 mm), Zenmuse P1 (50 mm), Zenmuse H20 wide camera, Zenmuse H20T wide camera | DJI WPML — supported (DJI Pilot 2 / FlightHub 2); boundary KML — boundary import (DJI Pilot 2) |
| DJI Matrice 3D (Dock 2) | multirotor | 50 min | 30 min | 15 m/s | 12 m/s | Matrice 3D wide camera | DJI WPML — supported (DJI FlightHub 2) |
| DJI Matrice 4E | multirotor | 49 min | 29 min | 15 m/s | 12 m/s | Matrice 4E wide camera | DJI WPML — supported (DJI Pilot 2 / FlightHub 2); boundary KML — boundary import (DJI Pilot 2) |
| DJI Matrice 4T | multirotor | 49 min | 29 min | 15 m/s | 12 m/s | Matrice 4T wide camera | DJI WPML — supported (DJI Pilot 2 / FlightHub 2); boundary KML — boundary import (DJI Pilot 2) |
| DJI Mavic 2 Pro | multirotor | 31 min | 17 min | 15 m/s | 10.7 m/s | Mavic 2 Pro Hasselblad camera | Litchi CSV — supported (Litchi (third-party app, Mission Hub import)); boundary KML — boundary import (DJI GS Pro) |
| DJI Mavic 3 Classic | multirotor | 46 min | 26 min | 15 m/s | 12 m/s | Mavic 3 Hasselblad camera | DJI WPML — unofficial (DJI Fly (Waypoint Flight)) |
| DJI Mavic 3 Enterprise (M3E) | multirotor | 45 min | 27 min | 15 m/s | 12 m/s | Mavic 3E wide camera | DJI WPML — supported (DJI Pilot 2 / FlightHub 2); boundary KML — boundary import (DJI Pilot 2) |
| DJI Mavic 3 Multispectral (M3M) | multirotor | 43 min | 26 min | 15 m/s | 12 m/s | Mavic 3M RGB camera | DJI WPML — supported (DJI Pilot 2 / FlightHub 2); boundary KML — boundary import (DJI Pilot 2) |
| DJI Mavic 3 Thermal (M3T) | multirotor | 45 min | 27 min | 15 m/s | 12 m/s | Mavic 3T wide camera | DJI WPML — supported (DJI Pilot 2 / FlightHub 2); boundary KML — boundary import (DJI Pilot 2) |
| DJI Mini 2 | multirotor | 31 min | 17 min | 10 m/s | 8.5 m/s | Mini 2 camera | Litchi CSV — supported (Litchi (third-party app, Mission Hub import)) |
| DJI Mini 3 | multirotor | 38 min | 21 min | 16 m/s | 10.7 m/s | Mini 3 Pro camera (12 MP) | none (no autonomous flight) |
| DJI Mini 4 Pro | multirotor | 34 min | 19 min | 15 m/s | 10.7 m/s | Mini 4 Pro camera (12 MP), Mini 4 Pro camera (48 MP) | DJI WPML — unofficial (DJI Fly (Waypoint Flight)) |
| DJI Phantom 4 Pro V2.0 | multirotor | 30 min | 17 min | 15 m/s | 10 m/s | Phantom 4 Pro / RTK camera | Litchi CSV — supported (Litchi (third-party app, Mission Hub import)); boundary KML — boundary import (DJI GS Pro) |
| DJI Phantom 4 RTK | multirotor | 30 min | 17 min | 13.9 m/s | 10 m/s | Phantom 4 Pro / RTK camera | boundary KML — boundary import (DJI GS RTK) |
| Freefly Systems Astro (mapping payload) | multirotor | 25 min | 16 min | 15 m/s | – | α7R IV (35 mm) | QGC plan — supported (Auterion Mission Control / QGroundControl) |
| Parrot ANAFI Ai | multirotor | 32 min | 18 min | 12 m/s | 12.7 m/s | ANAFI Ai camera | MAVLink waypoints — unofficial (Parrot Ground SDK / OpenFlight (MAVLink flight plan)) |
| Wingtra WingtraOne GEN II | vtol | 59 min | 38 min | 16 m/s | 12 m/s | RX1R II (35 mm), α6100 (20 mm) | boundary KML — boundary import (WingtraPilot) |
| Generic ArduPilot / PX4 fixed-wing | fixed-wing | 45 min | 27 min | 22 m/s | – | any | QGC plan — supported (QGroundControl); MAVLink waypoints — supported (Mission Planner / QGroundControl) |
| Generic PX4 / ArduPilot multirotor | multirotor | 25 min | 15 min | 15 m/s | – | any | QGC plan — supported (QGroundControl); MAVLink waypoints — supported (Mission Planner / QGroundControl) |

Notes on particular models:

- **DJI Matrice 300 / 350 RTK**: the rated 55 min is without a payload; the
  0.7 endurance factor allows for a Zenmuse P1-class payload.
- **DJI Matrice 4E / 4T and Zenmuse P1**: the aircraft and payload ids
  written into a WPML route are not in DJI's public WPML tables; they come
  from route files reported by users. The export says so. If Pilot 2 rejects
  the route, import the boundary KML and plan the area in Pilot 2.
- **DJI Mavic 3 Classic, Air 3, Mini 4 Pro**: DJI Fly has no import
  function. See [DJI Fly](export.md#dji-fly-mavic-3-classic-air-3-mini-4-pro).
- **DJI Air 2S, Mini 2, Mavic 2 Pro, Phantom 4 Pro**: DJI's own app has no
  waypoint mapping for these; missions are flown with the third-party app
  Litchi.
- **DJI Phantom 4 RTK, Autel, Wingtra, eBee**: their apps plan the flight
  themselves from an imported boundary.
- **DJI Mini 3**: no waypoint flight; the plan is a reference for flying by
  hand.
- **Generic PX4 / ArduPilot** entries are starting points, not real
  aircraft. Copy one and enter your airframe's measured endurance and speeds.

## Built-in cameras

Footprint is across × along the flight line with the camera in landscape
orientation.

| Camera | Sensor (mm) | Image (px) | Focal length | GSD at 100 m | Footprint at 100 m | Min. interval | Shutter |
|---|---|---|---|---|---|---|---|
| AgEagle (senseFly) Aeria X | 23.5 × 15.6 | 6000 × 4000 | 18.5 mm | 2.12 cm/px | 127 × 84 m | not published | global |
| AgEagle (senseFly) S.O.D.A. 3D | 13.2 × 8.8 | 5472 × 3648 | 10.6 mm | 2.28 cm/px | 125 × 83 m | not published | global |
| Autel Robotics EVO II Pro camera | 13.2 × 8.8 | 5472 × 3648 | 10.57 mm | 2.28 cm/px | 125 × 83 m | not published | electronic |
| Autel Robotics EVO Max 4T wide camera | 9.8 × 7.35 | 8192 × 6144 | 6.5 mm | 1.84 cm/px | 151 × 113 m | not published | electronic |
| DJI Air 2S camera | 13.2 × 8.8 | 5472 × 3648 | 8.38 mm | 2.88 cm/px | 158 × 105 m | 2 s | electronic |
| DJI Air 3 wide camera (12 MP) | 9.6 × 7.2 | 4032 × 3024 | 6.72 mm | 3.54 cm/px | 143 × 107 m | 2 s | electronic |
| DJI Air 3 wide camera (48 MP) | 9.6 × 7.2 | 8064 × 6048 | 6.72 mm | 1.77 cm/px | 143 × 107 m | 5 s | electronic |
| DJI Matrice 30 wide camera | 6.4 × 4.8 | 4000 × 3000 | 4.5 mm | 3.56 cm/px | 142 × 107 m | not published | electronic |
| DJI Matrice 30T wide camera | 6.4 × 4.8 | 4000 × 3000 | 4.5 mm | 3.56 cm/px | 142 × 107 m | not published | electronic |
| DJI Matrice 3D wide camera | 17.73 × 13.3 | 5280 × 3956 | 12.29 mm | 2.73 cm/px | 144 × 108 m | 0.7 s | mechanical |
| DJI Matrice 4E wide camera | 17.73 × 13.3 | 5280 × 3956 | 12.29 mm | 2.73 cm/px | 144 × 108 m | 0.5 s | mechanical |
| DJI Matrice 4T wide camera | 9.6 × 7.2 | 8064 × 6048 | 6.72 mm | 1.77 cm/px | 143 × 107 m | not published | electronic |
| DJI Mavic 2 Pro Hasselblad camera | 13.2 × 8.8 | 5472 × 3648 | 10.26 mm | 2.35 cm/px | 129 × 86 m | 2 s | electronic |
| DJI Mavic 3 Hasselblad camera | 17.73 × 13.3 | 5280 × 3956 | 12.29 mm | 2.73 cm/px | 144 × 108 m | 2 s | electronic |
| DJI Mavic 3E wide camera | 17.73 × 13.3 | 5280 × 3956 | 12.29 mm | 2.73 cm/px | 144 × 108 m | 0.7 s | mechanical |
| DJI Mavic 3M RGB camera | 17.73 × 13.3 | 5280 × 3956 | 12.29 mm | 2.73 cm/px | 144 × 108 m | 0.7 s | mechanical |
| DJI Mavic 3T wide camera | 6.4 × 4.8 | 8000 × 6000 | 4.4 mm | 1.82 cm/px | 145 × 109 m | not published | electronic |
| DJI Mini 2 camera | 6.3 × 4.7 | 4000 × 3000 | 4.49 mm | 3.51 cm/px | 140 × 105 m | 2 s | electronic |
| DJI Mini 3 Pro camera (12 MP) | 9.6 × 7.2 | 4032 × 3024 | 6.72 mm | 3.54 cm/px | 143 × 107 m | 2 s | electronic |
| DJI Mini 4 Pro camera (12 MP) | 9.6 × 7.2 | 4032 × 3024 | 6.72 mm | 3.54 cm/px | 143 × 107 m | 2 s | electronic |
| DJI Mini 4 Pro camera (48 MP) | 9.6 × 7.2 | 8064 × 6048 | 6.72 mm | 1.77 cm/px | 143 × 107 m | 5 s | electronic |
| DJI Phantom 4 Pro / RTK camera | 13.2 × 8.8 | 5472 × 3648 | 8.8 mm | 2.74 cm/px | 150 × 100 m | 2 s | mechanical |
| DJI Zenmuse H20 wide camera | 6.3 × 4.7 | 4056 × 3040 | 4.5 mm | 3.45 cm/px | 140 × 104 m | not published | electronic |
| DJI Zenmuse H20T wide camera | 6.3 × 4.7 | 4056 × 3040 | 4.5 mm | 3.45 cm/px | 140 × 104 m | not published | electronic |
| DJI Zenmuse P1 (24 mm) | 35.9 × 24 | 8192 × 5460 | 24 mm | 1.83 cm/px | 150 × 100 m | 0.7 s | global |
| DJI Zenmuse P1 (35 mm) | 35.9 × 24 | 8192 × 5460 | 35 mm | 1.25 cm/px | 103 × 69 m | 0.7 s | global |
| DJI Zenmuse P1 (50 mm) | 35.9 × 24 | 8192 × 5460 | 50 mm | 0.88 cm/px | 72 × 48 m | 0.7 s | global |
| MicaSense RedEdge-MX (per band) | 4.8 × 3.6 | 1280 × 960 | 5.4 mm | 6.94 cm/px | 89 × 67 m | 1 s | global |
| Parrot ANAFI Ai camera | 6.4 × 4.8 | 8000 × 6000 | 4.4 mm | 1.82 cm/px | 145 × 109 m | not published | electronic |
| Sony RX1R II (35 mm) | 35.9 × 24 | 7952 × 5304 | 35 mm | 1.29 cm/px | 103 × 69 m | not published | mechanical |
| Sony α6000 (20 mm) | 23.5 × 15.6 | 6000 × 4000 | 20 mm | 1.96 cm/px | 118 × 78 m | not published | mechanical |
| Sony α6100 (20 mm) | 23.5 × 15.6 | 6000 × 4000 | 20 mm | 1.96 cm/px | 118 × 78 m | not published | mechanical |
| Sony α7R IV (35 mm) | 35.7 × 23.8 | 9504 × 6336 | 35 mm | 1.07 cm/px | 102 × 68 m | not published | mechanical |

Sensor dimensions are the active image area. Where a manufacturer publishes
only a format name (4/3-inch, 1/2.3-inch), they are derived from the lens's
focal length and its published 35 mm equivalent, which is what makes the
computed GSD agree with the vendor's own figure (for example H / 36.5 cm/px
for the Mavic 3E and the Phantom 4 Pro, H / 80 for the Zenmuse P1 with the
35 mm lens).

## Your own equipment

Open **Flight Plans → Drones and cameras**. Profiles you add belong to your
organization: every member can use and edit them, nobody else can see them.
They appear first in the planner's lists, marked *Yours*.

### Add a camera

**Cameras → Add camera**, or **Copy** on a built-in camera to start from its
figures.

| Field | Notes |
|---|---|
| Name, manufacturer | How it appears in lists. |
| Sensor width × height (mm) | The active image area. |
| Image width × height (px) | Of the stills you will take (a 48 MP mode and a 12 MP mode are two profiles). |
| Focal length (mm) | The **real** focal length (as in the photo's EXIF), not the 35 mm equivalent. |
| Shortest photo interval (s) | The fastest the camera reliably takes stills in the mode you fly. Leave empty if unknown: 2 s is assumed. |
| Shutter | Mechanical, global or electronic (rolling). |

As you type, the dialog shows what the numbers mean — "At 100 m this camera
resolves 2.74 cm/px and one photo covers 150 × 100 m". Compare it with a
figure you trust for the camera (the manufacturer's GSD table, or a past
survey) before saving; a wrong sensor size or an equivalent focal length
shows up immediately there.

### Add a drone

**Drones → Add drone**, or **Copy** on a built-in aircraft.

| Field | Notes |
|---|---|
| Model, manufacturer, airframe, RTK | Airframe matters: a fixed-wing or VTOL cannot stop at a waypoint. |
| Rated flight time | The manufacturer's figure or, better, your own measurement with your payload. |
| Endurance factor | Share of the rated time you really get (0.1–1; default 0.8). If you entered a measured time, use 1. The dialog shows the resulting usable time. |
| Top speed, speed limit in missions, suggested mapping speed | The suggested speed is the default for new plans. |
| Climb / descent rate, wind limit, service ceiling, height limit, takeoff mass | Optional. Leave empty what you do not know. |
| Minimum airspeed, turn radius | Fixed-wing / VTOL only; the turn radius is required. |
| Cameras it carries | Tick every camera this aircraft flies with; choose the default. |
| How missions reach the aircraft | See below. |

**How missions reach the aircraft** is the one question that decides which
exports the profile is offered. Answer it for the app you actually fly with:

| Choice | Exports offered | Needs |
|---|---|---|
| DJI Pilot 2 / FlightHub 2 (Enterprise) | DJI WPML route, boundary KML | the aircraft's DJI WPML id |
| DJI Fly waypoint flight (unofficial file swap) | DJI WPML route (up to ~200 waypoints per file, no trigger in the file) | the aircraft's DJI WPML id |
| PX4 / ArduPilot (QGroundControl, Mission Planner) | QGroundControl plan, MAVLink waypoint list | – |
| Litchi | Litchi CSV (99 waypoints per file) | – |
| The manufacturer's app plans the flight | boundary KML only | – |
| No autonomous flight | none | – |

The **DJI WPML aircraft id** (`droneEnumValue`, and the sub-id for model
variants) is the number DJI's apps use to recognise the aircraft a route was
made for. The published values are in DJI's WPML reference (Cloud API
documentation → DJI WPML → Common Elements); the built-in profiles show the
ones the catalog uses. Without an id a WPML export is refused, because the
app would reject the file.

Do not give a profile a capability the aircraft does not have: exports are
offered on the strength of it.

### Editing and deleting

Edit or delete your own profiles from the same page. A camera that a drone
profile still carries cannot be deleted until it is removed from that drone.
Deleting a drone profile does not affect saved plans: each plan keeps the
figures it was computed with and can still be opened and exported.

## Adding a model to the built-in catalog

For developers. The catalog is data, not code: two JSON files in
`frappe-bench/apps/webodm_core/webodm_core/flightplan/catalog/`.

1. **Camera** — add an entry to `cameras.json`:

   ```json
   {
     "id": "acme-mapper-24",
     "manufacturer": "Acme",
     "name": "Mapper 24",
     "sensor_width_mm": 23.5, "sensor_height_mm": 15.6,
     "image_width_px": 6000, "image_height_px": 4000,
     "focal_length_mm": 24.0,
     "min_interval_s": 1.0,
     "shutter": "mechanical"
   }
   ```

   `min_interval_s` may be `null` (not published). DJI payloads may carry
   `"wpml": {"payload_enum": 66, "source": "dji-docs"}`; use
   `"source": "community"` for an id that is not in DJI's public tables.

2. **Drone** — add an entry to `drones.json`:

   ```json
   {
     "id": "acme-surveyor",
     "manufacturer": "Acme",
     "model": "Surveyor",
     "airframe": "multirotor",
     "max_flight_time_min": 38, "endurance_factor": 0.8,
     "max_speed_ms": 18, "max_mission_speed_ms": 15, "cruise_speed_ms": 8,
     "max_ascent_ms": 5, "max_descent_ms": 4,
     "max_wind_ms": 12, "service_ceiling_m": 5000,
     "max_height_above_takeoff_m": null, "takeoff_mass_g": 1800,
     "rtk": "optional",
     "cameras": ["acme-mapper-24"], "default_camera": "acme-mapper-24",
     "mission": {
       "waypoints": true,
       "per_waypoint_altitude": true,
       "formats": [
         {"id": "qgc-plan", "level": "official", "app": "Acme Ground Station"}
       ]
     }
   }
   ```

   Use `null` for a figure that is not published. `mission.formats` lists
   only what the aircraft's app really loads, each with its `level`
   (`official`, `community`, `boundary`), the `app` name and an optional
   `note`, default photo `trigger` and `max_waypoints`. An aircraft with no
   autonomous flight has `"waypoints": false` and no formats.

3. Run the planner's tests. `test_profiles.py` loads every entry through the
   same validation as custom profiles and checks that every camera and
   format reference resolves, that support levels match the kind of file,
   that DJI WPML aircraft carry an id with a stated source, and that the
   figures are physically plausible (square pixels, sensible speeds):

   ```sh
   cd frappe-bench/apps/webodm_core
   python -m pytest webodm_core/flightplan -q
   ```

4. Regenerate the tables on this page and bump `as_of` in both files:

   ```sh
   PYTHONPATH=frappe-bench/apps/webodm_core python -m webodm_core.flightplan.catalog_table
   ```

No planner, validator or exporter code changes for a new model. A new
*manufacturer ecosystem* (a file format the platform cannot write yet) is a
new exporter module: see [Architecture → Adding an export format](architecture.md#adding-an-export-format).
