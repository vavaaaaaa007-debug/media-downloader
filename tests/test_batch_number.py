"""Untrusted API numbering must never select a path outside the output folder."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as app


class BatchNumberTests(unittest.TestCase):
    def test_non_integer_numbers_rejected_before_either_downloader(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            output.mkdir()
            invalid = ("../escape", str(Path(directory) / "escape"),
                       "..\\escape", "1", 1.5, True, False, None)
            for number in invalid:
                for link in ("abcdefghij", "https://www.facebook.com/reel/123"):
                    with (self.subTest(number=number, link=link),
                          patch.object(app, "download_file") as drive,
                          patch.object(app, "download_facebook_video") as facebook):
                        with self.assertRaisesRegex(ValueError, "起始序号必须是整数"):
                            app.download_batch([link], number, str(output))
                        drive.assert_not_called()
                        facebook.assert_not_called()
            self.assertEqual(list(output.iterdir()), [])
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_integer_numbering_and_padding_are_preserved(self):
        for start, expected in ((1, "001.bin"), (23, "023.bin"),
                                (0, "000.bin"), (-1, "-01.bin")):
            with (self.subTest(start=start), tempfile.TemporaryDirectory() as directory,
                  patch.object(app, "download_file", return_value=(False, "test")) as drive):
                app.download_batch(["abcdefghij"], start, directory, zero_pad=3)
                self.assertEqual(drive.call_args.args[1], os.path.join(directory, expected))


if __name__ == "__main__":
    unittest.main()
