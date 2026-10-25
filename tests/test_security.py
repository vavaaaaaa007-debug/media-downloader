import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from email.message import Message
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as app

class Response(io.BytesIO):
    def __init__(self, body=b"test", headers=None):
        super().__init__(body)
        self.headers = headers or {"Content-Type": "image/jpeg", "Content-Length": str(len(body))}

class SecurityTests(unittest.TestCase):
    def test_prefix_escape(self):
        for prefix in ("../outside", "/tmp/x", "C:\\tmp", "a:b", "a\x00b", "a\nb"):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                app.download_batch([], 1, "/tmp", prefix=prefix)
        app._validate_prefix("祷告_图片")

    def test_old_dependencies_rejected(self):
        import certifi
        with patch.object(certifi, "__version__", "2026.6.17"):
            self.assertFalse(app.check_yt_dlp())

    def test_url_boundary(self):
        for url in ("https://evil.test/?next=https://facebook.com/reel/1", "https://facebook.com.evil.test/reel/1", "http://facebook.com/reel/1", "https://facebook.com@evil.test/reel/1", "https://facebook.com:999/reel/1", "https://facebook.com/reel/1 extra"):
            self.assertFalse(app.is_facebook_url(url), url)
        for url in ("https://www.facebook.com/reel/123", "https://fb.watch/abc/"):
            self.assertTrue(app.is_facebook_url(url), url)

    def test_untrusted_extension(self):
        for filename in ("x.jpg/../../outside", "x.exe", "x.desktop", "x.url", "x.jpg\\evil"):
            with self.subTest(filename=filename):
                r = Response(headers={"Content-Disposition": 'attachment; filename="' + filename + '"', "Content-Type": "application/octet-stream"})
                self.assertEqual(app._get_ext_from_response(r, "id"), ".bin")
        r = Response(headers={"Content-Disposition": 'attachment; filename="photo.PNG"'})
        self.assertEqual(app._get_ext_from_response(r, "id"), ".png")

    def test_download_symlink_and_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)/"01.jpg"
            victim = Path(d)/"victim"
            victim.write_bytes(b"private")
            Path(str(dest)+".downloading").symlink_to(victim)
            dest.symlink_to(victim)
            with patch.object(app, "_open_drive_url", return_value=Response()):
                ok, info = app.download_file("abcdefghij", str(dest))
            self.assertTrue(ok, info)
            self.assertEqual(Path(info).read_bytes(), b"test")
            self.assertEqual(victim.read_bytes(), b"private")
            self.assertEqual(Path(info).name, "01(1).jpg")

    def test_incomplete_and_stop_cleanup(self):
        for stop in (False, True):
            with tempfile.TemporaryDirectory() as d:
                r = Response(b"x", {"Content-Type": "image/jpeg", "Content-Length": "9"})
                with patch.object(app, "_open_drive_url", return_value=r):
                    ok, _ = app.download_file("abcdefghij", str(Path(d)/"01.jpg"), stop_check=lambda: stop)
                self.assertFalse(ok)
                self.assertEqual(list(Path(d).iterdir()), [])
                self.assertTrue(r.closed)

    def test_filesystem_without_hardlinks(self):
        import errno
        with tempfile.TemporaryDirectory() as d:
            src, dest = Path(d)/"temp", Path(d)/"01.jpg"
            src.write_bytes(b"image")
            dest.write_bytes(b"keep")
            with patch.object(app.os, "link", side_effect=OSError(errno.ENOTSUP, "unsupported")):
                final = app._publish_download(str(src), str(dest))
            self.assertEqual(dest.read_bytes(), b"keep")
            self.assertEqual(Path(final).read_bytes(), b"image")
            self.assertFalse(src.exists())

    def test_confirmation_response_closed(self):
        r = Response(b"no token", {"Content-Type": "text/html"})
        with patch.object(app, "_open_drive_url", return_value=r):
            self.assertFalse(app.download_file("abcdefghij", "/unused")[0])
        self.assertTrue(r.closed)

    def test_redirect_boundary(self):
        h = app._DriveRedirectHandler()
        req = app.urllib.request.Request("https://drive.google.com/uc")
        for url in ("http://drive.google.com/a", "https://127.0.0.1/a", "https://drive.google.com.evil.test/a", "https://evil.test/a", "file:///etc/passwd"):
            with self.subTest(url=url), self.assertRaises(app.urllib.error.URLError):
                h.redirect_request(req, None, 302, "Found", Message(), url)
        self.assertIsNotNone(h.redirect_request(req, None, 302, "Found", Message(), "https://abc.googleusercontent.com/a"))

    def test_fb_isolation_and_no_overwrite(self):
        class YDL:
            def __init__(self, opts): self.opts = opts
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, url, download):
                Path(self.opts["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"video")
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d)/"01"
            Path(str(dest)+".mp4").write_bytes(b"original")
            with patch("yt_dlp.YoutubeDL", YDL), patch.object(app, "check_yt_dlp", return_value=True):
                ok, info = app.download_facebook_video("https://facebook.com/reel/123", str(dest))
            self.assertTrue(ok, info)
            self.assertEqual(Path(info).name, "01(1).mp4")
            self.assertEqual(Path(str(dest)+".mp4").read_bytes(), b"original")
            self.assertFalse(any(p.name.startswith(".fbdl-") for p in Path(d).iterdir()))

if __name__ == "__main__": unittest.main()
