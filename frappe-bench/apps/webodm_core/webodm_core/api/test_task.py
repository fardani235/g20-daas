import io
import unittest

from PIL import Image

from webodm_core.api.task import _gps_to_decimal, _extract_photo_meta


def _plain_jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (120, 120, 120)).save(buf, format="JPEG")
    return buf.getvalue()


def _jpeg_with_datetime(value="2024:05:01 10:20:30"):
    im = Image.new("RGB", (8, 8), (10, 20, 30))
    exif = im.getexif()
    exif[306] = value  # DateTime (base IFD) — reliably round-trips through PIL
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class TestExtractPhotoMeta(unittest.TestCase):
    def test_gps_to_decimal_north(self):
        self.assertAlmostEqual(_gps_to_decimal((40, 34, 30), "N"), 40.575, places=3)

    def test_gps_to_decimal_west_is_negative(self):
        self.assertAlmostEqual(_gps_to_decimal((73, 30, 0), "W"), -73.5, places=3)

    def test_plain_image_yields_all_none(self):
        meta = _extract_photo_meta(_plain_jpeg())
        self.assertEqual(
            meta,
            {"lat": None, "lng": None, "altitude": None, "capture_time": None},
        )

    def test_capture_time_parsed_to_frappe_datetime(self):
        meta = _extract_photo_meta(_jpeg_with_datetime())
        self.assertEqual(meta["capture_time"], "2024-05-01 10:20:30")

    def test_garbage_bytes_never_raise(self):
        meta = _extract_photo_meta(b"not an image")
        self.assertEqual(meta["lat"], None)
        self.assertEqual(meta["capture_time"], None)

    def test_malformed_datetime_degrades_to_none(self):
        # A truthy-but-unparseable DateTime tag must not survive into the row,
        # or task.save() would raise on the Datetime field and block the batch.
        meta = _extract_photo_meta(_jpeg_with_datetime("not-a-date"))
        self.assertIsNone(meta["capture_time"])


if __name__ == "__main__":
    unittest.main()
