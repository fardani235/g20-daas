# Investor demo video

A 2–3 minute product walkthrough recorded from the running DaaS deployment and
assembled with FFmpeg. Story: **Drone Data → Processing → Visualization → AI
Analysis → Actionable Insights**.

| File | What it is |
|---|---|
| `investor-demo.mp4` | The finished video (1920×1080, 30 fps, H.264 + silent AAC track) |
| `investor-demo.srt` | The narration as a subtitle track, timed to the video |
| `discovery.md` | What the platform actually does, and what was left out and why |
| `storyboard.md` | Scene-by-scene plan with timings |
| `narration.md` | Voice-over script with timings |
| `record_demo.sh` | Records the scenes with Playwright CLI |
| `scenes/` | One script per scene, plus `_lib.js` (cursor, pacing, map helpers) |
| `recordings/` | Raw scene recordings (`.webm`) and their cut marks (`.json`) |
| `edit.json` | The edit: order of parts, cut points, captions, zooms, narration |
| `build_video.py` | Draws cards and captions, cuts the clips, assembles the video |
| `playwright-cli.config.json` | Browser settings for recording |

Two steps regenerate everything:

```bash
./record_demo.sh           # about 15 minutes; writes recordings/
python3 build_video.py     # about 10 minutes; writes investor-demo.mp4
```

## Requirements

- The platform running and reachable at the URL in `.env.local`.
- `playwright-cli` 0.1.22 or newer, with Google Chrome installed (the config uses the `chrome` channel).
- FFmpeg 6 or newer with `libx264`, Python 3.10+ with Pillow.
- Fonts: Lato (`fonts-lato`). DejaVu Sans is used if Lato is missing.
- Internet access for the map's basemap tiles (OpenStreetMap, Esri).

Credentials live in `.env.local`, which git ignores:

```bash
DAAS_DEMO_URL=https://webodm.local:32443
DAAS_DEMO_USER=<account e-mail>
DAAS_DEMO_PASSWORD=<password>
```

The password is used once, by `record_demo.sh`, to sign in through the API.
Only the session cookie goes to the browser, so the sign-in form, the password
and the cookie never appear in a recording, a log or any file here. Any account
that can see the *Urban* and *Beach* projects works; a dedicated demo account is
a drop-in replacement.

## What the recorder does and does not touch

The scenes look, navigate and measure. They do **not** create tasks, start
processing, run plugins, install marketplace products, or change settings. Two
dialogs (*Add task*, *Run Object Detection*) are opened and cancelled. The
volume measurement and the elevation profile are computed on request and not
stored.

Three things are adjusted in the browser only, for legibility or privacy:

- Table and form pages are shown at 125–150 % page zoom (`h.scale(1.5)`), so their text reads at video size.
- On the dataset page the account e-mail in the "Created by" card is blurred.
- In the change-detection scene the sidebar is dragged wider with its own divider.

## Recording

```bash
./record_demo.sh                    # every scene, in order
./record_demo.sh 07_model3d         # one scene
./record_demo.sh 04_map 09_detect   # several
```

Each scene first rehearses off camera (so map tiles and 3D data are cached),
then records. A scene that fails is retried up to three times.

**Why the recordings look slow.** The screencast cannot deliver 30 distinct
frames a second from a laptop GPU while WebGL is rendering. So each scene is
performed two to four times slower than it will play (`h.start(name, { slow })`)
and `build_video.py` speeds it back up, which leaves more than 30 real frames
per second of playback. Every duration in a scene script — `h.sleep`, pointer
moves, drags — is written in *playback* time, and so are the marks in
`recordings/*.json`. You never need to think in slowed time.

A scene script reads top to bottom as a shot list:

```js
await h.start('06_measure');          // camera on
h.mark('in');                         // a named cut point
await h.click(page.getByRole('button', { name: 'Volume' }));
...
h.mark('result');
await h.sleep(2800);                  // hold, in playback milliseconds
return await h.stop();                // camera off; marks are saved
```

The pointer in the video is drawn by `_lib.js` (the screencast does not capture
the system cursor) and moves along eased, slightly curved paths.

The scenes are tied to this deployment's data: project and task names, a few
record ids in URLs, and map coordinates. They are constants near the top of
each scene file.

## Editing

`edit.json` is the whole edit. `timeline` is a list of parts, played in order
with a cross-fade between neighbours:

```jsonc
{ "type": "clip", "scene": "06_measure",
  "in": "in+0.1", "out": "out",              // cut points: a mark, mark±seconds, or seconds
  "zoom": [ { "at": "result+0.5", "dur": 2.9, "rect": [400, 96, 1280, 720] } ],
  "captions": [
    { "at": 0.4, "until": "result-0.3", "align": "map",
      "title": "Measure directly on the survey",
      "text": "Outline any feature for distance, area or volume." } ],
  "narration": "Measurement happens on the map. …" }
```

- **`in` / `out`** — where the clip starts and ends in its recording. Marks are listed in `recordings/<scene>.json`.
- **`captions`** — `at` / `until` are seconds from the start of the clip, or marks. Omit both and the caption spans the clip. `pos` is `bottom` (default), `top`, `middle`, or `[x, y]` for the caption's centre. `align` is `center`, `map` (centred on the map area), `left` or `right`. The chapter tag on each caption comes from the last `chapter` part.
- **`zoom`** — a push-in on `rect` (`[x, y, width, height]` in 1920×1080 pixels) that eases in, holds, and eases back out within `dur` seconds.
- **`narration`** — not rendered into the picture; it feeds `investor-demo.srt` and the timeline report.
- **`title` / `chapter` / `closing`** parts are drawn cards with a `dur`.
- Top-level `transitions` sets the cross-fade lengths.

```bash
python3 build_video.py                # full build
python3 build_video.py --draft        # same edit, fast low-quality encode, for checking timing
python3 build_video.py --only 9,10    # re-render parts 9 and 10 (0-based), reassemble
python3 build_video.py --stills       # only draw cards and captions into build/
python3 build_video.py --timeline     # only write build/timeline.md and the .srt; warns if a narration line is too long for its slot
```

A clip can also drop a stretch of its recording with `"skip": [["zoomed+0.4", "detail-0.2"]]`;
the two sides are dissolved together. The map scenes use it to cut the few
seconds that full-resolution tiles take to arrive.

Each build writes `build/timeline.md` (start and end of every part, its
captions and narration), which is always current. `storyboard.md` and
`narration.md` carry the times of the cut delivered here; after changing the
edit, update them from `build/timeline.md`. Colours, fonts and card layouts are at the
top of `build_video.py`.

## Narration and music

No voice-over is included. The only speech engines on this machine are eSpeak
and Flite, which are not presentation quality, and no paid service was used.
The video therefore carries the story in on-screen captions and has a silent
audio track.

To add sound, put files in `audio/` and rebuild:

- `audio/narration.wav` (or `.mp3`, `.m4a`) — a recording of `narration.md`. It is laid from 0:00, so record it against the timings in that file, or time it to `investor-demo.srt`.
- `audio/music.mp3` (or `.wav`, `.m4a`) — looped under the video, faded in and out, and lowered when narration is present. Use only music you hold a licence for; none is shipped here.

## Checking a result

```bash
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height,r_frame_rate -of default=nw=1 investor-demo.mp4
```

Expect H.264 1920×1080 at 30/1, an AAC stream, and a duration between two and
three minutes.

## Known limits

- **The change-detection example compares two processing runs of the same flight**, because that is the only pair of surveys in this deployment. The video says so on screen and in the narration. With two real survey dates, re-record `10_change` and reword its captions.
- Semantic segmentation, contours and hillshade exist as plugins but are disabled for this organization and have no results, so they appear by name only. See `discovery.md`.
- Basemap tiles come from the internet and stop at zoom 19; closer than that the map shows only the survey.
- Chrome does not cache anything served over the deployment's untrusted certificate, so every take refetches its map tiles. That is why the map scenes wait for tiles and the edit skips the wait.
- Recording was developed on a 2-core laptop with integrated graphics. On a faster machine the `slow` factors can be lowered; nothing else changes.
- Nothing here is committed to git by this workflow. `.env.local`, `.work/`, `.playwright-cli/` and `build/` are ignored. `recordings/` (about 150 MB) and the video (85 MB) are not; decide whether they belong in the repository before adding them.
