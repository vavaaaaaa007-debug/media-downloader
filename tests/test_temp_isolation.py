"""Drive temporary-file isolation; no network access is performed."""
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as app


class Response(io.BytesIO):
    def __init__(self, body=b"image", length=None):
        super().__init__(body)
        self.headers = {
            "Content-Type": "image/jpeg",
            "Content-Length": str(len(body) if length is None else length),
        }


class TempIsolationTests(unittest.TestCase):
    def test_temporary_source_is_private_and_nested(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            seen_names = []

            def inspect_temporary_source(downloaded, total):
                visible = list(output.iterdir())
                self.assertEqual(len(visible), 1)
                private_dir = visible[0]
                self.assertTrue(private_dir.is_dir())
                self.assertTrue(private_dir.name.startswith(".drive-"))
                sources = list(private_dir.iterdir())
                self.assertEqual(len(sources), 1)
                self.assertTrue(sources[0].is_file())
                self.assertTrue(sources[0].name.startswith("media-"))
                seen_names.append(sources[0].name)
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(private_dir.stat().st_mode), 0o700)
                    self.assertEqual(stat.S_IMODE(sources[0].stat().st_mode), 0o600)

            with patch.object(app, "_open_drive_url", return_value=Response()):
                ok, info = app.download_file(
                    "abcdefghij", str(output / "01.jpg"),
                    progress_cb=inspect_temporary_source,
                )
            self.assertTrue(ok, info)
            self.assertTrue(seen_names)
            self.assertEqual(Path(info).read_bytes(), b"image")
            self.assertEqual(list(output.iterdir()), [output / "01.jpg"])

    def test_cancel_removes_private_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            response = Response()
            with patch.object(app, "_open_drive_url", return_value=response):
                ok, info = app.download_file(
                    "abcdefghij", str(Path(directory) / "01.jpg"),
                    stop_check=lambda: True,
                )
            self.assertFalse(ok)
            self.assertEqual(info, "已停止")
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertTrue(response.closed)

    def test_incomplete_download_removes_private_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, "_open_drive_url", return_value=Response(length=99)):
                ok, _ = app.download_file("abcdefghij", str(Path(directory) / "01.jpg"))
            self.assertFalse(ok)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_failed_file_creation_removes_private_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(app, "_open_drive_url", return_value=Response()),
                patch.object(app.tempfile, "mkstemp", side_effect=OSError("disk full")),
            ):
                ok, _ = app.download_file("abcdefghij", str(Path(directory) / "01.jpg"))
            self.assertFalse(ok)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_failed_publish_removes_private_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(app, "_open_drive_url", return_value=Response()),
                patch.object(app, "_publish_download", side_effect=OSError("cannot publish")),
            ):
                ok, _ = app.download_file("abcdefghij", str(Path(directory) / "01.jpg"))
            self.assertFalse(ok)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cleanup_failure_does_not_replace_download_result(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with (
                patch.object(app, "_open_drive_url", return_value=Response()),
                patch.object(app.shutil, "rmtree", side_effect=PermissionError("cleanup denied")),
            ):
                ok, info = app.download_file("abcdefghij", str(output / "01.jpg"))
            self.assertTrue(ok, info)
            self.assertEqual(Path(info).read_bytes(), b"image")


if __name__ == "__main__":
    unittest.main()
