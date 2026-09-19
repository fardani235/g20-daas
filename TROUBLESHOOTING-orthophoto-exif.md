# Troubleshooting: tiny orthophoto output

## Symptom

A task completes successfully, but the orthophoto is tiny — tens of pixels wide
(e.g. 40×45 px, a few KB) instead of thousands of pixels. On the map the image
markers appear in the correct place, but the raster overlay is a speck. The same
dataset produces a full-size orthophoto in upstream WebODM.

## Root cause

ODM georeferences the reconstruction from each source image's **EXIF GPS tags**.
When the geotags are missing, ODM cannot place the images in world coordinates
and falls back to a small arbitrary local model anchored near origin/null-island
(e.g. bounds `0,0`–`4,-4.5` in a UTM CRS). That collapsed model is what produces
the tiny orthophoto.

The geotags were being removed **during upload into Frappe**:

1. `webodm_core.api.task.upload_images` reads GPS from the *in-memory* upload
   bytes and stores it on the `WebODM Task Image` rows — so the DB and the map
   markers are correct. This masks the problem.
2. `File.save()` then runs Frappe's `save_file()`, which for `image/jpeg` with
   the system setting `strip_exif_metadata_from_uploaded_images` **enabled**
   rewrites the JPEG with `exif=b""`, deleting all EXIF (and the XMP/APP1
   marker).
3. `task_runner` sends the now-GPS-less file to NodeODM (byte-identical to
   what's on disk), ODM can't georeference → tiny orthophoto.

### How it was diagnosed

- The on-disk image and the copy received by NodeODM had identical md5 → our
  transfer wasn't the culprit; the file was already stripped before sending.
- A JPEG marker scan showed **no APP1 segment at all** (EXIF + XMP gone), leaving
  only JFIF — the signature of a re-encode, not a truncation.
- The DB image rows *did* have real lat/lng, proving GPS existed at upload time
  and was removed afterward.
- `strip_exif_metadata_from_uploaded_images` was found set to `1`.

## Fix

Two independent defenses (either alone is sufficient; both are in place):

1. **Disable the site setting**

   ```bash
   bench --site <site> execute frappe.client.set_value --kwargs \
     "{'doctype':'System Settings','name':'System Settings',\
       'fieldname':'strip_exif_metadata_from_uploaded_images','value':0}"
   ```

2. **Uploader hardening** — `webodm_core/api/task.py::_save_task_image_file`
   compares the bytes written to disk against the original upload and, if they
   differ, rewrites the untouched original and repairs the File's `content_hash`
   and `file_size`. This keeps task images intact regardless of the global
   setting, so the bug can't regress if someone re-enables it.

### Verify the fix

Save an image with EXIF through Frappe's File pipeline and confirm the on-disk
file still carries the `Exif\x00` APP1 marker and matches the source size
byte-for-byte. A freshly uploaded task image should be the full original size
(multi-MB for drone JPEGs), not a shrunken re-encode.

## Re-running affected tasks

Tasks uploaded **before** the fix have GPS-stripped images on disk and will keep
producing tiny output — the images cannot be recovered in place. To fix such a
task, re-upload the **original** images (with EXIF intact) and reprocess:

1. Re-upload originals via the MapView upload panel (or `upload_images`) — this
   creates a new task whose images retain EXIF.
2. Start processing; NodeODM will now receive geotagged images.
3. On completion, confirm the orthophoto is full-size and georeferenced
   (real CRS/UTM bounds, not origin-anchored), and that the map overlay renders.

## Related

- `frappe-bench/apps/webodm_core/README.md` — pipeline + EXIF requirement.
- `services/geospatial/README.md` — COG conversion and tile serving.
