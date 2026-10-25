"""Exercise yt-dlp's real error reporting without making a network request."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as app

try:
    from yt_dlp.extractor.facebook import FacebookIE
    from yt_dlp.utils import ExtractorError
except ImportError:
    FacebookIE = None


@unittest.skipIf(FacebookIE is None, "Optional Facebook dependency is not installed")
class ErrorRedactionTests(unittest.TestCase):
    def test_extractor_error_does_not_leak_to_stderr_or_stdout(self):
        secret = "SIGNED_URL_SECRET_MARKER"
        error = ExtractorError(
            "download failed at https://cdn.example.invalid/video?token=" + secret,
            expected=True,
        )
        stdout, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(app, "check_yt_dlp", return_value=True),
                patch.object(FacebookIE, "_real_extract", side_effect=error),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                ok, message = app.download_facebook_video(
                    "https://www.facebook.com/reel/1234567890",
                    str(Path(directory) / "01"),
                )
            self.assertFalse(ok)
            self.assertIn("Facebook 下载失败", message)
            self.assertNotIn(secret, message + stdout.getvalue() + stderr.getvalue())
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
