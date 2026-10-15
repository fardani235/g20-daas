# Storyboard — investor demo

Running time **2:51.6** (172 s), 1920×1080, 30 fps. Story: **Drone Data → Processing → Visualization → AI Analysis → Actionable Insights**.

Times below are from the built video (`build/timeline.md`). Every clip is a recording of the running platform; cards and captions are drawn by `build_video.py`. Parts overlap by the length of the cross-fade between them.

## The argument, in one paragraph

A drone flight is only worth something once it becomes an answer. The video follows a single survey of a residential site through the platform: the photos arrive and are kept as a reusable dataset; the photogrammetry engine turns them into survey products; those products are explored in the browser as maps, elevation, a 3D model and a point cloud, and measured directly; AI and change analysis run on top of them as plugins; and everything leaves in standard formats, with the analysis layer open to new plugins. What is shown is what was verified in `discovery.md` and nothing else.

## Hero moments

| Moment | Why it carries the story |
|---|---|
| Volume measurement on the orthophoto | A number an operations or finance person can use, produced in four clicks. |
| Textured 3D model, rotate / zoom / pan | The most immediately impressive visual; shows it runs in a browser. |
| Point cloud: classification and elevation profile | Technical depth: ten million points, streamed, classified and measurable. |
| Object detection, 184 objects | AI run on the survey itself, with the result returned as map data. |
| Change detection with uncertainty | It reports how sure it is, not only a number. |

## Timeline

### Opening

#### 0:00.0–0:04.5 · Title card · 4.5 s

- **On screen:** Title card. Logo lockup, headline, and the five-step story as a strip. Slow push-in; fades up from black.
- **Narration 0:00.4–0:05.5:** "G20 Tech turns drone imagery into decisions. Follow one survey, start to finish."

### Chapter 1 — Drone Data

**0:04.0–0:06.0** · chapter card (2.0 s) · *Survey imagery, organized and reusable* · step 1 of 5 lit in the strip · cross-fade in and out (0.5 s)

#### 0:05.5–0:15.7 · `01_workspace` · 10.2 s

- **On screen:** Dashboard at 150 % page zoom. The pointer passes the task counters (3 tasks, 3 completed) and the *Urban* project, then clicks **Datasets** in the navigation. The dataset library lists five flights; the pointer runs down to *Cadastre*.
- **Caption 0:06.0–0:10.3:** One workspace per organization — Projects, processing tasks and their status at a glance.
- **Caption 0:11.3–0:15.3:** A reusable dataset library — Upload a flight once, then process it as often as needed.
- **Transition in:** cross-fade, 0.50 s
- **Narration 0:05.8–0:15.4:** "Each organization gets its own workspace: projects, tasks and their status. Flights are stored as reusable datasets: upload once, process as often as needed."

#### 0:15.4–0:23.2 · `02_dataset` · 7.9 s

- **On screen:** The *Cadastre* dataset page: 68 images, 240 MB. Scroll down through the thumbnail grid; each card shows file name, GPS position and capture time. The account e-mail in the "Created by" card is blurred.
- **Caption 0:15.8–0:22.8:** Geotagged source imagery — 68 photos from one flight, each with its GPS position and capture time.
- **Transition in:** cross-fade, 0.35 s
- **Narration 0:15.7–0:24.2:** "This dataset is a single flight: sixty-eight photos, each carrying its GPS position and capture time."

### Chapter 2 — Processing

**0:22.8–0:24.8** · chapter card (2.0 s) · *Photogrammetry, managed from the browser* · step 2 of 5 lit in the strip · cross-fade in and out (0.5 s)

#### 0:24.2–0:38.0 · `03_processing` · 13.7 s

- **On screen:** The *Urban* project. Push-in on the task card: *Completed*, 68 images, and the orthophoto summary line. Click **Add Task**; in the dialog switch to **Upload new images** (drop zone, ground control, preset), scroll the engine options, then **Cancel**. Nothing is submitted.
- **Caption 0:24.6–0:28.8:** From photos to survey products — This run turned 68 photos into a 5 cm per pixel orthophoto.
- **Caption 0:29.5–0:37.5:** New task: drop images, pick a preset — Ground control and every engine option are there when needed.
- **Zoom 0:24.8–0:28.8:** push-in on the region [0, 170, 1180, 664] (x, y, w, h), eased in and out
- **Transition in:** cross-fade, 0.50 s
- **Narration 0:24.6–0:39.0:** "Processing runs on the OpenDroneMap engine, managed from the browser. This task turned those photos into a five-centimetre orthophoto, elevation models, a point cloud and a 3D model. A new task needs only images and a preset."

### Chapter 3 — Visualization

**0:37.5–0:39.5** · chapter card (2.0 s) · *Maps, elevation and 3D, in the browser* · step 3 of 5 lit in the strip · cross-fade in and out (0.5 s)

#### 0:39.0–0:55.4 · `04_map` · 16.4 s

- **On screen:** Click the task: the orthophoto and one marker per photo appear on the map. Tick **Flight path**, untick **Image markers**, switch the basemap to **Satellite**. Drag a large building to the centre and scroll-zoom onto it, zoom 17 to 20. The few seconds the full-resolution tiles take to arrive are dissolved out.
- **Caption 0:40.7–0:46.3:** The orthophoto, on the map — One georeferenced image of the site, with every camera position and the flight path.
- **Caption 0:46.8–0:54.9:** From site overview to 5 cm per pixel — Over street, satellite or topographic basemaps.
- **Cut inside the clip:** a wait for map tiles is removed with a 0.4 s dissolve
- **Transition in:** cross-fade, 0.50 s
- **Narration 0:39.2–0:55.0:** "Open the task and the orthophoto is on the map, with each camera position and the flight path. Switch the basemap for context, then zoom from the whole site down to five centimetres per pixel."

#### 0:55.0–1:06.4 · `05_elevation` · 11.4 s

- **On screen:** Tick **DSM**: the colour-ramped surface model covers the site. Drag its opacity slider to half so buildings show through. Untick it and tick **DTM**: the same site as bare earth.
- **Caption 0:56.5–1:02.3:** Digital Surface Model — Heights of buildings, trees and ground. Blend it over the imagery.
- **Caption 1:02.9–1:06.0:** Digital Terrain Model — The bare-earth surface, with structures and vegetation removed.
- **Cut inside the clip:** a wait for map tiles is removed with a 0.4 s dissolve
- **Transition in:** cross-fade, 0.35 s
- **Narration 0:55.3–1:06.1:** "Elevation comes too. The surface model shows buildings and trees; blend it over the imagery, or switch to bare-earth terrain."

#### 1:06.1–1:17.5 · `06_measure` · 11.4 s · **hero moment**

- **On screen:** On the building at zoom 19: click **Volume**, click the four roof corners, click **Finish**. The result appears in the toolbar and on the map: volume with fill and cut, and footprint area. Push-in on the map.
- **Caption 1:06.5–1:13.5:** Measure directly on the survey — Outline any feature for distance, area or volume.
- **Caption 1:13.9–1:17.3:** Volume and footprint in one step — Computed from the surface model inside the outline.
- **Zoom 1:14.3–1:17.2:** push-in on the region [330, 96, 1477, 831] (x, y, w, h), eased in and out
- **Transition in:** cross-fade, 0.35 s
- **Narration 1:06.4–1:17.1:** "Measurement happens on the map. Outline a building and the platform returns its footprint and its volume, computed from the surface model."

#### 1:17.1–1:31.9 · `07_model3d` · 14.8 s · **hero moment**

- **On screen:** The 3D model viewer. Scroll to zoom until the model fills the stage, left-drag to rotate, zoom closer, right-drag to pan, then a last slow orbit.
- **Caption 1:17.7–1:26.1:** Textured 3D model — Rotate, zoom and pan the reconstruction. Nothing to install.
- **Transition in:** cross-fade, 0.35 s
- **Narration 1:17.4–1:31.6:** "The same survey as a textured 3D model. Rotate it, zoom in, pan across it, right in the browser, with nothing to install."

#### 1:31.6–1:52.4 · `08_pointcloud` · 20.8 s · **hero moment**

- **On screen:** The point cloud viewer. Rotate; switch **Colour by** to *Elevation*, then *Classification* (ground, low vegetation, building), then back to *RGB*. Pick the elevation-profile tool, click two points across the site, press Enter: the profile dock opens with ground and surface lines and summary figures. The pointer reads along the chart.
- **Caption 1:32.1–1:36.9:** A 10-million-point cloud, streamed — Detail loads progressively as you move.
- **Caption 1:37.2–1:43.7:** Colour by elevation or by class — Ground, vegetation and buildings are told apart.
- **Caption 1:44.2–1:51.9:** Elevation profile along any line — Ground and surface heights, length and grade. Export as CSV.
- **Transition in:** cross-fade, 0.35 s
- **Narration 1:31.9–1:53.4:** "For full detail there is the point cloud: ten million points, streamed as you move. Colour it by elevation, or by class: ground, vegetation, buildings. Draw a line across the site and read off an elevation profile."

### Chapter 4 — AI Analysis

**1:51.9–1:53.9** · chapter card (2.0 s) · *Detection and change measurement on any survey* · step 4 of 5 lit in the strip · cross-fade in and out (0.5 s)

#### 1:53.4–1:59.8 · `09_detect` · 6.4 s

- **On screen:** Back on the *Urban* map. Click **Object Detection** under *Analysis*: the run dialog opens. Choose the detector *visdrone-yolov11s*, point at the confidence threshold, then **Cancel**. No run is started; the stored result is used.
- **Caption 1:55.7–1:59.6:** Run an AI model on any survey — Pick a detector: tree crowns, or vehicles and people.
- **Transition in:** cross-fade, 0.50 s
- **Narration 1:53.7–1:59.5:** "Analysis plugins bring AI to every survey. Here, a detector for vehicles and people."

#### 1:59.5–2:12.3 · `09_detect` · 12.8 s · **hero moment**

- **On screen:** Tick the **Object Detection** layer. Push-in on the legend and its per-class counts. Drag a car park to the centre and scroll-zoom to zoom 21: a box around each vehicle. The wait for full-resolution tiles is dissolved out.
- **Caption 1:59.9–2:05.6:** 184 objects found on this site — 137 cars, 20 vans, 20 pedestrians and more, each one georeferenced.
- **Caption 2:06.2–2:11.8:** Every detection is map data — Class, confidence and footprint, downloadable as GeoJSON.
- **Zoom 2:00.7–2:03.4:** push-in on the region [980, 170, 940, 529] (x, y, w, h), eased in and out
- **Cut inside the clip:** a wait for map tiles is removed with a 0.4 s dissolve
- **Transition in:** cross-fade, 0.35 s
- **Narration 1:59.8–2:11.9:** "The result comes back as map data: one hundred and eighty-four objects on this site, each with a class and a confidence score, ready to download."

#### 2:11.9–2:26.4 · `10_change` · 14.5 s · **hero moment**

- **On screen:** The *Beach* project, sidebar widened. Tick the **Change Detection** layer; click **Results** to open the plugin's own summary; push-in on it while the pointer marks *Changed* and *Net*. Click the largest fill region on the map: its popup lists area, volume with uncertainty and height change. Push-in on the popup.
- **Caption 2:12.3–2:16.6:** Change detection between two surveys — Cut and fill regions, measured from the elevation models.
- **Caption 2:16.9–2:21.5:** Volumes with their uncertainty — Demo input: two processing runs of one flight. 0.5% of the site is flagged.
- **Caption 2:23.1–2:26.3:** Every region carries its numbers — Area, volume with uncertainty, and height change.
- **Zoom 2:16.9–2:20.9:** push-in on the region [0, 560, 1040, 585] (x, y, w, h), eased in and out
- **Zoom 2:23.3–2:26.1:** push-in on the region [880, 250, 1040, 585] (x, y, w, h), eased in and out
- **Transition in:** cross-fade, 0.35 s
- **Narration 2:12.2–2:27.4:** "Change detection compares two surveys of one site and reports cut and fill volumes with their uncertainty. In this demo both inputs are runs of the same flight, so it finds almost nothing: half a percent of the site."

### Chapter 5 — Actionable Insights

**2:25.9–2:27.9** · chapter card (2.0 s) · *Standard files and an open analysis layer* · step 5 of 5 lit in the strip · cross-fade in and out (0.5 s)

#### 2:27.4–2:35.4 · `11_deliverables` · 8.0 s

- **On screen:** The task's console page at 150 % page zoom. The pointer crosses the five download buttons (Orthophoto, DSM, DTM, Point Cloud, 3D Model) without pressing them, then the raster metadata cards: size, pixel size, CRS, layout.
- **Caption 2:27.8–2:34.9:** Deliverables in standard formats — Cloud-optimized GeoTIFF rasters, a LAZ point cloud and a GLB model.
- **Transition in:** cross-fade, 0.50 s
- **Narration 2:27.7–2:35.0:** "Results leave the platform in standard formats: cloud-optimized GeoTIFFs, a LAZ point cloud and a GLB model."

#### 2:35.0–2:45.6 · `12_ecosystem` · 10.6 s

- **On screen:** The Plugins page at 125 % page zoom: five analysis plugins with type, version and status. The pointer marks Object Detection, Change Detection and Semantic Segmentation, then clicks **Marketplace**: the catalog with categories and two published products.
- **Caption 2:35.5–2:40.3:** Analysis is pluggable — Detection, change, contours, hillshade and segmentation, enabled per organization.
- **Caption 2:41.6–2:45.4:** A marketplace for new capabilities — Plugins are versioned, licensed and installed per organization.
- **Transition in:** cross-fade, 0.35 s
- **Narration 2:35.3–2:45.1:** "The analysis layer is open. Plugins are enabled per organization and distributed through a built-in marketplace, so new models arrive without changing the platform."

### Closing

#### 2:45.1–2:51.6 · Closing card · 6.5 s

- **On screen:** Closing card. Logo lockup, closing line, the five steps all lit, and the provenance note. Fades to black.
- **Transition in:** cross-fade, 0.50 s
- **Narration 2:45.4–2:50.8:** "Drone data, processing, visualization, AI analysis, insight. One platform, from flight to decision."

## Left out on purpose

| Not shown | Reason |
|---|---|
| A live processing run | Slow on this machine and unnecessary; completed tasks are reused. No processing time is claimed. |
| A live plugin run | Would add a result to the organization's data. Stored results are shown. |
| Semantic segmentation output | The plugin is disabled here and has no results; it appears by name only. |
| Invoices page | Hard-coded sample rows; would imply billing activity. |
| Landing-page animation | Its figures are illustrative, not measured. |
| Reports | The platform has no report generator. |
| Sign-in | Keeps credentials off screen. |
