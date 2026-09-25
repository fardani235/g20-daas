# On-Demand Processing — User Guide

*For anyone who uploads imagery and presses **Start**. No knowledge of the
architecture needed.*

## What changed

Until now every task ran on one processing engine that lived permanently next
to the web app, and everything it produced was kept on that same machine.

With on-demand processing, pressing **Start** rents a processing machine for
your task, runs the photogrammetry there, collects the results and gives the
machine back. Your imagery and results are kept in object storage (S3), and the
web app keeps a local copy of whatever is being viewed or processed so maps and
3D models stay fast.

From where you sit the workflow is the same: upload, press Start, wait, look at
the results. Three things look a little different, described below.

## The task states

| State | What it means | How long |
|---|---|---|
| **Pending** | Uploaded and parked. Nothing happens until you press Start. | Until you act |
| **Queued** | You pressed Start. The system is choosing where to run the task. | Seconds, normally |
| **Provisioning** *(new)* | A processing machine is being started for this task. The task row shows *starting node…* instead of a percentage. | Usually 1–5 minutes (see below) |
| **Running** | Photogrammetry is running. The progress bar is live. | Minutes to hours, depends on the dataset |
| **Completed** | Results are ready: orthophoto, elevation models, point cloud, 3D model — whichever you asked for. | — |
| **Failed** | Something went wrong. The card shows the reason; the console has the details. Press Start to retry. | — |
| **Cancelled** | You cancelled it. Any machine started for it is released. | — |

**Provisioning** is the only new state. It is there because the machine that
will run your task does not exist yet when you press Start. If your deployment
has *no* on-demand provider configured, you will never see it — tasks go
straight from Queued to Running on the built-in engine, exactly as before.

You can **cancel** a task while it is Provisioning. The machine is released and
nothing is billed beyond the minutes it took to start.

## How long does Provisioning take?

It depends on how the machine image was prepared:

* **Prebaked image** (the recommended setup): about a minute. The machine
  boots with the processing engine already installed and only has to start it.
* **Stock image**: three to five minutes. The engine is installed on first
  boot.

Add to that the time to upload your imagery to the new machine, which is part
of the *Queued → Running* transition and scales with the size of your dataset
and the connection between the app host and the cloud.

If a machine does not come up within the deployment's provisioning timeout
(15 minutes by default) the task goes back to **Queued** and tries again a few
minutes later, with increasing gaps between attempts. After several failed
attempts the task is marked **Failed** with the reason. This is deliberate: a
task never sits in Provisioning forever.

## Which machine does my task get?

You do not pick a machine, a provider or a region. The system chooses a
machine **class** from your task's processing options:

* the standard class (`cpu`) for most tasks;
* a larger class (`cpu-large`) when you selected *ultra* feature or
  point-cloud quality, or when the task has a very large number of images
  (the threshold is set by your administrator, 500 images by default).

GPU classes are not available yet. When they are, the same rule will pick them
for the options that benefit — you still will not have to choose.

## What if there is no machine available?

Each organization, and the deployment as a whole, has a cap on how many
on-demand machines can run at once. If the cap is reached your task waits in
**Queued** (the card shows *organization compute cap reached* or *global compute
cap reached* in its status line) and is retried every minute until a slot frees
up. Waiting for capacity does not count against the task's retry budget.

If the on-demand service itself is unavailable, the task falls back to the
built-in processing engine if one is configured — you may notice it is slower
than usual, but the result is the same. If neither is available the task
retries with increasing gaps and eventually fails with *no processing nodes
configured*.

## Where do my results live?

Results are stored in your organization's area of the object store. Nobody
outside your organization can address them. The web app keeps a local copy of
results you look at, so:

* opening a map layer or the 3D viewer for a result you (or a colleague) looked
  at recently is as fast as before;
* opening one nobody has touched for a while is **slower the first time** —
  the copy is fetched from object storage, then it is fast again. Map tiles
  render directly from object storage in the meantime, so the map is never
  blank, just slower to sharpen;
* a result is **never lost** because the local copy was cleaned up. The
  object store is the source of truth; the local copy is a cache.

The same is true of your uploaded imagery: it is kept in object storage, so a
task can be **re-processed** (press Start on a Completed, Failed or Cancelled
task) long after the local copy has been cleaned up. Re-processing replaces
the previous results: the old orthophoto, elevation models, point cloud and
model are removed the moment you press Start, and the task shows the new ones
when it completes.

Downloads (the orthophoto, point cloud, 3D model links) work the same way: a
download of a cold result takes a moment longer to start.

## Analysis plugins

Plugins (hillshade, contours, segmentation, 3D reconstruction, your own
uploads) work exactly as before. Their inputs are fetched from object storage
when needed and their outputs are stored there too, with the same cold/warm
behaviour described above.

## Things worth knowing

* **Cost.** Each on-demand machine is billed by the minute while it exists.
  Machines are started only when a task needs one and destroyed as soon as the
  task ends — Completed, Failed or Cancelled alike. If you are not going to
  need a result, cancel the task rather than letting it finish.
* **The console** shows the processing engine's live output while a task is
  Running, from whichever machine it runs on. During Provisioning there is no
  output yet.
* **A machine dying mid-run** (rare) is detected within a few minutes; the
  task is marked Failed with the reason and can be restarted.
* **Deleting a task** deletes its imagery and results from object storage and
  releases any machine still associated with it.
