# Flight planning

Plan a drone mapping mission on the platform before you fly it: find the
site, draw the area or the corridor to survey, pick your aircraft and
camera, and get the flight lines with an estimate of the resolution, photo
count, flight time and batteries. Then export the plan to your aircraft's
flight app, fly it, and upload the photos to the same project for
processing.

| Document | For whom |
|---|---|
| [User guide](#user-guide) (this page) | Pilots and survey planners |
| [Drones and cameras](equipment.md) | Supported equipment, custom profiles, adding models to the catalog |
| [Flight simulation](simulation.md) | Replaying a plan in 2D and 3D before flying it: what it shows, what it assumes, how it is built |
| [Exporting missions](export.md) | File formats, which aircraft take which, loading them into each flight app |
| [Configuration](configuration.md) | Operators: address search, elevation data, airspace zone file |
| [Architecture](architecture.md) | Developers: how it is built, the formulas, the decisions, the tests |
| [`openspec/specs/flight-planning/spec.md`](../../openspec/specs/flight-planning/spec.md), [`flight-simulation/spec.md`](../../openspec/specs/flight-simulation/spec.md) | Requirements and scenarios |

> **A flight plan is a planning estimate, not verified flight-safety
> information.** The planner checks your plan against the published limits
> of the aircraft and camera you select, estimates terrain clearance from an
> elevation model, and compares a few numbers with a rule preset. It has no
> data on obstacles, weather, NOTAMs or (unless your operator installs a
> zone file) airspace, and it says so on every plan. The remote pilot remains
> responsible for checking the route on site and for flying within the rules
> that apply.

## User guide

### Where it is

- **Flight Plans** in the top bar lists every plan of your organization.
- Inside a project, the **Flight plans** tab (next to *Map*) lists that
  project's plans; the project map also has a **Plan a flight** button.
- **Drones and cameras** (button on the Flight Plans page, or *Manage* in
  the planner) is the equipment list.

A plan always belongs to a project, next to the surveys processed from it.

### 1. Find the site

In the planner, type into the search box on the map:

- an address or place name ("Lembang, Bandung"), or
- coordinates, latitude first: `-6.7900, 107.6180`, `6.79 S 107.618 E`,
  `6°47'24"S 107°37'05"E`, or a pasted map link.

Coordinates are recognised without any lookup. If the address search is
unavailable or switched off, coordinates still work. If the project already
has a processed survey, the map opens on it, and **Latest orthophoto** in
the map options (layers button, top right) shows it under your plan.

### 2. Draw what to survey

Choose the mission type:

| Type | You draw | You get |
|---|---|---|
| **Area** | the outline of the area | parallel flight lines across it |
| **Corridor** | a line along a road, river, railway or pipeline, plus a **width** | passes parallel to the line that together cover the width |

Click **Draw area** (or **Draw line**) and click each corner on the map.

- Finish an area by clicking the first corner again; finish either shape
  with a double-click, **Enter** or the **Finish** button.
- **Backspace** removes the last corner, **Escape** abandons the drawing.

Afterwards the shape stays editable:

- drag a corner to move it;
- drag one of the faint handles in the middle of an edge to add a corner;
- right-click a corner to remove it;
- **Redraw** starts over. Switching between Area and Corridor keeps the corners.

Concave outlines are fine: a flight line that leaves and re-enters the
area is flown in pieces, without photographing the gap (very short gaps are
flown through). The outline must not cross itself.

**Takeoff point.** Click **Set takeoff point** and then the place you will
launch from; drag the **H** marker to adjust. Until you do, the plan assumes
you launch at the first waypoint (grey **H**). It matters: every height in
an exported mission is relative to the launch position, and the transit to
and from the area counts towards flight time and batteries. The mission
starts at the corner nearest the takeoff point.

### 3. Choose the aircraft and the camera

Pick the **Aircraft**; the **Camera** list then offers what that aircraft
carries (its standard camera first). Under the lists the planner says how
missions reach that aircraft:

- *Mission files: DJI Pilot 2 / FlightHub 2* — a mission file can be loaded
  directly;
- *Mission files (unofficial): DJI Fly* — it works, by a procedure the
  manufacturer does not document;
- *…plans its own lines from the boundary* — the manufacturer's app imports
  your survey outline and lays out its own flight;
- *No autonomous waypoint flight* — the plan can only guide a manual flight.

**Specifications used** shows the figures the estimates rest on. If your
aircraft or camera is missing, or yours flies differently from the published
figures, add your own profile: see [Drones and cameras](equipment.md).

### 4. Set the flight

| Setting | What it does |
|---|---|
| **Height** and **Ground resolution** | One quantity seen two ways. Type the height and read the resolution (GSD, cm per pixel), or type the resolution you need and read the height. The one you typed last is the input. |
| **Front overlap** | Overlap between consecutive photos on a line. 75–80 % is typical. |
| **Side overlap** | Overlap between neighbouring lines. 65–70 % is typical. |
| **Speed** | Speed along the lines. Empty uses the aircraft's suggested mapping speed. The camera's shortest photo interval and motion blur both limit it; the planner tells you the highest speed that works. |
| **Line direction** (area) | *Auto* picks the direction needing the fewest lines; untick it to set a compass bearing (for example to fly across the wind or along the rows of a crop). |
| **Double grid** (area) | A second grid at right angles: better 3D, about twice the flight. |
| **Minimum passes** (corridor) | Fly at least this many parallel passes. One pass gives a single strip with no side overlap, which reconstructs poorly; use 2 or more for a surface model. |

Under **Camera and advanced settings**: camera orientation (landscape puts
the long side of the image across the line), gimbal pitch, shutter speed
(used for the blur estimate), line overshoot (how far each line continues
past the boundary; automatic for fixed-wing aircraft), transit speed, return
height, start corner and what the aircraft does when finished.

Everything recomputes as you type. Numbers you leave empty use the default
shown in grey.

### 5. Terrain

| Altitude mode | Meaning |
|---|---|
| **Constant altitude** | Every waypoint at the same height above the takeoff point. Over hilly ground the height above ground, and with it the resolution and the overlap, changes along the route. |
| **Follow terrain** | Each waypoint's height follows the elevation model so the height above *ground* stays at your value. Extra waypoints are added only where the ground bends. |

**Elevation data** chooses where ground heights come from:

- the global elevation model your platform is configured with (about 30 m
  detail; bare ground only);
- **a survey of this project** — the DSM or DTM of a completed task. Far
  more detailed, and a DSM includes trees and buildings as they were on the
  day of that survey. It covers only the area that survey covered;
- **None** — the ground is assumed flat and level with the takeoff point.

With elevation data the Terrain section shows a profile of the ground and
the flight along the route, the range of height above ground and the lowest
estimated clearance (marked on the map). **Following tolerance** sets how
closely the route tracks the ground: smaller follows more closely with more
waypoints.

Terrain figures are estimates. A 30 m model smooths away narrow ridges and
knows nothing of trees, masts or wires; treat the clearance as indicative
and check it on site.

### 6. Limits

| Setting | What it does |
|---|---|
| **Rule preset** | A handful of numeric limits the plan is compared with (for example 120 m above ground). Not legal advice; pick *Custom limits* if you fly under a specific authorisation. |
| **Height limit above ground**, **Line-of-sight distance** | Override the preset's numbers. |
| **Minimum terrain clearance** | The plan is blocked where the estimated height above the terrain model falls below this. |
| **Tallest known obstacle** | The height above ground of the tallest thing you know of in or near the area (trees, a mast, a crane). The lowest part of the route is compared with it. |
| **Battery reserve** | The share of each battery kept in hand. Usable time per battery = rated time × the aircraft's endurance factor × (1 − reserve). |

### 7. Read the result

**Estimates** (top of the panel): ground resolution, area, photos, flight
time, flight distance and batteries; **Details** adds the footprint of one
photo, line and photo spacing, waypoint count and, when the mission needs
more than one battery, the flights it is split into (always at the end of a
flight line).

On the map: amber lines are flown with the camera triggering, dashed grey
lines are turns, **S** and **E** mark the start and end, the green rectangle
is the ground one photo covers, and the dotted violet lines are the transit
from and to the takeoff point.

**Checks** lists what the planner found. Each finding has a severity and
says how it was arrived at:

| Severity | Meaning |
|---|---|
| Red | The plan cannot be flown as specified with this aircraft and camera (or a required input is missing). No mission file can be exported until it is fixed. The message says what to change. |
| Amber | Flyable, but it needs your decision. Exporting a mission file asks you to acknowledge it. |
| Grey | A note. |

| Badge | Meaning |
|---|---|
| **Checked against profile** | Arithmetic against the aircraft and camera profile and your own numbers. As good as the profile is. |
| **Estimate** | From a model with real uncertainty (elevation data, endurance). Indicative only. |
| **Advisory** | Compared with data that may be incomplete or out of date (a rule preset, a zone file). |
| **Not checked** | The platform has no data for this. |

Below the findings, **What was and was not checked** states this per topic
for every plan: aircraft and camera limits, terrain clearance, obstacles,
flight time and batteries, aviation rules, airspace, wind and weather.
Obstacles and weather are always *Not checked*; airspace is *Not checked*
unless your operator has installed a zone file, and then only *Advisory*.

Typical findings and what to do:

| Finding | Fix |
|---|---|
| Speed above the aircraft's limit | Lower the speed. |
| The camera cannot keep up (photo interval too short) | Fly slower, higher, or lower the front overlap. The message gives the highest speed that works. |
| Motion blur | Fly slower or use a faster shutter. |
| Route comes too close to the terrain | Fly higher, switch to *Follow terrain*, or move the takeoff point. |
| Overlap drops over the highest ground | Use *Follow terrain* or fly higher. |
| Following the terrain needs a faster climb than the aircraft manages | Fly slower, or loosen the following tolerance. |
| One flight line does not fit one battery | Change the line direction, split the area, fly faster or take off closer. |
| Over the height limit of the rule preset / beyond the line-of-sight distance | Fly lower / plan observers or a closer takeoff point, or obtain the authorisation and set your own limits. |

### 8. Save, edit and reuse

Give the plan a title and **Save**. It is stored in the project with the
survey shape, your settings and the computed mission, and is shared with
your organization. Open it again from either list to keep editing; the
header shows whether there are unsaved changes.

To **reuse** a plan — the same site next month, or the same settings at
another site — use **Duplicate** (the copy button in the planner, or in the
list where you can also choose another project). A copy is recomputed, so it
picks up the current drone and camera profiles.

A saved plan keeps the equipment figures it was computed with. If a profile
changes later, the plan changes only when you edit and save it.

### 9. Simulate the flight

**Simulate flight** (next to *Export…*, or the play button of a plan in the
list) replays the plan: the aircraft flies the mission on the map and over
3D terrain, with play / pause, a timeline you can drag, speeds up to 64×,
five camera views, the photos and the ground they cover, and a dashboard of
speed, heights, battery and progress next to an elevation profile of the
whole flight. It works on an unsaved plan, changes nothing, and closes back
to the planner as it was.

It is an estimate replayed from the plan in still air - not telemetry and
not a safety check - and it says so. See [Flight simulation](simulation.md)
for what each part shows and what the simulation assumes.

### 10. Export

**Export…** saves the plan if needed and lists the formats for *your*
aircraft:

- **Mission files** are flown as planned. Only the formats the selected
  aircraft's flight app can load are offered; the others are shown greyed
  with the reason.
- **Boundary and reference files** are always available: the survey
  boundary for flight apps that plan their own lines, an overview for Google
  Earth, and the plan as GeoJSON.

Choose the format, the **photo trigger** where the format offers a choice,
read the notes, tick the acknowledgement if the plan has warnings, and
**Download**. A mission that needs several batteries, or more waypoints
than the app accepts, downloads as a zip with one file per flight and a
README listing them in order.

How to load each file into its flight app, and what each aircraft supports:
[Exporting missions](export.md).

### 11. After the flight

Upload the photos to the project as usual (**Add Task** on the project map).
If you planned a rolling-shutter camera at speed, the plan will have
suggested switching on ODM's `rolling-shutter` option when processing. A
later plan over the same site can use this survey's DSM or DTM as its
elevation data.

## Troubleshooting

| Symptom | Cause and remedy |
|---|---|
| "Address search is unavailable" | The geocoder is down, switched off or refusing the platform (see [Configuration](configuration.md)). Enter coordinates. |
| "Loading terrain…" takes several seconds | The public elevation service allows one request per second; a new site needs up to four. The result is cached, so later edits are instant. |
| "Elevation data could not be loaded" | The plan is computed as if the ground were flat. Try again, or choose another elevation source. |
| A survey's DSM gives "does not cover the takeoff point" | Heights are relative to the takeoff point, so it must lie inside the area that survey covered. Move it, or use the global model. |
| The format I expect is greyed out | The selected aircraft's flight app cannot load it. Check the aircraft, or see [Exporting missions](export.md). |
| No mission file for my aircraft at all | Its app plans its own lines (export the boundary KML), or it has no autonomous flight. |
| The flight app rejects the file | See the notes for that app in [Exporting missions](export.md); as a fallback, import the boundary KML and enter the plan's height, overlap and speed in the app. |
