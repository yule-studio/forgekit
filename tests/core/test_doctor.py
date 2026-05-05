from __future__ import annotations

import unittest
from unittest.mock import patch
import urllib.error

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.diagnostics.doctor import _check_discord_tls


class DoctorDiscordTLSTestCase(unittest.TestCase):
    @patch("yule_orchestrator.diagnostics.doctor._read_json")
    @patch("yule_orchestrator.diagnostics.doctor.apply_ca_bundle_fallback")
    @patch("yule_orchestrator.diagnostics.doctor.resolve_ca_bundle")
    def test_check_discord_tls_reports_ok_with_valid_bundle(
        self,
        resolve_ca_bundle_mock,
        apply_ca_bundle_fallback_mock,
        read_json_mock,
    ) -> None:
        resolve_ca_bundle_mock.return_value = _bundle(
            "default",
            "/tmp/default.pem",
            "using OpenSSL default cafile",
        )
        apply_ca_bundle_fallback_mock.return_value = _bundle(
            "default",
            "/tmp/default.pem",
            "using OpenSSL default cafile",
        )
        read_json_mock.return_value = {"url": "wss://gateway.discord.gg"}

        check = _check_discord_tls()

        self.assertEqual(check.status, "OK")
        self.assertEqual(check.name, "discord tls")

    @patch("yule_orchestrator.diagnostics.doctor.resolve_ca_bundle")
    def test_check_discord_tls_fails_when_bundle_is_missing(self, resolve_ca_bundle_mock) -> None:
        resolve_ca_bundle_mock.return_value = _bundle(
            "missing",
            None,
            "no CA bundle is available",
            exists=False,
        )

        check = _check_discord_tls()

        self.assertEqual(check.status, "FAIL")
        self.assertIn("no CA bundle", check.detail)

    @patch("yule_orchestrator.diagnostics.doctor._read_json")
    @patch("yule_orchestrator.diagnostics.doctor.apply_ca_bundle_fallback")
    @patch("yule_orchestrator.diagnostics.doctor.resolve_ca_bundle")
    def test_check_discord_tls_fails_on_url_error(
        self,
        resolve_ca_bundle_mock,
        apply_ca_bundle_fallback_mock,
        read_json_mock,
    ) -> None:
        resolve_ca_bundle_mock.return_value = _bundle(
            "certifi",
            "/tmp/certifi.pem",
            "certifi fallback is available",
        )
        apply_ca_bundle_fallback_mock.return_value = _bundle(
            "certifi-applied",
            "/tmp/certifi.pem",
            "using certifi CA bundle",
        )
        read_json_mock.side_effect = urllib.error.URLError("certificate verify failed")

        check = _check_discord_tls()

        self.assertEqual(check.status, "FAIL")
        self.assertIn("certificate verify failed", check.detail)


def _bundle(source: str, cafile: str | None, detail: str, exists: bool = True):
    from yule_orchestrator.core.tls import TLSCABundle

    return TLSCABundle(
        cafile=cafile,
        source=source,
        detail=detail,
        exists=exists,
    )
