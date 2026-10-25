import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as app


class Response(io.BytesIO):
    def __init__(self, content_type, disposition=""):
        super().__init__(b"media")
        self.headers = {"Content-Type": content_type, "Content-Length": "5",
                        "Content-Disposition": disposition}


class ResponseTypeTests(unittest.TestCase):
    def test_batch_uses_download_response_without_separate_probe(self):
        for content_type, disposition, expected in (
            ("video/mp4", "", "01.mp4"),
            ("application/octet-stream", 'attachment; filename="x.PNG"', "01.png"),
            ("application/octet-stream", 'attachment; filename="x.desktop"', "01.bin"),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                response = Response(content_type, disposition)
                with patch.object(app, "_open_drive_url", return_value=response) as opener:
                    count, results = app.download_batch(["abcdefghij"], 1, directory, zero_pad=2)
                self.assertEqual(count, 1, results)
                self.assertEqual(results[0][2], expected)
                self.assertEqual((Path(directory) / expected).read_bytes(), b"media")
                self.assertEqual(opener.call_count, 1)
                self.assertTrue(response.closed)

    def test_confirmation_uses_final_response_type(self):
        confirmation = io.BytesIO(b"confirm=token")
        confirmation.headers = {"Content-Type": "text/html"}
        media = Response("video/webm")
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, "_open_drive_url", side_effect=[confirmation, media]):
                count, results = app.download_batch(["abcdefghij"], 1, directory)
            self.assertEqual(count, 1, results)
            self.assertEqual(results[0][2], "1.webm")
            self.assertTrue(confirmation.closed)
            self.assertTrue(media.closed)

    def test_failed_probe_does_not_claim_jpeg(self):
        with patch.object(app, "_open_drive_url", side_effect=OSError("offline")):
            self.assertEqual(app.probe_ext("abcdefghij"), ".bin")
