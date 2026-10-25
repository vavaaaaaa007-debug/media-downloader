"""Published media stays private even when a downloader created it as 0644."""
import errno
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as app


@unittest.skipUnless(os.name == "posix", "POSIX file permission semantics required")
class PublishPermissionTests(unittest.TestCase):
    @staticmethod
    def prepare_source(directory):
        private = Path(directory) / ".fbdl-test"
        private.mkdir(mode=0o700)
        source = private / "video.mp4"
        source.write_bytes(b"private video")
        source.chmod(0o644)
        return source

    def test_hardlink_publication_restricts_existing_source_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.prepare_source(directory)
            source_stat = source.stat()
            destination = Path(directory) / "01.mp4"
            with patch.object(app.os, "link", wraps=os.link) as link:
                final = app._publish_download(str(source), str(destination))
            link.assert_called_once()
            published = Path(final)
            self.assertEqual(published, destination)
            self.assertEqual(published.read_bytes(), b"private video")
            self.assertEqual(stat.S_IMODE(published.stat().st_mode), 0o600)
            self.assertEqual(published.stat().st_ino, source_stat.st_ino)
            self.assertFalse(source.exists())

    def test_exclusive_copy_publication_also_stays_private(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.prepare_source(directory)
            destination = Path(directory) / "01.mp4"
            with patch.object(app.os, "link", side_effect=OSError(errno.ENOTSUP, "unsupported")) as link:
                final = app._publish_download(str(source), str(destination))
            link.assert_called_once()
            published = Path(final)
            self.assertEqual(published, destination)
            self.assertEqual(published.read_bytes(), b"private video")
            self.assertEqual(stat.S_IMODE(published.stat().st_mode), 0o600)
            self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main()
