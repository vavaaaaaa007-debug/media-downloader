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


class LoggingAndTimeoutTests(unittest.TestCase):
    def test_connection_timeout_is_explained_with_stage_logs(self):
        import socket
        logs = []
        with tempfile.TemporaryDirectory() as d, patch.object(
                app, "_open_drive_url", side_effect=socket.timeout("timed out")):
            ok, message = app.download_file("abcdefghij", os.path.join(d, "01.jpg"),
                                            log_cb=logs.append)
        self.assertFalse(ok)
        self.assertIn("没有收到谷歌的响应", message)
        self.assertIn("代理", message)
        self.assertEqual(sum("正在连接谷歌" in l for l in logs), 2)  # 两个地址都试过
        self.assertTrue(any("连接失败" in l for l in logs))

    def test_timeout_mid_download_has_clear_message(self):
        import socket

        class Stalls(io.BytesIO):
            headers = {"Content-Type": "image/jpeg", "Content-Length": "100"}

            def read(self, *a):
                raise socket.timeout("timed out")

        with tempfile.TemporaryDirectory() as d, \
                patch.object(app, "_open_drive_url", return_value=Stalls()):
            ok, message = app.download_file("abcdefghij", os.path.join(d, "01.jpg"))
            self.assertEqual(list(Path(d).iterdir()), [])
        self.assertFalse(ok)
        self.assertIn("没有收到数据", message)

    def test_progress_and_stage_logs_for_successful_download(self):
        logs = []

        class Body(io.BytesIO):
            headers = {"Content-Type": "image/jpeg", "Content-Length": "6"}

        ticks = iter([0, 0, 0, 6, 6, 6, 6, 6])
        with tempfile.TemporaryDirectory() as d, \
                patch.object(app, "_open_drive_url", return_value=Body(b"abcdef")), \
                patch.object(app.time, "monotonic", side_effect=lambda: next(ticks, 100)):
            ok, info = app.download_file("abcdefghij", os.path.join(d, "01.jpg"),
                                         log_cb=logs.append, chunk=3)
        self.assertTrue(ok, info)
        joined = "\n".join(logs)
        self.assertIn("已连接", joined)
        self.assertIn("开始接收文件：类型 image/jpeg，大小 6 B", joined)
        self.assertIn("已下载", joined)

    def test_run_plan_logs_each_item_and_wait(self):
        logs = []
        with tempfile.TemporaryDirectory() as d, \
                patch.object(app, "download_file", side_effect=lambda f, dest, **k: (False, "x")), \
                patch.object(app, "_pause"):
            app.download_batch([A, B], 1, d, delay=2, log_cb=logs.append)
        joined = "\n".join(logs)
        self.assertIn("开始 序号 1（Drive …/d/aaaaaaaaaa…）", joined)
        self.assertIn("序号 1 结束，用时", joined)
        self.assertIn("等待", joined)

    def test_size_formatting(self):
        self.assertEqual(app._fmt_size(None), "未知")
        self.assertEqual(app._fmt_size(500), "500 B")
        self.assertEqual(app._fmt_size(1536), "1.5 KB")
        self.assertEqual(app._fmt_size(5 * 1024 ** 2), "5.0 MB")


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


class StartNumberTests(unittest.TestCase):
    def test_parse_start(self):
        for text, expected in (("3", (3, None)), ("03", (3, None)), (" 03-2 ", (3, 2)),
                               ("01－1", (1, 1)), ("7—12", (7, 12)), ("-1", (-1, None)),
                               ("0", (0, None))):
            with self.subTest(text=text):
                self.assertEqual(app.parse_start(text), expected)
        for bad in ("", "a", "3-", "3-0", "1-2-3", "3 2", "03-a", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                app.parse_start(bad)

    def tags(self, links, start, sub, **kw):
        plan = app.build_plan(links, start, zero_pad=2, start_sub=sub, **kw)
        return [e["tag"] for e in plan["entries"]]

    def test_first_series_continues_then_normal_numbering(self):
        self.assertEqual(self.tags(['"%s' % A, B + '"', C], 3, 2), ["03-2", "03-3", "04"])

    def test_single_first_group_still_gets_suffix_when_continuing(self):
        self.assertEqual(self.tags([A, '"%s' % B, C + '"'], 3, 2), ["03-2", "04-1", "04-2"])
        plan = app.build_plan([A], 3, zero_pad=2, start_sub=2)
        self.assertEqual(plan["items"][0]["label"], "03-2")
        self.assertTrue(plan["items"][0]["suffix"])

    def test_sub_start_applies_to_first_valid_group_only(self):
        self.assertEqual(self.tags(["oops", "", A, B], 5, 1), ["—", "05-1", "06"])
        self.assertEqual(self.tags(['"%s' % A, B + '"'], 1, 1), ["01-1", "01-2"])

    def test_sub_padding_follows_largest_number_in_series(self):
        ids = [U % ("%012d" % i) for i in range(6)]
        lines = ['"' + ids[0]] + ids[1:-1] + [ids[-1] + '"']
        tags = self.tags(lines, 3, 5)
        self.assertEqual(tags[0], "03-05")
        self.assertEqual(tags[-1], "03-10")

    def test_without_sub_behaviour_is_unchanged(self):
        self.assertEqual(self.tags([A, B], 1, None), ["01", "02"])

    def test_invalid_sub_rejected(self):
        for bad in (0, -1, 1.5, "2", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                app.build_plan([A], 1, start_sub=bad)

    def test_download_batch_uses_sub_start_for_file_names(self):
        seen = []
        with tempfile.TemporaryDirectory() as d, patch.object(
                app, "download_file",
                side_effect=lambda f, dest, **k: (seen.append(os.path.basename(dest)), (False, "x"))[1]):
            app.download_batch(['"%s' % A, B + '"', C], 3, d, zero_pad=2, start_sub=2)
        self.assertEqual(seen, ["03-2.bin", "03-3.bin", "04.bin"])


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


def folder_entry(fid, name, mime):
    href = ("https://drive.google.com/drive/folders/%s" % fid if mime.endswith("folder")
            else "https://drive.google.com/file/d/%s/view?usp=drive_web" % fid)
    return ('<div class="flip-entry" id="entry-%s" tabindex="0" role="link"><div class="flip-entry-info">'
            '<a href="%s" target="_blank"><div class="flip-entry-list-icon"><img src="https://drive-thirdparty'
            '.googleusercontent.com/16/type/%s" alt=""/></div><div class="flip-entry-title">%s</div></a></div>'
            '</div>' % (fid, href, mime, name))


def folder_page(entries):
    return ('<html><body><div class="flip-entries">%s</div></body></html>'
            % "".join(folder_entry(*e) for e in entries))


FOLDER_URL = "https://drive.google.com/drive/folders/1zg6LP2iR5y4ERfYlHmOr7u4dLwgQUMrF"


class FolderLinkTests(unittest.TestCase):
    def test_folder_id_extraction(self):
        fid = "1zg6LP2iR5y4ERfYlHmOr7u4dLwgQUMrF"
        for link in (FOLDER_URL, FOLDER_URL + "?usp=sharing", FOLDER_URL + "?usp=drive_link",
                     "https://drive.google.com/drive/u/0/folders/" + fid, "  " + FOLDER_URL + "  "):
            self.assertEqual(app.extract_folder_id(link), fid, link)
        for link in ("https://drive.google.com/file/d/%s/view" % fid, "http://drive.google.com/drive/folders/" + fid,
                     "https://evil.com/drive/folders/" + fid, fid, ""):
            self.assertIsNone(app.extract_folder_id(link), link)
        self.assertTrue(app.has_folder_links(['"' + FOLDER_URL + '"']))
        self.assertFalse(app.has_folder_links(["https://drive.google.com/file/d/%s/view" % fid]))

    def test_parse_keeps_media_in_natural_name_order(self):
        page = folder_page([
            ("a" * 20, "5.png", "image/png"),
            ("b" * 20, "5 (10).jpg", "image/jpeg"),
            ("c" * 20, "5 (2).webp", "image/webp"),
            ("d" * 20, "sub", "application/vnd.google-apps.folder"),
            ("e" * 20, "notes.pdf", "application/pdf"),
            ("f" * 20, "clip.mp4", "video/mp4"),
            ("g" * 20, "A &amp; B.jpg", "image/jpeg"),
        ])
        media, skipped = app.parse_folder_page(page)
        self.assertEqual([n for _, n in media], ["5 (2).webp", "5 (10).jpg", "5.png", "A & B.jpg", "clip.mp4"])
        self.assertEqual(media[0][0], "c" * 20)
        self.assertEqual(skipped, 2)

    def test_non_folder_page_is_rejected(self):
        with self.assertRaises(ValueError):
            app.parse_folder_page("<html>Sign in</html>")

    def test_list_folder_maps_login_redirect_to_permission_message(self):
        err = urllib.error.URLError("拒绝非 Google HTTPS 下载重定向")
        with patch.object(app, "_open_drive_url", side_effect=err):
            with self.assertRaisesRegex(ValueError, "没有访问权限"):
                app.list_drive_folder("x" * 20)

    def test_list_folder_reads_page(self):
        page = folder_page([("a" * 20, "1.jpg", "image/jpeg")])
        with patch.object(app, "_open_drive_url", return_value=Page(page)):
            self.assertEqual(app.list_drive_folder("x" * 20), ([("a" * 20, "1.jpg")], 0))

    def test_folder_becomes_one_series(self):
        lister = lambda fid: ([("p" * 20, "1.jpg"), ("q" * 20, "2.jpg"), ("r" * 20, "3.jpg")], 1)
        single = "https://drive.google.com/uc?export=download&id=" + "s" * 20
        lines, notes, errors = app.expand_folder_links([single, FOLDER_URL, single], lister=lister)
        self.assertEqual(errors, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("3 个文件", notes[0])
        plan = app.build_plan(lines, 1, zero_pad=2)
        self.assertEqual([(e["tag"], e["fid"]) for e in plan["items"]],
                         [("01", "s" * 20), ("02-1", "p" * 20), ("02-2", "q" * 20),
                          ("02-3", "r" * 20), ("03", "s" * 20)])

    def test_failed_folder_is_reported_and_kept_as_unrecognized(self):
        def lister(fid):
            raise ValueError(app._PERMISSION_MESSAGE)
        lines, notes, errors = app.expand_folder_links([FOLDER_URL], lister=lister)
        self.assertEqual(notes, [])
        self.assertIn("没有访问权限", errors[0])
        plan = app.build_plan(lines, 1)
        self.assertEqual(plan["items"], [])
        self.assertEqual(len(plan["skipped"]), 1)

    def test_empty_folder_is_dropped_with_message(self):
        lines, notes, errors = app.expand_folder_links([FOLDER_URL], lister=lambda fid: ([], 2))
        self.assertEqual(lines, [])
        self.assertIn("没有图片或视频", errors[0])


class TablePasteTests(unittest.TestCase):
    F = "https://drive.google.com/uc?export=download&id=%s"

    def html_row(self, *cells):
        return "<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in cells)

    def a(self, key, text=None):
        url = (self.F % (key * 20)).replace("&", "&amp;")
        return '<a class="in-cell-link" href="%s">%s</a>' % (url, text or url)

    def tags(self, lines):
        return [(e["tag"], e["fid"][0]) for e in app.build_plan(lines, 1, zero_pad=2)["items"]]

    def test_html_rows_are_series_and_hyperlink_text_resolved(self):
        page = ("<meta charset='utf-8'><table><tbody>"
                + self.html_row(self.a("a"), self.a("b"))
                + self.html_row(self.a("c", "点击下载"), self.a("d"), '<img src="x.png">')
                + self.html_row("标题", self.a("e"))
                + self.html_row(self.a("f") + "<br>" + self.a("g"), "备注")
                + "</tbody></table>")
        lines = app.lines_from_paste("ignored\tplain", page)
        self.assertEqual(self.tags(lines), [("01-1", "a"), ("01-2", "b"), ("02-1", "c"), ("02-2", "d"),
                                            ("03", "e"), ("04-1", "f"), ("04-2", "g")])

    def test_multiline_cell_text_without_anchor(self):
        page = "<table>" + self.html_row(self.F % ("a" * 20) + "<br>" + self.F % ("b" * 20)) + "</table>"
        self.assertEqual(self.tags(app.lines_from_paste("", page)), [("01-1", "a"), ("01-2", "b")])

    def test_tab_text_rows_are_series(self):
        text = "%s\t%s\n点击下载\t%s\n\"%s\n%s\"\t%s\n" % (
            self.F % ("a" * 20), self.F % ("b" * 20), self.F % ("c" * 20),
            self.F % ("d" * 20), self.F % ("e" * 20), self.F % ("f" * 20))
        self.assertEqual(self.tags(app.lines_from_paste(text)),
                         [("01-1", "a"), ("01-2", "b"), ("02", "c"), ("03-1", "d"), ("03-2", "e"), ("03-3", "f")])

    def test_plain_text_keeps_old_rules(self):
        text = '%s\n"%s\n%s"\n[点击下载](%s)' % (self.F % ("a" * 20), self.F % ("b" * 20),
                                               self.F % ("c" * 20), self.F % ("d" * 20))
        self.assertEqual(self.tags(app.lines_from_paste(text, "<p>no table</p>")),
                         [("01", "a"), ("02-1", "b"), ("02-2", "c"), ("03", "d")])

    def test_folder_link_in_table_row_joins_that_series(self):
        lines = app.lines_from_paste("%s\t%s" % (self.F % ("a" * 20), FOLDER_URL))
        lines, _, _ = app.expand_folder_links(lines, lister=lambda fid: ([("p" * 20, "1.jpg")], 0))
        self.assertEqual(self.tags(lines), [("01-1", "a"), ("01-2", "p")])

    def test_bad_html_falls_back_to_text(self):
        self.assertEqual(app.lines_from_paste("x\ny", None), ["x", "y"])
        self.assertIsNone(app.rows_from_html("<div>no rows</div>"))


class MediaFolderTests(unittest.TestCase):
    def make(self, d, *names):
        paths = []
        for n in names:
            path = os.path.join(d, n)
            Path(path).write_bytes(b"x")
            paths.append(path)
        return paths

    def test_mixed_batch_is_split_into_two_folders(self):
        with tempfile.TemporaryDirectory() as d:
            paths = self.make(d, "01-1.jpg", "01-2.webp", "02.mp4", "03.MOV", "04.bin")
            moved = app.sort_media_files(paths, d)
            self.assertEqual(sorted(os.listdir(os.path.join(d, "图片"))), ["01-1.jpg", "01-2.webp"])
            self.assertEqual(sorted(os.listdir(os.path.join(d, "视频"))), ["02.mp4", "03.MOV"])
            self.assertTrue(os.path.exists(os.path.join(d, "04.bin")))  # 未知类型留在原处
            self.assertEqual(moved[os.path.join(d, "02.mp4")], os.path.join(d, "视频", "02.mp4"))
            self.assertEqual(len(moved), 4)

    def test_single_kind_batch_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            for names in (("01.jpg", "02.png"), ("01.mp4", "02.mov")):
                paths = self.make(d, *names)
                self.assertEqual(app.sort_media_files(paths, d), {})
                for p in paths:
                    self.assertTrue(os.path.exists(p))
                    os.remove(p)
            self.assertEqual(os.listdir(d), [])

    def test_repeat_call_moves_only_new_files_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            first = self.make(d, "01.jpg", "02.mp4")
            moved = app.sort_media_files(first, d)
            os.makedirs(os.path.join(d, "图片"), exist_ok=True)
            Path(os.path.join(d, "图片", "03.jpg")).write_bytes(b"old")
            later = self.make(d, "03.jpg")
            moved2 = app.sort_media_files(list(moved.values()) + later, d)
            self.assertEqual(list(moved2), [os.path.join(d, "03.jpg")])
            self.assertEqual(sorted(os.listdir(os.path.join(d, "图片"))), ["01.jpg", "03(1).jpg", "03.jpg"])
            self.assertEqual(Path(d, "图片", "03.jpg").read_bytes(), b"old")

    def test_files_outside_save_folder_are_ignored(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            outside = self.make(other, "x.mp4")
            inside = self.make(d, "01.jpg")
            self.assertEqual(app.sort_media_files(outside + inside, d), {})
            self.assertTrue(os.path.exists(outside[0]))


if __name__ == "__main__":
    unittest.main()
