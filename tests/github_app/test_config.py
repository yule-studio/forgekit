"""GitHub App config tests — A-G1.

Pin the env contract reader + private-key-path validator. Tests
never read .env.local and never expose pem contents.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.github_app import (
    ENV_GITHUB_APP_ID,
    ENV_GITHUB_APP_INSTALLATION_ID,
    ENV_GITHUB_APP_PRIVATE_KEY_PATH,
    ENV_GITHUB_DEFAULT_DRY_RUN,
    ENV_GITHUB_OWNER,
    ENV_GITHUB_REPO,
    GitHubAppConfig,
    GitHubAppConfigError,
    PLACEHOLDER_APP_IDS,
    PrivateKeyPathProblem,
    validate_private_key_path,
)


def _good_env(**overrides: str) -> dict:
    base = {
        ENV_GITHUB_APP_ID: "987654",
        ENV_GITHUB_APP_INSTALLATION_ID: "130485504",
        ENV_GITHUB_APP_PRIVATE_KEY_PATH: "/tmp/never-read.pem",
        ENV_GITHUB_OWNER: "yule-studio",
        ENV_GITHUB_REPO: "yule-studio-agent",
        ENV_GITHUB_DEFAULT_DRY_RUN: "true",
    }
    base.update(overrides)
    return base


class FromEnvTests(unittest.TestCase):
    def test_full_env_resolves_config(self) -> None:
        config = GitHubAppConfig.from_env(_good_env())
        self.assertEqual(config.app_id, "987654")
        self.assertEqual(config.installation_id, "130485504")
        self.assertEqual(config.owner, "yule-studio")
        self.assertEqual(config.repo, "yule-studio-agent")
        self.assertTrue(config.default_dry_run)
        self.assertEqual(config.repo_full_name, "yule-studio/yule-studio-agent")

    def test_default_dry_run_default_is_true_when_unset(self) -> None:
        env = _good_env()
        env.pop(ENV_GITHUB_DEFAULT_DRY_RUN)
        config = GitHubAppConfig.from_env(env)
        self.assertTrue(config.default_dry_run)

    def test_default_dry_run_parses_falsy_strings(self) -> None:
        for raw in ("0", "false", "no", "off", "FALSE"):
            with self.subTest(raw=raw):
                env = _good_env(**{ENV_GITHUB_DEFAULT_DRY_RUN: raw})
                config = GitHubAppConfig.from_env(env)
                self.assertFalse(config.default_dry_run)

    def test_default_dry_run_invalid_raises(self) -> None:
        env = _good_env(**{ENV_GITHUB_DEFAULT_DRY_RUN: "maybe"})
        with self.assertRaises(GitHubAppConfigError) as ctx:
            GitHubAppConfig.from_env(env)
        self.assertEqual(ctx.exception.key, ENV_GITHUB_DEFAULT_DRY_RUN)

    def test_missing_app_id_raises_with_key(self) -> None:
        env = _good_env()
        env.pop(ENV_GITHUB_APP_ID)
        with self.assertRaises(GitHubAppConfigError) as ctx:
            GitHubAppConfig.from_env(env)
        self.assertEqual(ctx.exception.key, ENV_GITHUB_APP_ID)

    def test_blank_owner_raises(self) -> None:
        env = _good_env(**{ENV_GITHUB_OWNER: "   "})
        with self.assertRaises(GitHubAppConfigError) as ctx:
            GitHubAppConfig.from_env(env)
        self.assertEqual(ctx.exception.key, ENV_GITHUB_OWNER)

    def test_each_required_key_individually_missing_raises(self) -> None:
        for key in (
            ENV_GITHUB_APP_ID,
            ENV_GITHUB_APP_INSTALLATION_ID,
            ENV_GITHUB_APP_PRIVATE_KEY_PATH,
            ENV_GITHUB_OWNER,
            ENV_GITHUB_REPO,
        ):
            with self.subTest(key=key):
                env = _good_env()
                env.pop(key)
                with self.assertRaises(GitHubAppConfigError) as ctx:
                    GitHubAppConfig.from_env(env)
                self.assertEqual(ctx.exception.key, key)


class PlaceholderAppIdTests(unittest.TestCase):
    def test_placeholder_123456_is_recognised(self) -> None:
        config = GitHubAppConfig.from_env(_good_env(**{ENV_GITHUB_APP_ID: "123456"}))
        self.assertTrue(config.is_placeholder_app_id())

    def test_real_id_not_flagged(self) -> None:
        config = GitHubAppConfig.from_env(_good_env(**{ENV_GITHUB_APP_ID: "987654"}))
        self.assertFalse(config.is_placeholder_app_id())

    def test_each_placeholder_recognised(self) -> None:
        for ph in PLACEHOLDER_APP_IDS:
            with self.subTest(ph=ph):
                config = GitHubAppConfig.from_env(_good_env(**{ENV_GITHUB_APP_ID: ph}))
                self.assertTrue(config.is_placeholder_app_id())


class ReprRedactionTests(unittest.TestCase):
    def test_repr_does_not_leak_path(self) -> None:
        config = GitHubAppConfig.from_env(
            _good_env(**{ENV_GITHUB_APP_PRIVATE_KEY_PATH: "/secret/path/yule.pem"})
        )
        self.assertNotIn("/secret/path/yule.pem", repr(config))
        self.assertIn("<configured>", repr(config))


class PrivateKeyPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_missing_path_raises_fail(self) -> None:
        with self.assertRaises(PrivateKeyPathProblem) as ctx:
            validate_private_key_path(str(self.tmp / "nope.pem"))
        self.assertEqual(ctx.exception.severity, "fail")

    def test_empty_path_raises(self) -> None:
        with self.assertRaises(PrivateKeyPathProblem) as ctx:
            validate_private_key_path("   ")
        self.assertEqual(ctx.exception.severity, "fail")

    def test_directory_instead_of_file_fails(self) -> None:
        with self.assertRaises(PrivateKeyPathProblem) as ctx:
            validate_private_key_path(str(self.tmp))
        self.assertEqual(ctx.exception.severity, "fail")
        self.assertIn("not a regular file", str(ctx.exception))

    def test_world_writable_warns_not_fails(self) -> None:
        pem = self.tmp / "key.pem"
        pem.write_bytes(b"-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")
        os.chmod(pem, 0o646)  # world-writable
        with self.assertRaises(PrivateKeyPathProblem) as ctx:
            validate_private_key_path(str(pem))
        self.assertEqual(ctx.exception.severity, "warn")
        self.assertIn("world-writable", str(ctx.exception))

    def test_group_writable_warns(self) -> None:
        pem = self.tmp / "key.pem"
        pem.write_bytes(b"-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")
        os.chmod(pem, 0o660)  # group-writable, no other-write
        with self.assertRaises(PrivateKeyPathProblem) as ctx:
            validate_private_key_path(str(pem))
        self.assertEqual(ctx.exception.severity, "warn")

    def test_owner_only_passes(self) -> None:
        pem = self.tmp / "key.pem"
        pem.write_bytes(b"-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")
        os.chmod(pem, 0o600)
        # No exception expected.
        validate_private_key_path(str(pem))

    def test_repr_for_problem_does_not_include_pem_bytes(self) -> None:
        # The error message references the path (operator hint) but
        # never the file contents.
        pem = self.tmp / "key.pem"
        pem.write_bytes(b"-----BEGIN PRIVATE KEY-----\nSECRET-DO-NOT-LEAK\n-----END PRIVATE KEY-----\n")
        os.chmod(pem, 0o646)
        with self.assertRaises(PrivateKeyPathProblem) as ctx:
            validate_private_key_path(str(pem))
        self.assertNotIn("SECRET-DO-NOT-LEAK", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
