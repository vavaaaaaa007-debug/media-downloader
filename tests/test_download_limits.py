"""Size-limit policy tests; Facebook and Drive requests are simulated locally."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as app


class Response(io.BytesIO):
    def __init__(self, body=b"abcdef", length=None):
        super().__init__(body)
        self.headers = {"Content-Type": "image/jpeg"}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self.read_calls = 0

    def read(self, *args):
        self.read_calls += 1
        return super().read(*args)


class DownloadLimitTests(unittest.TestCase):
    def assert_limit_failure(self, result, output):
        self.assertEqual(result, (False, app._LIMIT_MESSAGE))
        self.assertEqual(list(output.iterdir()), [])

    def test_default_is_two_gib_and_known_length_rejected_before_read(self):
        self.assertEqual(app.DEFAULT_MAX_BYTES, 2 * 1024 ** 3)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            response = Response(length=app.DEFAULT_MAX_BYTES + 1)
            with patch.object(app, "_open_drive_url", return_value=response):
                result = app.download_file("abcdefghij", str(output / "01.jpg"))
            self.assert_limit_failure(result, output)
            self.assertEqual(response.read_calls, 0)
            self.assertTrue(response.closed)

    def test_known_and_unknown_lengths_never_publish_above_limit(self):
        for length in (None, 6, 3):
            with self.subTest(length=length), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                with (
                    patch.object(app, "_open_drive_url", return_value=Response(length=length)),
                    patch.object(app, "_publish_download") as publish,
                ):
                    result = app.download_file(
                        "abcdefghij", str(output / "01.jpg"), chunk=3, max_bytes=5,
                    )
                self.assert_limit_failure(result, output)
                publish.assert_not_called()

    def test_zero_and_raised_limit_allow_download(self):
        for limit in (0, 6, 10):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                with patch.object(app, "_open_drive_url", return_value=Response(length=6)):
                    ok, info = app.download_file(
                        "abcdefghij", str(output / "01.jpg"), max_bytes=limit,
                    )
                self.assertTrue(ok, info)
                self.assertEqual(Path(info).read_bytes(), b"abcdef")
                self.assertEqual(list(output.iterdir()), [output / "01.jpg"])

    def test_nonnegative_integer_required_for_all_entry_points(self):
        for limit in (-1, 1.5, "5", None, True):
            for invoke in (
                lambda: app.download_file("abcdefghij", "/unused", max_bytes=limit),
                lambda: app.download_facebook_video("https://facebook.com/reel/123", "/unused", max_bytes=limit),
                lambda: app.download_batch([], 1, "/unused", max_bytes=limit),
            ):
                with self.subTest(limit=limit), self.assertRaises(ValueError):
                    invoke()

    def run_facebook(self, action, max_bytes=5):
        options = []

        class YoutubeDL:
            def __init__(self, opts):
                self.opts = opts
                options.append(opts)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def extract_info(self, url, download):
                return action(self.opts)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with (
                patch.object(app, "check_yt_dlp", return_value=True),
                patch("yt_dlp.YoutubeDL", YoutubeDL),
            ):
                result = app.download_facebook_video(
                    "https://facebook.com/reel/1234567890", str(output / "01"),
                    max_bytes=max_bytes,
                )
            published = {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
            remaining_dirs = [p.name for p in output.iterdir() if p.is_dir()]
        self.assertEqual(remaining_dirs, [])
        self.assertEqual(options[0]["max_filesize"], max_bytes or None)
        return result, published

    @staticmethod
    def write_video(opts, body=b"abcdef"):
        Path(opts["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(body)

    def test_facebook_hook_limit_survives_wrapped_exception(self):
        def action(opts):
            self.write_video(opts)
            try:
                opts["progress_hooks"][0]({"status": "downloading", "downloaded_bytes": 6})
            except Exception as error:
                raise RuntimeError("downloader wrapped the hook exception") from error

        result, published = self.run_facebook(action)
        self.assertEqual(result, (False, app._LIMIT_MESSAGE))
        self.assertEqual(published, {})

    def test_facebook_hook_limit_cannot_be_silently_swallowed(self):
        def action(opts):
            self.write_video(opts, b"small")
            try:
                opts["progress_hooks"][0]({"status": "downloading", "downloaded_bytes": 6})
            except Exception:
                pass
            return {}

        result, published = self.run_facebook(action)
        self.assertEqual(result, (False, app._LIMIT_MESSAGE))
        self.assertEqual(published, {})

    def test_facebook_final_file_size_checked_without_hook(self):
        def action(opts):
            self.write_video(opts)
            return {}

        result, published = self.run_facebook(action)
        self.assertEqual(result, (False, app._LIMIT_MESSAGE))
        self.assertEqual(published, {})

    def test_facebook_max_filesize_skip_is_not_success(self):
        for action in (
            lambda opts: {"filesize": 6},
            lambda opts: opts["logger"].debug("[download] File is larger than max-filesize (6 bytes > 5 bytes). Aborting."),
            lambda opts: None,
        ):
            result, published = self.run_facebook(action)
            self.assertFalse(result[0])
            self.assertEqual(published, {})

    def test_facebook_zero_and_raised_limit_allow_download(self):
        def action(opts):
            self.write_video(opts)
            opts["progress_hooks"][0]({"status": "finished", "downloaded_bytes": 6})
            return {}

        for limit in (0, 6, 10):
            with self.subTest(limit=limit):
                result, published = self.run_facebook(action, max_bytes=limit)
                self.assertTrue(result[0], result[1])
                self.assertEqual(published, {"01.mp4": b"abcdef"})

    def test_batch_passes_limit_to_both_downloaders(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(app, "download_file", return_value=(False, app._LIMIT_MESSAGE)) as drive,
                patch.object(app, "download_facebook_video", return_value=(False, app._LIMIT_MESSAGE)) as facebook,
            ):
                ok, _ = app.download_batch(
                    ["abcdefghij", "https://facebook.com/reel/1234567890"], 1,
                    directory, max_bytes=17,
                )
            self.assertEqual(ok, 0)
            self.assertEqual(drive.call_args.kwargs["max_bytes"], 17)
            self.assertEqual(facebook.call_args.kwargs["max_bytes"], 17)


if __name__ == "__main__":
    unittest.main()
