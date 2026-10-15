# Narration — voice-over script

For the 2:51.6 cut. 360 words, written for about 150 words a minute: a calm, confident presenter's pace with room to breathe at the chapter cards.

**No voice-over audio is included in the video.** The only speech engines installed here are eSpeak and Flite, which sound robotic and would undercut an investor presentation, and no paid service was used. The video carries the story with on-screen captions instead. This script is ready for a human voice or a licensed TTS voice; see "Adding a recording" below. The same text, timed, is in `investor-demo.srt`.

## Script

Each line starts at its **In** time and must finish by **Out**. Lines under a chapter card may run on over it.

| # | In | Out | Over | Line | Words |
|---|---|---|---|---|---|
| 1 | 0:00.4 | 0:05.5 | Title card | G20 Tech turns drone imagery into decisions. Follow one survey, start to finish. | 13 |
| 2 | 0:05.8 | 0:15.4 | 1 · Drone Data | Each organization gets its own workspace: projects, tasks and their status. Flights are stored as reusable datasets: upload once, process as often as needed. | 24 |
| 3 | 0:15.7 | 0:24.2 | 1 · Drone Data | This dataset is a single flight: sixty-eight photos, each carrying its GPS position and capture time. | 16 |
| 4 | 0:24.6 | 0:39.0 | 2 · Processing | Processing runs on the OpenDroneMap engine, managed from the browser. This task turned those photos into a five-centimetre orthophoto, elevation models, a point cloud and a 3D model. A new task needs only images and a preset. | 37 |
| 5 | 0:39.2 | 0:55.0 | 3 · Visualization | Open the task and the orthophoto is on the map, with each camera position and the flight path. Switch the basemap for context, then zoom from the whole site down to five centimetres per pixel. | 35 |
| 6 | 0:55.3 | 1:06.1 | 3 · Visualization | Elevation comes too. The surface model shows buildings and trees; blend it over the imagery, or switch to bare-earth terrain. | 20 |
| 7 | 1:06.4 | 1:17.1 | 3 · Visualization | Measurement happens on the map. Outline a building and the platform returns its footprint and its volume, computed from the surface model. | 22 |
| 8 | 1:17.4 | 1:31.6 | 3 · Visualization | The same survey as a textured 3D model. Rotate it, zoom in, pan across it, right in the browser, with nothing to install. | 23 |
| 9 | 1:31.9 | 1:53.4 | 3 · Visualization | For full detail there is the point cloud: ten million points, streamed as you move. Colour it by elevation, or by class: ground, vegetation, buildings. Draw a line across the site and read off an elevation profile. | 37 |
| 10 | 1:53.7 | 1:59.5 | 4 · AI Analysis | Analysis plugins bring AI to every survey. Here, a detector for vehicles and people. | 14 |
| 11 | 1:59.8 | 2:11.9 | 4 · AI Analysis | The result comes back as map data: one hundred and eighty-four objects on this site, each with a class and a confidence score, ready to download. | 26 |
| 12 | 2:12.2 | 2:27.4 | 4 · AI Analysis | Change detection compares two surveys of one site and reports cut and fill volumes with their uncertainty. In this demo both inputs are runs of the same flight, so it finds almost nothing: half a percent of the site. | 39 |
| 13 | 2:27.7 | 2:35.0 | 5 · Actionable Insights | Results leave the platform in standard formats: cloud-optimized GeoTIFFs, a LAZ point cloud and a GLB model. | 17 |
| 14 | 2:35.3 | 2:45.1 | 5 · Actionable Insights | The analysis layer is open. Plugins are enabled per organization and distributed through a built-in marketplace, so new models arrive without changing the platform. | 24 |
| 15 | 2:45.4 | 2:50.8 | Closing card | Drone data, processing, visualization, AI analysis, insight. One platform, from flight to decision. | 13 |

## Read-through

G20 Tech turns drone imagery into decisions. Follow one survey, start to finish.

**Drone Data**

Each organization gets its own workspace: projects, tasks and their status. Flights are stored as reusable datasets: upload once, process as often as needed. This dataset is a single flight: sixty-eight photos, each carrying its GPS position and capture time.

**Processing**

Processing runs on the OpenDroneMap engine, managed from the browser. This task turned those photos into a five-centimetre orthophoto, elevation models, a point cloud and a 3D model. A new task needs only images and a preset.

**Visualization**

Open the task and the orthophoto is on the map, with each camera position and the flight path. Switch the basemap for context, then zoom from the whole site down to five centimetres per pixel. Elevation comes too. The surface model shows buildings and trees; blend it over the imagery, or switch to bare-earth terrain. Measurement happens on the map. Outline a building and the platform returns its footprint and its volume, computed from the surface model. The same survey as a textured 3D model. Rotate it, zoom in, pan across it, right in the browser, with nothing to install. For full detail there is the point cloud: ten million points, streamed as you move. Colour it by elevation, or by class: ground, vegetation, buildings. Draw a line across the site and read off an elevation profile.

**AI Analysis**

Analysis plugins bring AI to every survey. Here, a detector for vehicles and people. The result comes back as map data: one hundred and eighty-four objects on this site, each with a class and a confidence score, ready to download. Change detection compares two surveys of one site and reports cut and fill volumes with their uncertainty. In this demo both inputs are runs of the same flight, so it finds almost nothing: half a percent of the site.

**Actionable Insights**

Results leave the platform in standard formats: cloud-optimized GeoTIFFs, a LAZ point cloud and a GLB model. The analysis layer is open. Plugins are enabled per organization and distributed through a built-in marketplace, so new models arrive without changing the platform.

**Closing**

Drone data, processing, visualization, AI analysis, insight. One platform, from flight to decision.

## Direction

- Tone: plain and factual. The pictures do the selling; the voice explains what is on screen.
- Say "G twenty Tech". "GeoTIFF" is "gee-oh-tiff"; spell out L-A-Z and G-L-B. "OpenDroneMap" is three words run together.
- "Five centimetres per pixel" and "half a percent" are the two figures to land clearly.
- The change-detection line states that both inputs are runs of the same flight. Keep it; it is what makes the example honest.

## Every claim, and where it comes from

| Claim | Source |
|---|---|
| One workspace per organization; reusable datasets | Dashboard and Datasets pages |
| Sixty-eight photos with GPS position and capture time | *Cadastre* dataset page |
| OpenDroneMap engine | Raster metadata: "Software: ODM 3.6.2" |
| Five-centimetre orthophoto, elevation models, point cloud, 3D model | Task outputs: 5.00 cm/px orthophoto, DSM, DTM, LAZ, GLB |
| Camera positions, flight path, basemaps | Map layer panel |
| Footprint and volume from the surface model | Volume tool result |
| Ten million points | Viewer counter: 10,498,703 points |
| Colour by elevation or class: ground, vegetation, buildings | Point cloud panel |
| One hundred and eighty-four objects, class and confidence | Stored detection run: 184 features with `class` and `confidence` |
| Cut and fill volumes with uncertainty; half a percent of the site | Stored change-detection run: 0.5 % changed, fill 17.2 m³ ± 1.7, cut 4.89 m³ ± 2.69 |
| Both inputs are runs of the same flight | Both Beach tasks use the *Brighton* dataset |
| Cloud-optimized GeoTIFFs, LAZ, GLB | Console downloads and raster metadata ("COG") |
| Plugins per organization, built-in marketplace | Plugins and Marketplace pages |

Nothing in the script refers to customers, revenue, speed, accuracy benchmarks or cost savings; none of those were observable in the deployment.

## Adding a recording

1. Record the script against the In/Out times above (or play `investor-demo.mp4` with `investor-demo.srt` as a guide).
2. Save it as `audio/narration.wav` (or `.mp3` / `.m4a`), starting at 0:00 of the video.
3. Optionally add licensed music as `audio/music.mp3`.
4. Run `python3 build_video.py`. Narration is mixed at full level, music underneath.
