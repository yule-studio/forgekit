"""GitHub App doctor tests — A-G1.

Pin offline + live doctor behaviour using fake HTTP / fake signer.
Tests never read .env.local and never touch the real network.

The fake HTTP client both proves doctor never calls the network on
``live=False`` (call counts must stay 0) and lets us drive each
4xx/5xx mapping path with deterministic responses.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, List, Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.github_app import (
    CHECK_ENV_CONFIG,
    CHECK_LIVE_INSTALLATION_TOKEN,
    CHECK_LIVE_REPO_ACCESS,
    CHECK_PLACEHOLDER_APP_ID,
    CHECK_PRIVATE_KEY_LOADABLE,
    CHECK_PRIVATE_KEY_PATH,
    CHECK_STATUS_FAIL,
    CHECK_STATUS_OK,
    CHECK_STATUS_SKIP,
    CHECK_STATUS_WARN,
    DOCTOR_OVERALL_FAIL,
    DOCTOR_OVERALL_OK,
    DOCTOR_OVERALL_WARN,
    ENV_GITHUB_APP_ID,
    ENV_GITHUB_APP_INSTALLATION_ID,
    ENV_GITHUB_APP_PRIVATE_KEY_PATH,
    ENV_GITHUB_DEFAULT_DRY_RUN,
    ENV_GITHUB_OWNER,
    ENV_GITHUB_REPO,
    HTTPResponse,
    doctor,
    redact_secret_like,
)
from yule_engineering.github_app.auth import fake_signer


class _RecordingHTTP:
    """Fake HTTP that records calls + returns scripted responses."""

    def __init__(self, responses: Mapping[str, HTTPResponse]) -> None:
        # responses keyed by HTTP method; first match wins.
        self.responses: Mapping[str, HTTPResponse] = responses
        self.calls: List[tuple] = []

    def post(self, url: str, *, headers, body) -> HTTPResponse:
        self.calls.append(("POST", url, dict(headers), dict(body)))
        return self.responses.get("POST", HTTPResponse(status=500))

    def get(self, url: str, *, headers) -> HTTPResponse:
        self.calls.append(("GET", url, dict(headers)))
        return self.responses.get("GET", HTTPResponse(status=500))


def _make_pem(tmp: Path, *, mode: int = 0o600) -> Path:
    pem = tmp / "key.pem"
    pem.write_bytes(
        b"-----BEGIN PRIVATE KEY-----\nfake-bytes\n-----END PRIVATE KEY-----\n"
    )
    os.chmod(pem, mode)
    return pem


def _good_env(pem_path: str, **overrides: str) -> dict:
    base = {
        ENV_GITHUB_APP_ID: "987654",
        ENV_GITHUB_APP_INSTALLATION_ID: "130485504",
        ENV_GITHUB_APP_PRIVATE_KEY_PATH: pem_path,
        ENV_GITHUB_OWNER: "yule-studio",
        ENV_GITHUB_REPO: "yule-studio-agent",
        ENV_GITHUB_DEFAULT_DRY_RUN: "true",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Offline path
# ---------------------------------------------------------------------------


class OfflineDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_clean_env_offline_overall_ok(self) -> None:
        pem = _make_pem(self.tmp)
        result = doctor(env=_good_env(str(pem)), live=False)
        self.assertEqual(result.overall, DOCTOR_OVERALL_OK)
        self.assertFalse(result.live)
        # Each non-network check ran with status=ok.
        for name in (
            CHECK_ENV_CONFIG,
            CHECK_PLACEHOLDER_APP_ID,
            CHECK_PRIVATE_KEY_PATH,
            CHECK_PRIVATE_KEY_LOADABLE,
        ):
            check = result.find(name)
            self.assertIsNotNone(check)
            assert check is not None
            self.assertEqual(check.status, CHECK_STATUS_OK)
        # Live checks never appended.
        self.assertIsNone(result.find(CHECK_LIVE_INSTALLATION_TOKEN))
        self.assertIsNone(result.find(CHECK_LIVE_REPO_ACCESS))

    def test_offline_does_not_call_http_or_signer(self) -> None:
        pem = _make_pem(self.tmp)
        http = _RecordingHTTP(responses={})
        signer_calls: List[Any] = []

        def loud_signer(payload, key):
            signer_calls.append("signer-was-called")
            return b"\x00"

        result = doctor(
            env=_good_env(str(pem)), live=False, http=http, signer=loud_signer
        )
        self.assertEqual(http.calls, [])
        self.assertEqual(signer_calls, [])
        self.assertEqual(result.overall, DOCTOR_OVERALL_OK)

    def test_missing_env_fails_with_friendly_message(self) -> None:
        env = _good_env("/tmp/x")
        env.pop(ENV_GITHUB_APP_ID)
        result = doctor(env=env, live=False)
        self.assertEqual(result.overall, DOCTOR_OVERALL_FAIL)
        cfg = result.find(CHECK_ENV_CONFIG)
        assert cfg is not None
        self.assertEqual(cfg.status, CHECK_STATUS_FAIL)
        self.assertIn(ENV_GITHUB_APP_ID, str(cfg.detail))

    def test_placeholder_app_id_fails(self) -> None:
        pem = _make_pem(self.tmp)
        env = _good_env(str(pem), **{ENV_GITHUB_APP_ID: "123456"})
        result = doctor(env=env, live=False)
        self.assertEqual(result.overall, DOCTOR_OVERALL_FAIL)
        check = result.find(CHECK_PLACEHOLDER_APP_ID)
        assert check is not None
        self.assertEqual(check.status, CHECK_STATUS_FAIL)
        self.assertIn("placeholder", check.message)

    def test_missing_pem_fails(self) -> None:
        env = _good_env("/tmp/never-exists.pem")
        result = doctor(env=env, live=False)
        self.assertEqual(result.overall, DOCTOR_OVERALL_FAIL)
        check = result.find(CHECK_PRIVATE_KEY_PATH)
        assert check is not None
        self.assertEqual(check.status, CHECK_STATUS_FAIL)

    def test_world_writable_pem_warns_not_fails(self) -> None:
        pem = _make_pem(self.tmp, mode=0o646)
        result = doctor(env=_good_env(str(pem)), live=False)
        self.assertEqual(result.overall, DOCTOR_OVERALL_WARN)
        check = result.find(CHECK_PRIVATE_KEY_PATH)
        assert check is not None
        self.assertEqual(check.status, CHECK_STATUS_WARN)
        self.assertIn("world-writable", check.message)


# ---------------------------------------------------------------------------
# Live path — placeholder / pem-fail short-circuit before network
# ---------------------------------------------------------------------------


class LiveShortCircuitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_placeholder_app_id_skips_live_without_network(self) -> None:
        pem = _make_pem(self.tmp)
        env = _good_env(str(pem), **{ENV_GITHUB_APP_ID: "123456"})
        http = _RecordingHTTP(responses={})
        result = doctor(env=env, live=True, http=http, signer=fake_signer)
        self.assertEqual(http.calls, [])
        live = result.find(CHECK_LIVE_INSTALLATION_TOKEN)
        assert live is not None
        self.assertEqual(live.status, CHECK_STATUS_SKIP)
        repo = result.find(CHECK_LIVE_REPO_ACCESS)
        assert repo is not None
        self.assertEqual(repo.status, CHECK_STATUS_SKIP)

    def test_missing_pem_skips_live_without_network(self) -> None:
        env = _good_env("/tmp/never.pem")
        http = _RecordingHTTP(responses={})
        result = doctor(env=env, live=True, http=http, signer=fake_signer)
        self.assertEqual(http.calls, [])
        live = result.find(CHECK_LIVE_INSTALLATION_TOKEN)
        assert live is not None
        self.assertEqual(live.status, CHECK_STATUS_SKIP)


# ---------------------------------------------------------------------------
# Live path — actual fake HTTP responses
# ---------------------------------------------------------------------------


class LiveHTTPMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.pem = _make_pem(self.tmp)
        self.env = _good_env(str(self.pem))

    def _run(self, *, post_status: int, post_body=None, get_status: int = 200, get_body=None):
        http = _RecordingHTTP(
            responses={
                "POST": HTTPResponse(
                    status=post_status,
                    body=post_body if post_body is not None else (
                        {
                            "token": "ghs_FAKE_TOKEN_FOR_TEST_ONLY",
                            "expires_at": "2026-05-08T12:00:00Z",
                            "permissions": {"contents": "write"},
                        }
                        if post_status == 201
                        else {"message": "GitHub said no"}
                    ),
                ),
                "GET": HTTPResponse(
                    status=get_status,
                    body=get_body if get_body is not None else (
                        {
                            "full_name": "yule-studio/yule-studio-agent",
                            "default_branch": "main",
                            "private": False,
                            "permissions": {"push": True, "pull": True},
                        }
                        if get_status == 200
                        else {"message": "GitHub said no"}
                    ),
                ),
            }
        )
        return http, doctor(env=self.env, live=True, http=http, signer=fake_signer)

    def test_201_then_200_overall_ok(self) -> None:
        http, result = self._run(post_status=201, get_status=200)
        self.assertEqual(result.overall, DOCTOR_OVERALL_OK)
        # Two HTTP calls — POST issuance + GET repo metadata.
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(http.calls[0][0], "POST")
        self.assertEqual(http.calls[1][0], "GET")
        # Token check ok with redacted summary, no raw token in message.
        token_check = result.find(CHECK_LIVE_INSTALLATION_TOKEN)
        assert token_check is not None
        self.assertEqual(token_check.status, CHECK_STATUS_OK)
        self.assertNotIn("ghs_FAKE_TOKEN_FOR_TEST_ONLY", token_check.message)

    def test_post_401_marks_auth_failure(self) -> None:
        _, result = self._run(post_status=401)
        token_check = result.find(CHECK_LIVE_INSTALLATION_TOKEN)
        assert token_check is not None
        self.assertEqual(token_check.status, CHECK_STATUS_FAIL)
        self.assertEqual(token_check.detail.get("kind"), "auth")
        repo_check = result.find(CHECK_LIVE_REPO_ACCESS)
        assert repo_check is not None
        self.assertEqual(repo_check.status, CHECK_STATUS_SKIP)

    def test_post_403_marks_permission_failure(self) -> None:
        _, result = self._run(post_status=403)
        token_check = result.find(CHECK_LIVE_INSTALLATION_TOKEN)
        assert token_check is not None
        self.assertEqual(token_check.detail.get("kind"), "permission")

    def test_post_404_marks_not_found(self) -> None:
        _, result = self._run(post_status=404)
        token_check = result.find(CHECK_LIVE_INSTALLATION_TOKEN)
        assert token_check is not None
        self.assertEqual(token_check.detail.get("kind"), "not_found")

    def test_post_502_marks_server_failure(self) -> None:
        _, result = self._run(post_status=502)
        token_check = result.find(CHECK_LIVE_INSTALLATION_TOKEN)
        assert token_check is not None
        self.assertEqual(token_check.detail.get("kind"), "server")

    def test_get_404_after_201_marks_repo_not_found(self) -> None:
        _, result = self._run(post_status=201, get_status=404)
        token_check = result.find(CHECK_LIVE_INSTALLATION_TOKEN)
        assert token_check is not None
        self.assertEqual(token_check.status, CHECK_STATUS_OK)
        repo_check = result.find(CHECK_LIVE_REPO_ACCESS)
        assert repo_check is not None
        self.assertEqual(repo_check.detail.get("kind"), "not_found")


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


class RedactionTests(unittest.TestCase):
    def test_token_pattern_redacted(self) -> None:
        text = "got token ghs_aaaaaaaaaaaaaaaaaaaaaaaa back from GitHub"
        out = redact_secret_like(text)
        self.assertNotIn("ghs_aaaaaaaaaaaaaaaaaaaaaaaa", out)
        self.assertIn("<redacted>", out)

    def test_authorization_header_redacted(self) -> None:
        text = "request had Authorization: Bearer abc123def456ghi789"
        out = redact_secret_like(text)
        self.assertNotIn("Bearer abc123", out)
        self.assertIn("<redacted>", out)

    def test_pem_block_redacted(self) -> None:
        text = (
            "key was -----BEGIN RSA PRIVATE KEY-----\n"
            "SECRET-LEAKED\n"
            "-----END RSA PRIVATE KEY-----\n"
            "trailing text"
        )
        out = redact_secret_like(text)
        self.assertNotIn("SECRET-LEAKED", out)
        self.assertIn("<redacted>", out)
        self.assertIn("trailing text", out)


if __name__ == "__main__":
    unittest.main()
