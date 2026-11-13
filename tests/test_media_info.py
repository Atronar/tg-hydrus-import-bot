from io import BytesIO
import unittest

from PIL import Image

from media_info import MediaInfo, is_exact_duplicate, probe_media


def make_image(width: int = 100, height: int = 80) -> BytesIO:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "red").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


class ProbeMediaTests(unittest.TestCase):
    def test_image_from_file_like(self):
        buffer = make_image(120, 90)

        info = probe_media(buffer, "image/png")

        self.assertEqual(info.kind, "image")
        self.assertEqual((info.width, info.height), (120, 90))
        self.assertGreater(info.size, 0)

    def test_mime_with_parameters_is_understood(self):
        info = probe_media(make_image(), "image/png; charset=binary")

        self.assertEqual(info.kind, "image")

    def test_file_like_without_image_data_stays_document(self):
        info = probe_media(BytesIO(b"not an image"), "application/pdf")

        self.assertEqual(info.kind, "document")
        self.assertEqual(info.size, len(b"not an image"))

    def test_audio_file_like_keeps_audio_kind(self):
        info = probe_media(BytesIO(b"audio"), "audio/mpeg")

        self.assertEqual(info.kind, "audio")
        self.assertEqual(info.size, len(b"audio"))


class ExactDuplicateTests(unittest.TestCase):
    def test_image_is_exact_when_not_larger(self):
        new = MediaInfo("image", 100, 100, 80)
        existing = {"width": 100, "height": 80}

        self.assertTrue(is_exact_duplicate(new, existing))

    def test_image_is_not_exact_when_larger(self):
        new = MediaInfo("image", 100, 120, 90)
        existing = {"width": 100, "height": 80}

        self.assertFalse(is_exact_duplicate(new, existing))

    def test_image_without_existing_dimensions_is_not_exact(self):
        new = MediaInfo("image", 100, 100, 80)
        existing = {"width": None, "height": None}

        self.assertFalse(is_exact_duplicate(new, existing))

    def test_video_requires_same_duration_and_not_larger(self):
        new = MediaInfo("video", 100, 640, 480, 10.2)
        existing = {"width": 640, "height": 480, "duration": 10.0}

        self.assertTrue(is_exact_duplicate(new, existing))

    def test_video_with_different_duration_is_not_exact(self):
        new = MediaInfo("video", 100, 640, 480, 12.0)
        existing = {"width": 640, "height": 480, "duration": 10.0}

        self.assertFalse(is_exact_duplicate(new, existing))

    def test_audio_requires_same_duration(self):
        new = MediaInfo("audio", 100, duration=10.4)
        existing = {"duration": 10.0}

        self.assertTrue(is_exact_duplicate(new, existing))

    def test_audio_without_duration_is_not_exact(self):
        new = MediaInfo("audio", 100)
        existing = {"duration": 10.0}

        self.assertFalse(is_exact_duplicate(new, existing))

    def test_document_requires_same_size(self):
        new = MediaInfo("document", 1234)
        existing = {"size": 1234}

        self.assertTrue(is_exact_duplicate(new, existing))
        self.assertFalse(
            is_exact_duplicate(new, {"size": 1235})
        )
        self.assertFalse(
            is_exact_duplicate(new, {"size": None})
        )


if __name__ == "__main__":
    unittest.main()
