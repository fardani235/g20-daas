# Exporting missions

A saved flight plan can be exported in the file format your aircraft's
flight app loads. This page says which aircraft take which files, what is
in them, and how to get each one onto the aircraft.

- [What you can export](#what-you-can-export)
- [Which aircraft take which file](#which-aircraft-take-which-file)
- [Before a mission file is released](#before-a-mission-file-is-released)
- [What is in a mission file](#what-is-in-a-mission-file)
- [Loading the file into the flight app](#loading-the-file-into-the-flight-app)
- [What has and has not been verified](#what-has-and-has-not-been-verified)

## What you can export

| Format | File | Kind | Photo trigger | Waypoint limit |
|---|---|---|---|---|
| DJI WPML route (.kmz) | `.kmz` | mission | distance, time, waypoint, manual | 65535 |
| QGroundControl plan (.plan) | `.plan` | mission | distance, time, manual | – |
| MAVLink waypoint list (.waypoints) | `.waypoints` | mission | distance, time, manual | – |
| Litchi mission (.csv) | `.csv` | mission | distance, time, waypoint, manual | 99 |
| Survey boundary (.kml) | `.kml` | boundary | – | – |
| Plan overview (.kml) | `.kml` | reference | – | – |
| Plan data (.geojson) | `.geojson` | reference | – | – |

- **Mission** files are flown as planned: the waypoints and heights you see
  in the planner are the ones in the file.
- The **boundary** file holds only the survey outline (or the corridor's
  centre line). It is for flight apps that plan their own lines: you import
  the outline and enter the plan's height, overlap and speed there. The
  export dialog lists those values.
- **Reference** files are for looking at the plan: the overview KML opens in
  Google Earth, the GeoJSON in any GIS. They carry the estimates, the
  findings and the notice with them. They are not mission files.

## Which aircraft take which file

Autonomous flight is not one feature that every drone has. Flight apps
differ in whether they load missions at all, in which format, how many
waypoints, and whether a file can trigger the camera. The planner therefore
offers, per aircraft, only what its app can load, and states how well
supported that is:

| Aircraft family | Mission file | Support | Notes |
|---|---|---|---|
| DJI Enterprise: Mavic 3E / 3T / 3M, Matrice 30, 300 / 350 RTK, Matrice 4E / 4T, Matrice 3D (dock) | DJI WPML `.kmz` | supported (DJI Pilot 2, FlightHub 2) | Photos by distance interval from the file. |
| DJI consumer with waypoint flight: Mavic 3 Classic, Air 3, Mini 4 Pro | DJI WPML `.kmz` | **unofficial** (DJI Fly) | No import function: the file replaces a saved mission. About 200 waypoints per mission. DJI Fly does not run interval triggers from the file. |
| Older DJI: Air 2S, Mini 2, Mavic 2 Pro, Phantom 4 Pro | Litchi `.csv` | supported by Litchi (third-party app) | 99 waypoints per mission. |
| PX4 / ArduPilot vehicles (generic, Freefly Astro) | QGroundControl `.plan`, MAVLink `.waypoints` | supported | The vehicle's camera trigger must be configured. |
| Parrot ANAFI Ai | MAVLink `.waypoints` | unofficial | Through Parrot's SDK flight-plan interface; FreeFlight 7 itself has no file import. |
| DJI Phantom 4 RTK, Autel EVO II / EVO Max, WingtraOne, eBee X | boundary `.kml` only | boundary import | Their apps plan the flight lines themselves. |
| DJI Mini 3 | none | – | No waypoint flight. |

The full list, per model, is on the **Drones and cameras** page and in
[Drones and cameras](equipment.md). Custom drone profiles get the formats of
the flight app family chosen for them.

A format the aircraft cannot use is shown greyed out in the export dialog
with the reason, and the server refuses it even if requested directly.

## Before a mission file is released

Every export goes through the same gate on the server:

1. **The format must apply to the plan's aircraft.**
2. **A plan with a blocking finding (red) gives no mission file.** The
   aircraft or camera cannot fly it as specified — too fast for the airframe
   or the camera, above a firmware height limit, estimated terrain clearance
   below your minimum, a flight line that does not fit one battery — or the
   format itself objects (no DJI WPML id for a custom drone; heights outside
   what Litchi accepts; a terrain-following plan for an aircraft whose
   missions cannot carry a height per waypoint). The dialog lists the
   findings; fix them in the planner.
3. **Warnings (amber) must be acknowledged.** The dialog repeats them next to
   the format's own notes; the download stays disabled until you tick the
   acknowledgement. Who exported which format, and which warnings they
   acknowledged, is noted on the plan.

Boundary and reference files are not held back: they contain no flight
instructions, only geometry and the plan's own findings.

The mission in the file is the one stored with the plan, which the server
computed when the plan was saved; a client cannot substitute its own.

## What is in a mission file

**Heights are relative to the launch position.** That is how DJI, Litchi
and MAVLink missions are usually flown, and how the planner computes them.
For a terrain-following plan each waypoint carries its own height, worked
out from the elevation data relative to the planned takeoff point. So:

> Launch from the planned takeoff point, or from a spot at the same
> elevation. Launching 10 m higher or lower shifts the whole route by 10 m.

If no takeoff point was set, the export warns that the first waypoint was
assumed.

**Photo trigger.** Where the format offers a choice, the dialog has a
*Photo trigger* list:

| Trigger | What the file does | Use it when |
|---|---|---|
| Distance interval | Starts photos every N metres at the start of each flight line and stops at its end. | The default wherever the app honours it (DJI Pilot 2, Litchi, MAVLink). |
| Time interval | The same, every N seconds. | The app or camera only supports timed shooting. |
| Photo at every waypoint | One waypoint per photo, with a take-photo action; the aircraft stops for each. Many more waypoints, and a longer flight than estimated. | The app ignores interval triggers in a file (DJI Fly) and you want a fully automatic mission. |
| None in the file | No trigger at all. | You start interval shooting on the camera yourself. The export tells you the interval the plan needs. |

**Several files.** A mission is split:

- into one file per **battery**, at the end of a flight line, when it does
  not fit the usable time of one battery;
- further when the app limits the **waypoints per mission** (99 for Litchi,
  about 200 for DJI Fly).

The download is then a zip: the files in flight order (`…-flight-2-of-3…`,
`…-part-1-of-2…`) plus a `README.txt` with the list, the plan's warnings
and the loading steps. Each file starts where the previous one ended.

**Line ends and turns.** Multirotors stop at the end of each flight line
and turn on the spot; intermediate waypoints (terrain following, corridor
bends) are flown through. Fixed-wing plans extend each line past the
boundary by the aircraft's turn radius so the turn happens outside the area.

## Loading the file into the flight app

Whatever the app: **review the imported route on its map before flying** —
the aircraft and payload it shows, the height of the first and last
waypoints, the speed, and what happens at the end and on signal loss.

### DJI Pilot 2 (Mavic 3 Enterprise, Matrice 30 / 300 / 350 / 4)

1. Copy the `.kmz` to the remote controller (microSD card or USB).
2. In DJI Pilot 2 open **Flight Route → Import Route (KMZ/KML)** and pick
   the file.
3. Pilot 2 shows the route as a *waypoint route*. Check the aircraft and
   payload, the waypoint heights and the photo interval, then upload and
   fly.

For the Matrice 4 series and the Zenmuse P1 the ids in the file are not in
DJI's public WPML tables (the export says so). If Pilot 2 refuses the file,
export the **boundary KML**, import it as a *Mapping* (area) or *Linear
Flight* (corridor) route, and enter the plan's height, overlap and speed.

### DJI FlightHub 2 (docks, fleet)

**Wayline Library → Import**, then assign the route to the device. For a
dock the takeoff point is the dock: set it as the plan's takeoff point.

### DJI Fly (Mavic 3 Classic, Air 3, Mini 4 Pro)

DJI Fly has **no import function**. The procedure below is unofficial: it
is widely used, DJI does not document or support it, and an app update can
break it.

1. In DJI Fly, create and save a placeholder waypoint mission (any two
   waypoints). Note which one it is.
2. Connect the controller or phone to a computer and find the mission
   folder: on Android `Android/data/dji.go.v5/files/waypoint/<id>/<id>.kmz`;
   on iOS in the Files app under *DJI Fly → wayline_mission*.
3. Replace the placeholder's `.kmz` with the exported file, **keeping the
   placeholder's file name**.
4. Reopen the mission in DJI Fly. The preview may still show the old place:
   pan to the real site and save once. Review every waypoint.

DJI Fly does not run interval triggers from a file. Choose either:

- **None in the file** (the default for these aircraft): before starting the
  mission, set the camera to timed shooting at the interval the export
  states (or the next shorter one the camera offers); or
- **Photo at every waypoint**: fully automatic, but the aircraft stops for
  each photo and a mission is limited to about 200 waypoints, so a typical
  survey becomes several files and takes considerably longer than estimated.

### Litchi (Air 2S, Mini 2, Mavic 2 Pro, Phantom 4 Pro)

1. In Litchi Mission Hub (flylitchi.com/hub) choose **Missions → Import**
   and select the `.csv`. Review the route and heights; save it to your
   account.
2. In the Litchi app open the mission in *Waypoint* mode.
3. Set the mission-level settings, which a CSV does not carry: **heading
   mode** (follow the waypoints), **finish action**, cruising speed and
   signal-loss behaviour.

Litchi missions hold at most 99 waypoints, so larger plans arrive as
several files to fly in order. On the Mini 2 Litchi flies the mission from
the mobile device: it stops if the control link is lost.

### QGroundControl (PX4, ArduPilot)

1. **Plan view → File → Open** and choose the `.plan` file.
2. Check the firmware and vehicle type shown; the export dialog lets you
   choose PX4 or ArduPilot.
3. **Upload** to the vehicle.

The mission asks for photos with `MAV_CMD_DO_SET_CAM_TRIGG_DIST` (or timed
capture). The vehicle must have its camera trigger configured — PX4:
`TRIG_*` parameters or a MAVLink camera; ArduPilot: `CAM_*` parameters —
otherwise it flies the lines without shooting. Altitudes are relative to
home: arm at the planned takeoff point.

### Mission Planner and other MAVLink tools

**Plan → Load File** and choose the `.waypoints` file (the plain-text
`QGC WPL 110` format, which QGroundControl and MAVProxy read too), review,
then **Write**. The first row is the planned home position.

### Flight apps that plan their own lines (boundary KML)

Export **Survey boundary (.kml)** and import it as the area, or as the
centre line of a linear / corridor mission. Menu names differ between app
versions; look for the KML import in the app's mission or project screen:

| App | Aircraft | Where |
|---|---|---|
| DJI Pilot 2 | DJI Enterprise | Flight Route → Import Route (KMZ/KML) → *Mapping* or *Linear Flight* |
| DJI GS RTK | Phantom 4 RTK | Photogrammetry → import KML |
| DJI GS Pro | Phantom 4 Pro, Mavic 2 Pro | New mission → import KML |
| WingtraPilot | WingtraOne | Import KML when creating the area or corridor |
| eMotion | eBee X | Import KML as the mission area |
| Autel Explorer / Autel Enterprise | EVO II, EVO Max | Mission → import KML (depends on app version and controller) |

Then set the height, front and side overlap and speed from the plan (the
export dialog lists them) and, if you planned terrain following, switch it
on in the app. The app's own line count, flight time and battery estimate
replace the planner's.

### Review and GIS

**Plan overview (.kml)** opens in Google Earth with the boundary, the
flight path at its height above ground, the waypoints and the takeoff
point — useful for briefing and for looking at the route against 3D terrain
and imagery. **Plan data (.geojson)** loads into QGIS or your own tooling;
waypoint heights are metres above the takeoff point.

## What has and has not been verified

Stated plainly, so you know how far to trust a file before its first use:

- The **file structures** follow the published references: DJI's WPML
  reference (Cloud API documentation), QGroundControl's plan format and
  MAVLink command definitions, the `QGC WPL 110` text format, and the CSV
  that Litchi Mission Hub itself exports. Automated tests parse every file
  the exporters write and check waypoint order, coordinates, heights,
  trigger commands and splitting.
- The exports have **not been flight-tested, and not been imported into
  every flight app and firmware version** as part of building this feature.
  Apps change their importers. Treat the first use with your app version as
  a test: import, review on the app's map, and where the app has a
  simulator, simulate before flying.
- **DJI WPML ids** marked *not in DJI's public tables* (Matrice 4 series,
  Zenmuse P1, the DJI Fly consumer aircraft) come from route files reported
  by users, not from DJI documentation.
- The **DJI Fly** file swap is unofficial by nature.

If an app rejects a file, the boundary KML is the dependable fallback: every
mapping app listed above imports an outline.
