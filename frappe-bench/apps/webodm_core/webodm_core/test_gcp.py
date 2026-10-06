"""The GCP file parser: ODM's format, strictly shaped, leniently valued."""

import unittest

from webodm_core import gcp

GOOD = """\
+proj=utm +zone=33 +ellps=WGS84 +datum=WGS84 +units=m +no_defs
# measured 2026-09-30
544256.7 5320919.9 5.1 3044 2622 IMG_0001.JPG gcp1
544257.7 5320920.9 5.2 3144 2722 IMG_0002.JPG gcp2
544257.7 5320920.9 5.2 1144 1722 IMG_0003.JPG gcp2 extra1 extra2

544258.7\t5320921.9\t5.3\t3244\t2822\tIMG_0003.JPG
"""


class TestParse(unittest.TestCase):
    def test_parses_crs_points_labels_and_images(self):
        info = gcp.parse(GOOD)
        self.assertEqual(info.crs, "+proj=utm +zone=33 +ellps=WGS84 +datum=WGS84 +units=m +no_defs")
        self.assertEqual(len(info.points), 4)
        self.assertEqual(info.points[0].geo, (544256.7, 5320919.9, 5.1))
        self.assertEqual(info.points[0].pixel, (3044.0, 2622.0))
        self.assertEqual(info.points[0].image, "IMG_0001.JPG")
        self.assertEqual(info.points[0].label, "gcp1")
        self.assertIsNone(info.points[3].label)  # the 7th column is optional
        self.assertEqual(info.points[3].line, 7)  # blank + comment lines keep their numbers
        self.assertEqual(info.images, ["IMG_0001.JPG", "IMG_0002.JPG", "IMG_0003.JPG"])
        self.assertEqual(info.labels, ["gcp1", "gcp2"])

    def test_accepts_epsg_and_named_crs_lines(self):
        for crs in ("EPSG:32633", "WGS84 UTM 33N", "epsg:4326"):
            info = gcp.parse(f"{crs}\n1 2 3 4 5 a.jpg\n")
            self.assertEqual(info.crs, crs)

    def test_bytes_with_bom_and_crlf(self):
        data = b"\xef\xbb\xbfEPSG:32633\r\n1 2 3 4 5 a.jpg\r\n"
        self.assertEqual(gcp.parse(data).images, ["a.jpg"])

    def test_summary_reports_unknown_images_and_few_points(self):
        info = gcp.parse("EPSG:32633\n1 2 3 4 5 a.jpg p1\n1 2 3 4 5 b.jpg p2\n")
        s = info.summary(known_images=["a.jpg", "c.jpg"])
        self.assertEqual(s["points"], 2)
        self.assertEqual(s["labels"], 2)
        self.assertEqual(s["unknown_images"], ["b.jpg"])
        self.assertTrue(s["few_points"])  # 2 labelled points < 3
        full = gcp.parse(GOOD).summary()
        self.assertNotIn("unknown_images", full)
        self.assertTrue(full["few_points"])  # only 2 distinct labels
        many = gcp.parse("EPSG:32633\n" + "".join(f"1 2 3 4 5 a.jpg p{i}\n" for i in range(5)))
        self.assertFalse(many.summary()["few_points"])

    def test_unknown_images_is_exact_match_like_odm(self):
        info = gcp.parse("EPSG:32633\n1 2 3 4 5 IMG_0001.jpg\n")
        self.assertEqual(info.unknown_images({"IMG_0001.JPG"}), ["IMG_0001.jpg"])


class TestRefusals(unittest.TestCase):
    def _refuses(self, text, fragment, line=None):
        with self.assertRaises(gcp.GCPFormatError) as ctx:
            gcp.parse(text)
        self.assertIn(fragment, str(ctx.exception))
        if line is not None:
            self.assertEqual(ctx.exception.line, line)
            self.assertTrue(str(ctx.exception).startswith(f"line {line}:"))

    def test_empty_and_binary_and_oversized(self):
        self._refuses(b"", "empty")
        self._refuses(b"   \n\n", "empty")
        self._refuses(b"EPSG:1\n1 2 3 4 5 a.jpg\x00", "binary")
        self._refuses(b"x" * (gcp.MAX_GCP_BYTES + 1), "larger")

    def test_missing_crs_line(self):
        self._refuses("544256.7 5320919.9 5 3044 2622 IMG_0001.JPG\n", "first line must be the coordinate", line=1)

    def test_point_lines_need_six_columns_with_five_numbers(self):
        self._refuses("EPSG:32633\n1 2 3 4 a.jpg\n", "at least 6 columns", line=2)
        self._refuses("EPSG:32633\n1 2 x 4 5 a.jpg\n", "geo_z is not a number", line=2)
        self._refuses("EPSG:32633\n1 2 3 4 5 6\n", "sixth column must be the image", line=2)

    def test_crs_only_file(self):
        self._refuses("EPSG:32633\n# nothing measured yet\n", "contains no points")

    def test_a_geo_txt_is_not_a_gcp_file(self):
        # ODM's image-geolocation sidecar starts each line with the image name;
        # NodeODM would read it as --geo, not --gcp, so refuse it here.
        self._refuses("EPSG:4326\nIMG_0001.JPG 13.4 52.5 100\n", "at least 6 columns", line=2)
        self._refuses("EPSG:4326\nIMG_0001.JPG 13.4 52.5 100 0 0\n", "geo_x is not a number", line=2)

    def test_a_photo_is_refused_without_decoding_it(self):
        jpeg_head = b"\xff\xd8\xff\xe1\x00\x16Exif\x00\x00MM\x00*" + b"\x00" * 64
        self._refuses(jpeg_head, "binary")


class TestConstants(unittest.TestCase):
    def test_upload_name_is_a_txt_nodeodm_treats_as_gcp(self):
        self.assertTrue(gcp.UPLOAD_NAME.endswith(".txt"))
        self.assertNotEqual(gcp.UPLOAD_NAME.lower(), "geo.txt")
        self.assertFalse(gcp.UPLOAD_NAME.lower().startswith("align."))


if __name__ == "__main__":
    unittest.main()
