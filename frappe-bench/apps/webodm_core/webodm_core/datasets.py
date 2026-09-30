"""The dataset library: reusable, organization-scoped input image sets.

A ``WebODM Dataset`` owns the uploaded imagery (``WebODM Dataset Image``
rows + their private ``File`` attachments + the object storage copies); a
``WebODM Task`` only *references* a dataset, so several tasks can process
the same photos without re-uploading them. This module holds everything
that is not an HTTP endpoint or a DocType hook:

* EXIF extraction (``extract_photo_meta``) — unchanged from the task upload,
  it reads the raw bytes before anything could strip the tags;
* ``create_from_uploads`` — the single way a dataset with images comes into
  existence from user input (the Datasets page and the task dialog both
  land here);
* summaries / serialisation shared by the API and the task-progress payload;
* ``referencing_tasks`` — the guard that keeps a dataset alive while a task
  still uses it;
* on-demand thumbnails, so the map view never has to pull multi-megabyte
  originals to show a 72 px preview.
"""

from __future__ import annotations

import io
import os

import frappe
from frappe.utils import cint
from PIL import Image, ImageOps
from PIL.ExifTags import GPSTAGS

from webodm_core.plugins.files import abs_path_for_file_doc, save_private_file_from_stream

DOCTYPE = "WebODM Dataset"
IMAGE_DOCTYPE = "WebODM Dataset Image"

# Thumbnail sizes the endpoint will produce (longest edge, px). A fixed set
# keeps the on-disk cache bounded and the URLs cache-friendly.
THUMBNAIL_SIZES = (128, 256, 512)
DEFAULT_THUMBNAIL_SIZE = 256
THUMBNAIL_QUALITY = 78

IMAGE_FIELDS = ("name", "image", "filename", "file_size", "storage_key",
                "latitude", "longitude", "altitude", "capture_time")


# -- EXIF ---------------------------------------------------------------------


def gps_to_decimal(dms, ref):
    deg, min_, sec = dms
    decimal = float(deg) + float(min_) / 60 + float(sec) / 3600
    if ref in ("S", "W"):
        decimal = -decimal
    return round(decimal, 6)


def extract_photo_meta(source):
    """Extract georeferencing/timing metadata from an image's EXIF.

    ``source`` is raw ``bytes`` or an on-disk path. Pillow reads only the
    headers it needs for ``getexif()``, so passing a path never loads the
    full image into memory.

    Returns a dict with keys ``lat``, ``lng``, ``altitude`` (metres, signed),
    and ``capture_time`` (Frappe ``YYYY-MM-DD HH:MM:SS`` string). Every field is
    independently optional: a missing or malformed tag yields ``None`` and never
    raises, so a bad tag can never block an upload.
    """
    meta = {"lat": None, "lng": None, "altitude": None, "capture_time": None}

    try:
        with Image.open(io.BytesIO(source) if isinstance(source, bytes) else source) as img:
            exif = img.getexif()
    except Exception:
        return meta
    if not exif:
        return meta

    # --- GPS: latitude / longitude / altitude (GPS IFD 34853) ---
    try:
        gps_ifd = exif.get_ifd(34853)
        if gps_ifd:
            gps_info = {}
            for k, v in gps_ifd.items():
                tag = GPSTAGS.get(k)
                if tag:
                    gps_info[tag] = v

            if "GPSLatitude" in gps_info and "GPSLongitude" in gps_info:
                meta["lat"] = gps_to_decimal(gps_info["GPSLatitude"], gps_info.get("GPSLatitudeRef", "N"))
                meta["lng"] = gps_to_decimal(gps_info["GPSLongitude"], gps_info.get("GPSLongitudeRef", "E"))

            if "GPSAltitude" in gps_info:
                try:
                    alt = float(gps_info["GPSAltitude"])
                    ref = gps_info.get("GPSAltitudeRef", 0)
                    # GPSAltitudeRef == 1 (or b"\x01") means below sea level.
                    if ref in (1, b"\x01"):
                        alt = -alt
                    meta["altitude"] = round(alt, 3)
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    # --- Capture time: DateTimeOriginal (Exif IFD), then DateTime (base IFD) ---
    try:
        dto = None
        exif_ifd = exif.get_ifd(34665)  # ExifIFD
        if exif_ifd:
            dto = exif_ifd.get(36867)  # DateTimeOriginal
        if not dto:
            dto = exif.get(306)  # DateTime
        if dto:
            # EXIF "YYYY:MM:DD HH:MM:SS" -> Frappe "YYYY-MM-DD HH:MM:SS".
            s = str(dto).strip()
            date_part, _, time_part = s.partition(" ")
            date_part = date_part.replace(":", "-")
            candidate = (date_part + " " + time_part).strip()
            # Only keep a value Frappe's Datetime field can actually store, so a
            # malformed-but-truthy tag can never raise at save() and block the
            # whole upload batch. Unparseable -> leave capture_time None.
            from frappe.utils import get_datetime

            get_datetime(candidate)
            meta["capture_time"] = candidate
    except Exception:
        pass

    return meta


def image_row_from_meta(file_doc, file_name: str, meta: dict) -> dict:
    """The ``WebODM Dataset Image`` row for a saved File plus its EXIF metadata."""
    row = {"image": file_doc.file_url, "filename": file_name, "file_size": file_doc.file_size}
    if meta["lat"] is not None and meta["lng"] is not None:
        row["latitude"] = meta["lat"]
        row["longitude"] = meta["lng"]
    if meta["altitude"] is not None:
        row["altitude"] = meta["altitude"]
    if meta["capture_time"]:
        row["capture_time"] = meta["capture_time"]
    return row


# -- creation -----------------------------------------------------------------


def save_image_file(stream, file_name: str, dataset_name: str):
    """Save an uploaded image as a private File with its bytes untouched.

    ODM georeferencing depends on per-image EXIF GPS. Frappe's ``File.save_file``
    strips EXIF from JPEGs when ``strip_exif_metadata_from_uploaded_images`` is
    on, which removes the geotags and collapses the reconstruction to a tiny
    local model. Streaming the upload straight to disk and registering the File
    against the existing blob bypasses ``save_file`` entirely, so the setting
    cannot affect dataset images — and the file is never held in memory.
    """
    if hasattr(stream, "seek"):
        try:
            stream.seek(0)
        except (OSError, ValueError):
            pass
    return save_private_file_from_stream(
        stream, file_name, attached_to_doctype=DOCTYPE, attached_to_name=dataset_name,
    )


def create_from_uploads(files, *, title: str, description: str | None = None):
    """Create a dataset from werkzeug ``FileStorage`` uploads. Returns the saved doc.

    The dataset row is inserted first so the Files can be attached to its real
    name, then each upload is streamed to disk, its EXIF read from the stored
    bytes, and the image rows are saved in one go. If anything fails midway the
    half-built dataset is removed again (Frappe drops its attachments with it),
    so the library never shows a dataset that does not match its files. A
    request with no files is refused before anything is written; an empty
    dataset is meaningless (``WebODMDataset.validate`` guards the same rule).

    Once saved the images are copied to object storage in the background when
    a bucket is configured; dispatch syncs anything still missing, so nothing
    depends on that job having finished.
    """
    files = [f for f in (files or []) if f is not None]
    if not files:
        frappe.throw("A dataset needs at least one image", frappe.ValidationError)
    title = (title or "").strip()
    if not title:
        frappe.throw("title is required", frappe.ValidationError)

    dataset = frappe.get_doc({"doctype": DOCTYPE, "title": title, "description": description or None})
    dataset.flags.allow_empty = True  # rows are appended after the insert below
    dataset.insert()

    try:
        for f in files:
            file_name = f.filename or f"unnamed_{frappe.generate_hash()[:6]}.jpg"
            # Werkzeug has already spooled large parts to a temp file; stream that
            # to its final location instead of f.read()-ing it into memory.
            file_doc = save_image_file(f.stream, file_name, dataset.name)
            meta = extract_photo_meta(abs_path_for_file_doc(file_doc))
            dataset.append("images", image_row_from_meta(file_doc, file_name, meta))
        dataset.flags.allow_empty = False
        dataset.flags.images_fixed = False  # first (and only) time rows are added
        dataset.save()
    except Exception:
        frappe.delete_doc(DOCTYPE, dataset.name, ignore_permissions=True, force=True, delete_permanently=True)
        raise

    schedule_input_sync(dataset)
    return dataset


def schedule_input_sync(dataset):
    """Kick off the background copy of the dataset's images to object storage."""
    from webodm_core import storage

    if storage.configured():
        from webodm_core.storage import assets as storage_assets
        storage_assets.enqueue_input_sync(dataset.name)


# -- queries / serialisation --------------------------------------------------


def referencing_tasks(dataset_name: str) -> list[dict]:
    """``[{name, title, status, project}]`` for every task that points at the dataset."""
    return frappe.get_all(
        "WebODM Task", filters={"dataset": dataset_name},
        fields=["name", "title", "status", "project"], order_by="creation asc",
    )


def in_use_message(dataset, tasks: list[dict]) -> str:
    names = ", ".join(f"\"{t.title or t.name}\"" for t in tasks)
    return (f"Dataset \"{dataset.title}\" is still used by {len(tasks)} task(s): {names}. "
            "Delete those tasks first.")


def summary(dataset) -> dict:
    """The compact block a task payload / list row carries about its dataset."""
    return {
        "name": dataset.name,
        "title": dataset.title,
        "description": dataset.description,
        "image_count": cint(dataset.image_count),
        "total_size": cint(dataset.total_size),
        "created_by": dataset.created_by or dataset.owner,
        "creation": str(dataset.creation) if dataset.creation else None,
        "modified": str(dataset.modified) if dataset.modified else None,
    }


def image_dict(dataset_name: str, row) -> dict:
    """A dataset image row as the frontend consumes it (plus a thumbnail URL)."""
    out = {f: row.get(f) for f in IMAGE_FIELDS}
    out["capture_time"] = str(out["capture_time"]) if out["capture_time"] else None
    out["thumbnail"] = thumbnail_url(dataset_name, row.name)
    return out


def image_dicts(dataset) -> list[dict]:
    return [image_dict(dataset.name, row) for row in dataset.get("images") or []]


def task_image_count(task) -> int:
    """How many images a task will process (its dataset's count; 0 without one)."""
    name = getattr(task, "dataset", None)
    if not name or not isinstance(name, str):
        return 0
    return cint(frappe.db.get_value(DOCTYPE, name, "image_count") or 0)


# -- thumbnails ----------------------------------------------------------------


def thumbnail_url(dataset_name: str, image_name: str, size: int = DEFAULT_THUMBNAIL_SIZE) -> str:
    return (f"/api/method/webodm_core.api.dataset.thumbnail?dataset={dataset_name}"
            f"&image={image_name}&size={normalize_thumbnail_size(size)}")


def normalize_thumbnail_size(size) -> int:
    """Snap a requested size to the nearest allowed one (never larger than the max)."""
    wanted = cint(size) or DEFAULT_THUMBNAIL_SIZE
    for allowed in THUMBNAIL_SIZES:
        if wanted <= allowed:
            return allowed
    return THUMBNAIL_SIZES[-1]


def thumbnails_dir(dataset_name: str) -> str:
    # Derived data, not canonical: lives next to private/files but is not a
    # File, so eviction never touches it and it is simply regenerated on a miss.
    return frappe.get_site_path("private", "thumbnails", frappe.scrub(dataset_name))


def thumbnail_path(dataset_name: str, image_name: str, size: int) -> str:
    return os.path.join(thumbnails_dir(dataset_name), f"{frappe.scrub(image_name)}_{int(size)}.jpg")


def ensure_thumbnail(dataset, row, size: int = DEFAULT_THUMBNAIL_SIZE) -> str:
    """Absolute path of a cached JPEG thumbnail for one dataset image; built on a miss.

    The original is resolved cache-first / S3-second like every other blob
    (``cache.ensure_local``), then decoded with Pillow's JPEG *draft* mode,
    which asks the decoder for the smallest DCT scale that still covers the
    target size — a 20 MP drone photo is reduced 1/8 while decoding instead of
    being fully decoded and then resized, so a cold thumbnail costs tens of
    milliseconds, not seconds. EXIF orientation is honoured; the output is a
    plain baseline JPEG without metadata.
    """
    from webodm_core.storage import cache

    size = normalize_thumbnail_size(size)
    dest = thumbnail_path(dataset.name, row.name, size)
    if os.path.isfile(dest):
        return dest

    source = cache.ensure_local(row.image, row.get("storage_key"), dataset.organization)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = f"{dest}.{frappe.generate_hash()[:6]}.part"
    try:
        with Image.open(source) as img:
            img.draft("RGB", (size, size))
            img = ImageOps.exif_transpose(img)
            img.thumbnail((size, size))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(part, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
        os.replace(part, dest)
    except Exception:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return dest


def remove_image_blobs(dataset) -> int:
    """Delete the on-disk (cache) copy of each image of ``dataset``. Returns files removed.

    Frappe drops the attached ``File`` rows when the dataset is deleted, but
    ``File._delete_file_on_disk`` keeps the blob whenever another File shares
    the same ``content_hash`` — it assumes content-hash dedup. Our streamed
    uploads never dedup (every File gets its own, uniquely named blob, see
    ``plugins.files``), so the same photo uploaded into two datasets would
    leave an orphan on disk after either is deleted, invisible to eviction.
    Each row's blob is checked to be attached to *this* dataset before it is
    removed; Frappe's own pass afterwards tolerates a missing file.
    """
    from webodm_core.plugins.files import abs_path_for_attached_file

    removed = 0
    for row in dataset.get("images") or []:
        if not row.image:
            continue
        try:
            path = abs_path_for_attached_file(row.image, attached_to_doctype=DOCTYPE, attached_to_name=dataset.name)
        except (frappe.DoesNotExistError, frappe.PermissionError):
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def remove_thumbnails(dataset_name: str) -> int:
    """Drop the cached thumbnails of a dataset (on delete). Returns files removed."""
    folder = thumbnails_dir(dataset_name)
    removed = 0
    if not os.path.isdir(folder):
        return 0
    for entry in os.listdir(folder):
        try:
            os.remove(os.path.join(folder, entry))
            removed += 1
        except OSError:
            pass
    try:
        os.rmdir(folder)
    except OSError:
        pass
    return removed
