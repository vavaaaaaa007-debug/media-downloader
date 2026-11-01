"""失败原因判断、同链接只下载一次、限流暂停、记住保存位置；全部离线模拟。"""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as app

U = "https://drive.google.com/file/d/%s/view?usp=drive_link"
A, B, C = (U % (c * 12) for c in "abc")


class Page(io.BytesIO):
    def __init__(self, text, ctype="text/html"):
        super().__init__(text.encode("utf-8"))
        self.headers = {"Content-Type": ctype}


def http_error(code, body=""):
    return urllib.error.HTTPError("https://x.test", code, "err", {},
                                  io.BytesIO(body.encode("utf-8")))


class FailureReasonTests(unittest.TestCase):
    def run_page(self, text):
        with tempfile.TemporaryDirectory() as d, \
                patch.object(app, "_open_drive_url", return_value=Page(text)):
            return app.download_file("abcdefghij", os.path.join(d, "01.jpg"))

    def test_page_reasons(self):
        cases = (
            ("Sorry, you can't view or download this file at this time. Too many users "
             "have viewed or downloaded this file recently.", app._QUOTA_MESSAGE),
            ("We're sorry... but your computer or network may be sending automated queries. "
             "unusual traffic", app._RATE_LIMIT_MESSAGE),
            ("很抱歉，您请求的文件不存在。", app._NOTFOUND_MESSAGE),
            ("You need access. Request access, or switch to an account with access.",
             app._PERMISSION_MESSAGE),
        )
        for text, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.run_page(text), (False, expected))

    def test_generic_sorry_is_not_called_a_quota_problem(self):
        ok, message = self.run_page("很抱歉，出了点问题。please try again")
        self.assertFalse(ok)
        self.assertEqual(message, app._UNKNOWN_PAGE_MESSAGE)

    def test_http_errors_are_reported_not_hidden(self):
        for error, expected in ((http_error(429), app._RATE_LIMIT_MESSAGE),
                                (http_error(404), app._NOTFOUND_MESSAGE),
                                (http_error(403, "Too many users have viewed"), app._QUOTA_MESSAGE),
                                (http_error(403, "You need access"), app._PERMISSION_MESSAGE)):
            with self.subTest(code=error.code), tempfile.TemporaryDirectory() as d, \
                    patch.object(app, "_open_drive_url", side_effect=error):
                self.assertEqual(app.download_file("abcdefghij", os.path.join(d, "01.jpg")),
                                 (False, expected))

    def test_unexplained_403_mentions_status_after_trying_both_addresses(self):
        with tempfile.TemporaryDirectory() as d, patch.object(
                app, "_open_drive_url",
                side_effect=lambda *a, **k: (_ for _ in ()).throw(http_error(403, "x"))) as opener:
            ok, message = app.download_file("abcdefghij", os.path.join(d, "01.jpg"))
        self.assertFalse(ok)
        self.assertIn("HTTP 403", message)
        self.assertGreaterEqual(opener.call_count, 2)


class BatchReliabilityTests(unittest.TestCase):
    def test_same_link_requested_once_and_copied_with_new_names(self):
        links = ['"%s' % A, B + '"', '"%s' % A, B + '"', A]
        with tempfile.TemporaryDirectory() as d:
            calls = []

            def fake(fid, dest, **kw):
                calls.append(fid)
                final = dest[:-4] + ".jpg"
                Path(final).write_bytes(b"data-" + fid.encode())
                return True, final

            with patch.object(app, "download_file", side_effect=fake):
                count, results = app.download_batch(links, 1, d, zero_pad=2)
            self.assertEqual(count, 5, results)
            self.assertEqual(calls, ["a" * 12, "b" * 12])
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()),
                             ["01-1.jpg", "01-2.jpg", "02-1.jpg", "02-2.jpg", "03.jpg"])
            self.assertEqual((Path(d) / "02-1.jpg").read_bytes(), b"data-" + b"a" * 12)

    def test_cache_shared_between_runs_avoids_repeat_requests(self):
        cache, calls = {}, []

        def fake(fid, dest, **kw):
            calls.append(fid)
            final = dest[:-4] + ".jpg"
            Path(final).write_bytes(b"x")
            return True, final

        with tempfile.TemporaryDirectory() as d, patch.object(app, "download_file", side_effect=fake):
            first = app.build_plan([A], 1, zero_pad=2)["items"]
            second = app.build_plan([A, A], 2, zero_pad=2)["items"]
            app.run_plan(first, d, cache=cache)
            app.run_plan(second, d, cache=cache)
            names = sorted(p.name for p in Path(d).iterdir())
        self.assertEqual(calls, ["a" * 12])
        self.assertEqual(names, ["01.jpg", "02.jpg", "03.jpg"])

    def test_rate_limit_stops_batch_and_marks_rest_not_run(self):
        seen, cb = [], []
        with tempfile.TemporaryDirectory() as d, patch.object(
                app, "download_file",
                side_effect=lambda fid, dest, **kw: (seen.append(fid),
                                                     (False, app._RATE_LIMIT_MESSAGE))[1]):
            count, results = app.download_batch(
                [A, B, C], 1, d, result_cb=lambda item, ok, info: cb.append((item["tag"], ok)))
        self.assertEqual(count, 0)
        self.assertEqual(len(seen), 1)
        self.assertEqual([r[2] for r in results[1:]], [app._NOT_RUN_MESSAGE] * 2)
        self.assertEqual(cb, [("1", False), ("2", False), ("3", False)])

    def test_file_level_failure_does_not_stop_and_is_not_repeated(self):
        calls = []

        def fake(fid, dest, **kw):
            calls.append(fid)
            if fid == "a" * 12:
                return False, app._QUOTA_MESSAGE
            final = dest[:-4] + ".jpg"
            Path(final).write_bytes(b"ok")
            return True, final

        with tempfile.TemporaryDirectory() as d, patch.object(app, "download_file", side_effect=fake):
            count, results = app.download_batch([A, B, A], 1, d, zero_pad=2)
        self.assertEqual(count, 1)
        self.assertEqual(calls, ["a" * 12, "b" * 12])
        self.assertTrue(results[0][2].startswith("失败"))
        self.assertTrue(results[2][2].startswith("失败"))

    def test_delay_only_between_real_requests(self):
        sleeps = []
        with tempfile.TemporaryDirectory() as d, \
                patch.object(app, "download_file",
                             side_effect=lambda fid, dest, **kw: (False, "x")), \
                patch.object(app.time, "sleep", side_effect=sleeps.append), \
                patch.object(app, "_pause", side_effect=lambda s, f: sleeps.append(s)):
            app.download_batch([A, B, C], 1, d, delay=2)
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(all(1.3 <= s <= 2.7 for s in sleeps))

    def test_plan_entries_cover_invalid_lines_for_preview(self):
        plan = app.build_plan(['"%s' % A, "oops", B + '"', C], 5, zero_pad=2)
        self.assertEqual([e["tag"] for e in plan["entries"]], ["05-1", "05-2", "05-3", "06"])
        self.assertEqual([e["valid"] for e in plan["entries"]], [True, False, True, True])
        self.assertEqual(plan["group_titles"], ["05", "06"])
        self.assertEqual(len(plan["items"]), 3)


class SettingsTests(unittest.TestCase):
    def test_last_folder_is_remembered_and_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "conf", "settings.json")
            folder = os.path.join(d, "mine")
            os.mkdir(folder)
            self.assertEqual(app.initial_out_dir(cfg), app.default_out_dir())
            self.assertTrue(app.save_settings({"out_dir": folder}, cfg))
            self.assertEqual(app.initial_out_dir(cfg), folder)
            os.rmdir(folder)
            self.assertEqual(app.initial_out_dir(cfg), app.default_out_dir())

    def test_corrupt_settings_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "settings.json")
            Path(cfg).write_text("{not json", encoding="utf-8")
            self.assertEqual(app.load_settings(cfg), {})
            Path(cfg).write_text(json.dumps([1, 2]), encoding="utf-8")
            self.assertEqual(app.load_settings(cfg), {})


if __name__ == "__main__":
    unittest.main()
