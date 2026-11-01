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
        if fn:
            fn()  # 测试里同步执行

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

    def test_garbage_paste_is_rejected(self):
        self.app.root.clip = "hello world"
        self.app._on_paste_click()
        self.assertIsNone(self.app.plan)
        self.assertEqual(self.box.calls[-1][0], "showwarning")

    def test_repaste_returns_to_empty_state(self):
        self.app._on_paste_click()
        self.app._on_repaste()
        self.assertIsNone(self.app.plan)
        self.assertEqual(self.app.btn_start.style, "primary_off")


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
