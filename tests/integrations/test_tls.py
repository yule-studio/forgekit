from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.core.tls import apply_ca_bundle_fallback, resolve_ca_bundle


class TLSBundleTestCase(unittest.TestCase):
    def test_resolve_ca_bundle_prefers_existing_ssl_cert_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cafile = Path(temp_dir) / "bundle.pem"
            cafile.write_text("test", encoding="utf-8")

            with patch.dict(os.environ, {"SSL_CERT_FILE": str(cafile)}, clear=False):
                bundle = resolve_ca_bundle()

        self.assertEqual(bundle.source, "env")
        self.assertEqual(bundle.cafile, str(cafile))
        self.assertTrue(bundle.exists)

    @patch("yule_orchestrator.core.tls._load_certifi_cafile")
    @patch("yule_orchestrator.core.tls.ssl.get_default_verify_paths")
    def test_apply_ca_bundle_fallback_uses_certifi_when_default_is_missing(
        self,
        get_default_verify_paths_mock,
        load_certifi_cafile_mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            certifi_bundle = Path(temp_dir) / "certifi.pem"
            certifi_bundle.write_text("test", encoding="utf-8")
            get_default_verify_paths_mock.return_value = _verify_paths("/broken/default.pem")
            load_certifi_cafile_mock.return_value = certifi_bundle

            with patch.dict(os.environ, {}, clear=False):
                bundle = apply_ca_bundle_fallback()
                applied = os.environ.get("SSL_CERT_FILE")

        self.assertEqual(bundle.source, "certifi-applied")
        self.assertEqual(bundle.cafile, str(certifi_bundle))
        self.assertEqual(applied, str(certifi_bundle))

    @patch("yule_orchestrator.core.tls._load_certifi_cafile")
    @patch("yule_orchestrator.core.tls.ssl.get_default_verify_paths")
    def test_resolve_ca_bundle_reports_missing_when_nothing_is_available(
        self,
        get_default_verify_paths_mock,
        load_certifi_cafile_mock,
    ) -> None:
        get_default_verify_paths_mock.return_value = _verify_paths("/broken/default.pem")
        load_certifi_cafile_mock.return_value = None

        with patch.dict(os.environ, {}, clear=False):
            bundle = resolve_ca_bundle()

        self.assertEqual(bundle.source, "missing")
        self.assertIsNone(bundle.cafile)
        self.assertFalse(bundle.exists)


def _verify_paths(cafile: str | None):
    class VerifyPaths:
        def __init__(self, resolved_cafile: str | None) -> None:
            self.openssl_cafile_env = "SSL_CERT_FILE"
            self.openssl_cafile = resolved_cafile
            self.openssl_capath_env = "SSL_CERT_DIR"
            self.openssl_capath = None
            self.cafile = resolved_cafile
            self.capath = None

    return VerifyPaths(cafile)
