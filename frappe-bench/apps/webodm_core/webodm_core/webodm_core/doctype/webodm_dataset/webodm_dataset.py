import frappe
from frappe.model.document import Document
from frappe.utils import cint

from webodm_core import datasets


class DatasetInUse(frappe.ValidationError):
    """A task still references the dataset, so it cannot be deleted."""


class DatasetImagesFixed(frappe.ValidationError):
    """Images cannot be added to or removed from an existing dataset."""


class WebODMDataset(Document):
    def before_insert(self):
        # "Who created it" is a first-class field rather than only the standard
        # `owner`: the migration backfill runs as Administrator but records the
        # owner of the task it split the dataset out of.
        if not self.created_by:
            self.created_by = frappe.session.user

    def validate(self):
        self.title = (self.title or "").strip()
        if not self.title:
            frappe.throw("Dataset title is required")
        self._check_images_fixed()
        self._check_not_empty()
        self._recompute_summary()

    def _check_not_empty(self):
        # An input set with nothing in it cannot feed a task. The flag is for
        # the two callers that legitimately save an empty shell: the upload
        # path (rows are appended right after the insert, in the same request)
        # and the backfill of a legacy task that never had images.
        if self.flags.allow_empty or self.images:
            return
        if self.is_new() or self.flags.images_fixed is False:
            frappe.throw("A dataset needs at least one image")

    def _check_images_fixed(self):
        """Refuse any change to the image rows of an existing dataset.

        Inputs are frozen so every task that references a dataset processed the
        same photos; to change the inputs, create a new dataset. Title and
        description stay editable, and per-row bookkeeping (``storage_key``)
        is written with ``db_set`` and never goes through here.
        """
        if self.is_new() or self.flags.images_fixed is False:
            return
        before = self.get_doc_before_save()
        if before is None:
            return
        old = [(r.name, r.image) for r in before.get("images") or []]
        new = [(r.name, r.image) for r in self.get("images") or []]
        if old != new:
            frappe.throw(
                "The images of a dataset are fixed once it exists; create a new dataset to change the inputs",
                DatasetImagesFixed,
            )

    def _recompute_summary(self):
        rows = self.get("images") or []
        self.image_count = len(rows)
        self.total_size = sum(cint(r.file_size) for r in rows)

    def on_trash(self):
        # Frappe's own link check (LinkExistsError) would also stop the delete,
        # but only after on_trash — and with a generic message. Refuse here,
        # first, naming the tasks so the user knows what to remove.
        tasks = datasets.referencing_tasks(self.name)
        if tasks:
            frappe.throw(datasets.in_use_message(self, tasks), DatasetInUse)

        # Unreferenced: the images go with the dataset. Object storage copies
        # are deleted by the key on each row (works for datasets/<id>/... and
        # for migrated tasks/<task>/inputs/... keys alike); the on-disk blobs
        # are removed here (Frappe would keep one whose content hash another
        # File shares); the File rows themselves are dropped by Frappe after
        # on_trash.
        from webodm_core.storage import assets

        assets.delete_dataset_objects(self)
        datasets.remove_image_blobs(self)
        datasets.remove_thumbnails(self.name)
