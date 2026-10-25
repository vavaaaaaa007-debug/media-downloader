"""Check the actual CA bundle and TLS settings without network access."""
import hashlib
from pathlib import Path
import ssl
import sys
import unittest
from unittest.mock import MagicMock, patch

import certifi

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import drive_image_downloader as app


class TLSTrustTests(unittest.TestCase):
    def test_drive_explicitly_uses_fixed_ca_bundle_with_verification(self):
        opener = MagicMock()
        with patch.object(app.urllib.request, "build_opener", return_value=opener) as build:
            app._open_drive_url("https://drive.google.com/uc", timeout=1)
        handlers = build.call_args.args
        context = next(h._context for h in handlers
                       if isinstance(h, app.urllib.request.HTTPSHandler))
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        roots = {hashlib.sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True)}
        expected = ssl.create_default_context(cafile=certifi.where())
        self.assertEqual(roots, {hashlib.sha256(c).hexdigest()
                                 for c in expected.get_ca_certs(binary_form=True)})
        self.assertGreater(len(roots), 0)
        # emSign ECC Root CA - G3, removed after the intermediate-key incident.
        self.assertNotIn("86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b", roots)

    def test_old_certificate_bundle_fails_before_opening_connection(self):
        with (patch.object(certifi, "__version__", "2026.6.17"),
              patch.object(app.urllib.request, "build_opener") as build):
            with self.assertRaises(app.urllib.error.URLError):
                app._open_drive_url("https://drive.google.com/uc", timeout=1)
            build.assert_not_called()
