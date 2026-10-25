"""SVG requires an affirmative confirmation before writing a downloaded file."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as app


class Response(io.BytesIO):
    def __init__(self, body=b"<svg/>", headers=None):
        super().__init__(body)
        self.headers = headers or {"Content-Type": "image/svg+xml", "Content-Length": str(len(body))}


class SvgConfirmationTests(unittest.TestCase):
    def test_missing_or_denied_confirmation_writes_nothing(self):
        for confirm in (None, Mock(return_value=False)):
            with self.subTest(confirm=confirm), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                response = Response()
                with patch.object(app, "_open_drive_url", return_value=response):
                    result = app.download_file(
                        "abcdefghij", str(output / "01.bin"), infer_extension=True,
                        svg_confirm=confirm,
                    )
                self.assertEqual(result, (False, app._SVG_CANCEL_MESSAGE))
                self.assertEqual(list(output.iterdir()), [])
                self.assertTrue(response.closed)

    def test_affirmative_confirmation_allows_svg(self):
        confirm = Mock(return_value=True)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, "_open_drive_url", return_value=Response()):
                ok, info = app.download_file(
                    "abcdefghij", str(Path(directory) / "01.bin"),
                    infer_extension=True, svg_confirm=confirm,
                )
            self.assertTrue(ok, info)
            self.assertEqual(Path(info).name, "01.svg")
            self.assertEqual(Path(info).read_bytes(), b"<svg/>")
            confirm.assert_called_once_with()

    def test_final_response_after_drive_confirmation_is_checked(self):
        first = Response(b"confirm=12345", {"Content-Type": "text/html"})
        final = Response()
        confirm = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, "_open_drive_url", side_effect=[first, final]):
                result = app.download_file(
                    "abcdefghij", str(Path(directory) / "01.bin"),
                    infer_extension=True, svg_confirm=confirm,
                )
            self.assertEqual(result, (False, app._SVG_CANCEL_MESSAGE))
            self.assertEqual(list(Path(directory).iterdir()), [])
            confirm.assert_called_once_with()
            self.assertTrue(first.closed)
            self.assertTrue(final.closed)

    def test_mime_svg_requires_confirmation_even_if_named_jpeg(self):
        confirm = Mock(return_value=False)
        response = Response(headers={
            "Content-Type": "image/svg+xml",
            "Content-Disposition": 'attachment; filename="photo.jpg"',
        })
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, "_open_drive_url", return_value=response):
                result = app.download_file(
                    "abcdefghij", str(Path(directory) / "01.bin"),
                    infer_extension=True, svg_confirm=confirm,
                )
            self.assertEqual(result, (False, app._SVG_CANCEL_MESSAGE))
            self.assertEqual(list(Path(directory).iterdir()), [])
            confirm.assert_called_once_with()

    def test_filename_svg_requires_confirmation_with_generic_mime(self):
        response = Response(headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="drawing.svg"',
        })
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, "_open_drive_url", return_value=response):
                result = app.download_file("abcdefghij", str(Path(directory) / "01.bin"))
            self.assertEqual(result, (False, app._SVG_CANCEL_MESSAGE))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_batch_passes_svg_callback(self):
        confirm = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, "download_file", return_value=(False, app._SVG_CANCEL_MESSAGE)) as drive:
                app.download_batch(["abcdefghij"], 1, directory, svg_confirm=confirm)
            self.assertIs(drive.call_args.kwargs["svg_confirm"], confirm)


if __name__ == "__main__":
    unittest.main()
