"""表格复制格式：引号包住的多行是一个系列 → 01-1、01-2…；无引号单行 → 01。"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as app

U = "https://drive.google.com/file/d/%s/view?usp=drive_link"
A, B, C, D = (U % (c * 12) for c in "abcd")
FB = "https://www.facebook.com/reel/123"


def names(text, **kw):
    seen = []
    with tempfile.TemporaryDirectory() as d, \
            patch.object(app, "download_file",
                         side_effect=lambda f, dest, **_: (seen.append(os.path.basename(dest)), (False, "x"))[1]), \
            patch.object(app, "download_facebook_video",
                         side_effect=lambda u, b, **_: (seen.append(os.path.basename(b)), (False, "x"))[1]):
        app.download_batch(text.splitlines(), kw.pop("start", 1), d, zero_pad=2, **kw)
    return seen


class SeriesNamingTests(unittest.TestCase):
    def test_spreadsheet_copy_format(self):
        text = '"%s\n%s\n%s"\n"%s\n%s"\n%s\n"%s\n%s"' % (A, B, C, A, B, D, B, C)
        self.assertEqual(names(text), [
            "01-1.bin", "01-2.bin", "01-3.bin", "02-1.bin", "02-2.bin",
            "03.bin", "04-1.bin", "04-2.bin"])

    def test_plain_lines_unchanged(self):
        self.assertEqual(names("%s\n\n%s\n%s" % (A, B, C)),
                         ["01.bin", "02.bin", "03.bin"])

    def test_quoted_single_link_and_curly_quotes(self):
        self.assertEqual(names('"%s"\n\u201c%s\n%s\u201d' % (A, B, C)),
                         ["01.bin", "02-1.bin", "02-2.bin"])

    def test_lone_quote_lines(self):
        self.assertEqual(names('"\n%s\n%s\n"\n%s' % (A, B, C)),
                         ["01-1.bin", "01-2.bin", "02.bin"])

    def test_facebook_in_series_and_prefix(self):
        self.assertEqual(names('"%s\n%s"\n%s' % (A, FB, B)),
                         ["01-1.bin", "01-2", "02.bin"])
        seen = []
        with tempfile.TemporaryDirectory() as d, patch.object(
                app, "download_file",
                side_effect=lambda f, dest, **_: (seen.append(os.path.basename(dest)), (False, "x"))[1]):
            app.download_batch(['"' + A, B + '"', C], 5, d, prefix="p", zero_pad=2)
        self.assertEqual(seen, ["p_05-1.bin", "p_05-2.bin", "p_06.bin"])

    def test_invalid_link_keeps_position_and_does_not_burn_number(self):
        self.assertEqual(names('"nope"\n%s' % A), ["01.bin"])
        self.assertEqual(names('"%s\nbad\n%s"' % (A, B)), ["01-1.bin", "01-3.bin"])

    def test_ten_or_more_pads_sub_number(self):
        ids = [U % ("%012d" % i) for i in range(10)]
        got = names('"' + "\n".join(ids) + '"')
        self.assertEqual((got[0], got[-1]), ("01-01.bin", "01-10.bin"))

    def test_results_label(self):
        with tempfile.TemporaryDirectory() as d, patch.object(
                app, "download_file", return_value=(False, "x")):
            _, results = app.download_batch(['"' + A, B + '"', C], 1, d, zero_pad=2)
        self.assertEqual([r[0] for r in results], ["01-1", "01-2", 2])


if __name__ == "__main__":
    unittest.main()
