"""新界面的冒烟测试：用假的 tkinter 零件跑完整流程（粘贴 → 下载 → 限流 → 继续），
用来发现名字写错、流程走不通等问题；不能代替在真实桌面上的目视检查。"""
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as backend
import downloader_ui as ui


class FakeTclError(Exception):
    pass


class FakeWidget:
    counter = 0
    delayed = []

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        FakeWidget.counter += 1

    def __getattr__(self, name):
        def method(*args, **kwargs):
            if name.startswith("create_"):
                FakeWidget.counter += 1
                return FakeWidget.counter
            return None
        return method

    def winfo_width(self):
        return 700

    def winfo_height(self):
        return 300

    def winfo_rootx(self):
        return 0

    def winfo_rooty(self):
        return 0

    def yview(self):
        return (0.0, 0.5)

    def focus_get(self):
        return None

    def after(self, ms, fn=None, *a):
        if fn is None:
            return None
        if ms == 0:
            fn()  # 立即要做的事在测试里同步执行
        else:
            FakeWidget.delayed.append((ms, fn))  # 延时任务只记录，不执行
        return None

    def clipboard_get(self):
        return self.clip

    def bind(self, *a, **k):
        pass


class FakeEntry(FakeWidget):
    pass


class FakeVar:
    def __init__(self, value=""):
        self.value, self.traces = value, []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for fn in self.traces:
            fn()

    def trace_add(self, mode, fn):
        self.traces.append(lambda: fn())


class FakeFont:
    def __init__(self, *a, **k):
        pass

    def measure(self, text):
        return 7 * len(text)


class FakeMessageBox:
    def __init__(self):
        self.calls = []

    def _rec(self, kind):
        def f(title, msg=""):
            self.calls.append((kind, msg))
            return True
        return f

    def __getattr__(self, name):
        return self._rec(name)


def install_fakes():
    fake_tk = types.SimpleNamespace(
        Tk=FakeWidget, Canvas=FakeWidget, Frame=FakeWidget, Label=FakeWidget,
        Entry=FakeEntry, Text=FakeWidget, Menu=FakeWidget, StringVar=FakeVar,
        TclError=FakeTclError)
    box = FakeMessageBox()
    ui.tk = fake_tk
    ui.ttk = types.SimpleNamespace(Scrollbar=FakeWidget)
    ui.filedialog = types.SimpleNamespace(askdirectory=lambda **k: "")
    ui.messagebox = box
    ui.tkfont = types.SimpleNamespace(Font=FakeFont, families=lambda root=None: ["PingFang SC"])
    return box


U = "https://drive.google.com/file/d/%s/view?usp=drive_link"
IDS = {k: U % (k * 12) for k in "abcdefg"}


def sample_text():
    def block(keys):
        links = [IDS[k] for k in keys]
        return links[0] if len(links) == 1 else '"' + "\n".join(links) + '"'
    return "\n".join(block(g) for g in ("abcd", "ad", "d", "ad", "ef", "efg"))


class SyncThread:
    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class UiSmokeTests(unittest.TestCase):
    def setUp(self):
        self.box = install_fakes()
        self.dir = tempfile.TemporaryDirectory()
        self.saved = []
        patches = [
            patch.object(backend, "initial_out_dir", return_value=self.dir.name),
            patch.object(backend, "save_settings", side_effect=lambda d, p=None: self.saved.append(d)),
            patch.object(backend, "check_certifi", return_value=True),
            patch.object(backend, "_pause"),
            patch.object(ui.threading, "Thread", SyncThread),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.dir.cleanup)
        self.app = ui.App(backend)
        self.app.root.clip = sample_text()

    def fake_download(self, fail_on=None):
        def fake(fid, dest, **kw):
            if fail_on and fid in fail_on:
                return False, fail_on[fid]
            final = dest[:-4] + ".jpg"
            Path(final).write_bytes(b"x")
            return True, final
        return fake

    def test_paste_builds_cards(self):
        self.app._on_paste_click()
        plan = self.app.plan
        self.assertEqual(len(plan["entries"]), 14)
        self.assertEqual(plan["group_titles"], ["01", "02", "03", "04", "05", "06"])
        self.assertEqual(ui.summarize(plan), (5, 1, 14))
        self.assertEqual(self.app.btn_start.style, "primary")

    def test_full_download_and_rename_on_setting_change(self):
        self.app._on_paste_click()
        with patch.object(backend, "download_file", side_effect=self.fake_download()):
            self.app._on_start()
        names = sorted(p.name for p in Path(self.dir.name).iterdir())
        self.assertEqual(names[:4], ["01-1.jpg", "01-2.jpg", "01-3.jpg", "01-4.jpg"])
        self.assertIn("03.jpg", names)
        self.assertEqual(len(self.app.ok_set), 14)
        self.assertTrue(self.app.status_var.get().startswith("全部完成"))
        self.assertEqual({"out_dir": self.dir.name}, self.saved[-1])
        self.assertFalse(self.app.running)

    def test_rate_limit_then_continue(self):
        self.app._on_paste_click()
        limit = {"c" * 12: backend._RATE_LIMIT_MESSAGE}
        with patch.object(backend, "download_file", side_effect=self.fake_download(limit)):
            self.app._on_start()
        self.assertFalse(self.app.running)
        self.assertEqual(self.app.btn_start.text, "继续下载")
        self.assertIn("还有", self.app.status_var.get())
        done_before = set(self.app.ok_set)
        self.assertTrue(0 < len(done_before) < 14)
        with patch.object(backend, "download_file", side_effect=self.fake_download()):
            self.app._on_start()
        self.assertEqual(len(self.app.ok_set), 14)
        self.assertEqual(len(list(Path(self.dir.name).iterdir())), 14)

    def test_changing_pad_renames_preview_and_start_without_links_warns(self):
        self.app._on_start()
        self.assertEqual(self.box.calls[-1][0], "showwarning")
        self.app._on_paste_click()
        self.app.pad_var.set("3位（001）")
        self.assertEqual(self.app.plan["group_titles"][0], "001")
        self.app.start_var.set("29")
        self.assertEqual(self.app.plan["group_titles"][0], "029")

    def test_start_with_series_suffix_renames_first_series(self):
        self.app.root.clip = '"%s\n%s"\n%s' % (IDS["a"], IDS["b"], IDS["c"])
        self.app._on_paste_click()
        self.app.start_var.set("03-2")
        tags = [e["tag"] for e in self.app.plan["entries"]]
        self.assertEqual(tags, ["03-2", "03-3", "04"])
        self.assertEqual(ui.summarize(self.app.plan), (1, 1, 3))
        with patch.object(backend, "download_file", side_effect=self.fake_download()):
            self.app._on_start()
        self.assertEqual(sorted(p.name for p in Path(self.dir.name).iterdir()),
                         ["03-2.jpg", "03-3.jpg", "04.jpg"])

    def test_single_file_continuing_series_counts_as_series_in_summary(self):
        self.app.root.clip = IDS["a"]
        self.app._on_paste_click()
        self.app.start_var.set("03-2")
        self.assertEqual([e["tag"] for e in self.app.plan["entries"]], ["03-2"])
        self.assertEqual(ui.summarize(self.app.plan), (1, 0, 1))

    def test_bad_start_text_is_reported_and_preview_still_works(self):
        self.app._on_paste_click()
        self.app.start_var.set("abc")
        self.assertEqual(self.app.plan["group_titles"][0], "01")  # 预览退回默认，不崩溃
        self.app._on_start()
        self.assertEqual(self.box.calls[-1][0], "showerror")
        self.assertIn("03-2", self.box.calls[-1][1])
        self.assertFalse(self.app.running)

    def test_live_log_lines_have_timestamps_and_reach_the_log(self):
        self.app._on_paste_click()
        lines = []
        self.app._log_write = lambda text, tag=None, clear=False: lines.append(text)

        def fake(fid, dest, log_cb=None, **kw):
            log_cb("正在连接谷歌（地址 1/2）…")
            final = dest[:-4] + ".jpg"
            Path(final).write_bytes(b"x")
            return True, final

        with patch.object(backend, "download_file", side_effect=fake):
            self.app._on_start()
        joined = "\n".join(lines)
        self.assertIn("正在连接谷歌（地址 1/2）…", joined)
        self.assertIn("[成功] 序号 01-1 -> 01-1.jpg", joined)
        self.assertRegex(lines[1], r"^\[\d\d:\d\d:\d\d\] ")

    def test_heartbeat_explains_long_silence(self):
        lines = []
        self.app._log_line = lines.append
        self.app.running = True
        now = ui.time.monotonic()
        self.app._last_activity = now - 12
        self.app._last_heartbeat = 0.0
        self.app._tick()
        self.assertIn("仍在等待服务器响应", lines[-1])
        self.assertNotIn("代理", lines[-1])
        self.app._last_activity = now - 40
        self.app._last_heartbeat = 0.0
        self.app._tick()
        self.assertIn("代理", lines[-1])
        before = len(lines)
        self.app._tick()  # 刚提示过，10 秒内不重复
        self.assertEqual(len(lines), before)
        self.app.running = False
        self.app._tick()
        self.assertEqual(len(lines), before)

    def test_copy_log_puts_text_on_clipboard(self):
        copied = []
        self.app.root.clipboard_clear = lambda: copied.clear()
        self.app.root.clipboard_append = copied.append
        self.app.log.get = lambda *a: "[12:00:00] hello\n"
        self.app._copy_log()
        self.assertEqual(copied, ["[12:00:00] hello"])

    def test_garbage_paste_is_rejected(self):
        self.app.root.clip = "hello world"
        self.app._on_paste_click()
        self.assertIsNone(self.app.plan)
        self.assertEqual(self.box.calls[-1][0], "showwarning")

    def test_clear_returns_to_empty_state_and_confirms_when_unfinished(self):
        self.app._on_paste_click()
        self.app._on_clear()
        self.assertEqual(self.box.calls[-1][0], "askyesno")
        self.assertIsNone(self.app.plan)
        self.assertEqual(self.app.btn_start.style, "primary_off")
        self.assertEqual(self.app.done_cache, {})

    def test_clear_does_not_ask_when_everything_is_downloaded(self):
        self.app._on_paste_click()
        with patch.object(backend, "download_file", side_effect=self.fake_download()):
            self.app._on_start()
        self.box.calls.clear()
        self.app._on_clear()
        self.assertEqual(self.box.calls, [])
        self.assertIsNone(self.app.plan)

    def test_clear_declined_keeps_list(self):
        self.app._on_paste_click()
        self.box.askyesno = lambda *a, **k: False
        self.app._on_clear()
        self.assertIsNotNone(self.app.plan)

    def test_multiple_pastes_append_and_continue_numbering(self):
        self.app.root.clip = '"%s\n%s"' % (IDS["a"], IDS["b"])
        self.app._on_paste_click()
        self.assertEqual(self.app.plan["group_titles"], ["01"])
        self.app.root.clip = IDS["c"]
        self.app._on_paste_click()
        self.app.root.clip = '"%s\n%s"' % (IDS["d"], IDS["e"])
        self.app._on_paste_click()
        plan = self.app.plan
        self.assertEqual([e["tag"] for e in plan["entries"]], ["01-1", "01-2", "02", "03-1", "03-2"])
        self.assertIn("已添加 2 个文件，共 5 个", self.app.status_var.get())

    def test_unclosed_quote_in_one_paste_does_not_swallow_next_paste(self):
        self.app.root.clip = '"%s\n%s' % (IDS["a"], IDS["b"])
        self.app._on_paste_click()
        self.app.root.clip = IDS["c"]
        self.app._on_paste_click()
        self.assertEqual([e["tag"] for e in self.app.plan["entries"]], ["01-1", "01-2", "02"])

    def test_paste_after_download_keeps_done_and_only_downloads_new(self):
        self.app.root.clip = '"%s\n%s"' % (IDS["a"], IDS["b"])
        self.app._on_paste_click()
        with patch.object(backend, "download_file", side_effect=self.fake_download()):
            self.app._on_start()
        self.assertEqual(self.app.ok_set, {0, 1})
        self.app.root.clip = "%s\n%s" % (IDS["a"], IDS["c"])  # 其中 a 是重复链接
        self.app._on_paste_click()
        self.assertEqual(self.app.ok_set, {0, 1})
        self.assertEqual(self.app.btn_start.text, "继续下载")
        calls = []

        def fake(fid, dest, **kw):
            calls.append(fid)
            final = dest[:-4] + ".jpg"
            Path(final).write_bytes(b"x")
            return True, final

        with patch.object(backend, "download_file", side_effect=fake):
            self.app._on_start()
        self.assertEqual(calls, ["c" * 12])  # 重复的 a 直接复制，不再向谷歌请求
        self.assertEqual(sorted(p.name for p in Path(self.dir.name).iterdir()),
                         ["01-1.jpg", "01-2.jpg", "02.jpg", "03.jpg"])

    def test_paste_key_works_and_is_debounced(self):
        self.app._on_paste_key()
        self.assertEqual(len(self.app.plan["items"]), 14)
        self.app._on_paste_key()  # 同一次按键触发的第二个事件
        self.assertEqual(len(self.app.plan["items"]), 14)

    def test_paste_ignored_while_running(self):
        self.app._on_paste_click()
        self.app.running = True
        count = len(self.app.plan["items"])
        self.app._on_paste_click()
        self.assertEqual(len(self.app.plan["items"]), count)
        self.assertIn("正在下载", self.app.status_var.get())


class LayoutHelperTests(unittest.TestCase):
    def test_layout_two_columns_equal_height_rows(self):
        layout, total = ui.layout_cards([4, 2, 1, 2], 700)
        self.assertEqual(len(layout), 4)
        self.assertEqual(layout[0][3], layout[1][3])
        self.assertGreater(layout[2][1], layout[0][1])
        self.assertGreater(total, layout[-1][1] + layout[-1][3])

    def test_narrow_width_uses_one_column(self):
        layout, _ = ui.layout_cards([2, 2], 380)
        self.assertEqual(layout[0][0], layout[1][0])

    def test_rounded_points_and_logo(self):
        self.assertEqual(len(ui.rounded_points(0, 0, 10, 10, 50)) % 2, 0)
        self.assertGreater(len(ui.cloud_outline(1)), 60)

    def test_display_link_and_fit_text(self):
        self.assertEqual(ui.display_link("drive", "1sjO8LZ_8cOHEhnsM4R7", "x"), "…/d/1sjO8LZ_8cOHEh…")
        self.assertIn("facebook.com", ui.display_link("facebook", None, "https://www.facebook.com/reel/123456"))
        font = FakeFont()
        self.assertTrue(ui.fit_text(font, "a" * 50, 70).endswith("…"))
        self.assertEqual(ui.fit_text(font, "abc", 70), "abc")


if __name__ == "__main__":
    unittest.main()
