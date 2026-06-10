"""F8 / #99 — vault_auto_push hard rail unit tests.

env OFF default / 보호 브랜치 차단 / status≠done skip / dry_run
no-op / PasteGuard 통합 / 변경 없을 때 skip — 6 hard rail 을 ≤12
케이스로 핀.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.obsidian.vault_auto_push import (
    AutoPushVerdict,
    DEFAULT_AUTO_BRANCH,
    ENV_AUTOPUSH_ENABLED,
    ENV_VAULT_BRANCH,
    ENV_VAULT_REPO_ROOT,
    push_vault_if_ready,
)


@dataclass
class _FakeEvent:
    status: str = "done"
    job_id: str = "job-1"
    reason: str = "all good"


class EnvFlagTests(unittest.TestCase):
    def test_default_env_is_off(self) -> None:
        verdict = push_vault_if_ready(
            completion_event=_FakeEvent(),
            env={},
        )
        self.assertFalse(verdict.performed)
        self.assertIn(ENV_AUTOPUSH_ENABLED, verdict.skipped_reason or "")
        self.assertEqual(verdict.branch, DEFAULT_AUTO_BRANCH)

    def test_flag_false_is_off(self) -> None:
        verdict = push_vault_if_ready(
            completion_event=_FakeEvent(),
            env={ENV_AUTOPUSH_ENABLED: "false"},
        )
        self.assertFalse(verdict.performed)
        self.assertIsNotNone(verdict.skipped_reason)


class StatusGateTests(unittest.TestCase):
    def test_non_done_status_is_skipped(self) -> None:
        verdict = push_vault_if_ready(
            completion_event=_FakeEvent(status="blocked"),
            env={ENV_AUTOPUSH_ENABLED: "true"},
        )
        self.assertFalse(verdict.performed)
        self.assertIn("status=", verdict.skipped_reason or "")


class ProtectedBranchTests(unittest.TestCase):
    def test_main_branch_is_blocked(self) -> None:
        verdict = push_vault_if_ready(
            completion_event=_FakeEvent(),
            env={
                ENV_AUTOPUSH_ENABLED: "true",
                ENV_VAULT_BRANCH: "main",
            },
        )
        self.assertFalse(verdict.performed)
        self.assertIsNotNone(verdict.blocked_reason)
        self.assertIn("protected", verdict.blocked_reason or "")

    def test_master_branch_is_blocked(self) -> None:
        verdict = push_vault_if_ready(
            completion_event=_FakeEvent(),
            env={
                ENV_AUTOPUSH_ENABLED: "true",
                ENV_VAULT_BRANCH: "master",
            },
        )
        self.assertFalse(verdict.performed)
        self.assertIn("protected", verdict.blocked_reason or "")


class RepoRootRequiredTests(unittest.TestCase):
    def test_missing_repo_root_is_blocked(self) -> None:
        verdict = push_vault_if_ready(
            completion_event=_FakeEvent(),
            env={ENV_AUTOPUSH_ENABLED: "true"},
        )
        self.assertFalse(verdict.performed)
        self.assertIn(ENV_VAULT_REPO_ROOT, verdict.blocked_reason or "")

    def test_nonexistent_repo_root_is_blocked(self) -> None:
        verdict = push_vault_if_ready(
            completion_event=_FakeEvent(),
            env={
                ENV_AUTOPUSH_ENABLED: "true",
                ENV_VAULT_REPO_ROOT: "/nonexistent/path/does/not/exist",
            },
        )
        self.assertFalse(verdict.performed)
        self.assertIn("invalid", verdict.blocked_reason or "")


class DryRunTests(unittest.TestCase):
    def test_dry_run_skips_when_all_rails_pass(self) -> None:
        verdict = push_vault_if_ready(
            completion_event=_FakeEvent(),
            vault_repo_root=Path("/tmp"),
            dry_run=True,
            env={ENV_AUTOPUSH_ENABLED: "true"},
        )
        self.assertFalse(verdict.performed)
        self.assertIn("dry_run", verdict.skipped_reason or "")
        self.assertEqual(verdict.branch, DEFAULT_AUTO_BRANCH)
        self.assertEqual(verdict.commit_hash, "")


class PasteGuardIntegrationTests(unittest.TestCase):
    def test_pasteguard_blocks_when_secret_in_reason(self) -> None:
        secret = "sk-ant-" + "A" * 40 + "ZZ"
        verdict = push_vault_if_ready(
            completion_event=_FakeEvent(reason=secret),
            vault_repo_root=Path("/tmp"),
            dry_run=True,
            env={ENV_AUTOPUSH_ENABLED: "true"},
        )
        # PasteGuard 가 redact 하므로 push 자체는 진행되지만 (dry_run 단계),
        # commit message 의 실 secret 은 verdict 에 남으면 안 된다.
        self.assertNotIn(secret, repr(verdict))
        self.assertEqual(verdict.commit_hash, "")  # dry_run


class VerdictSurfaceTests(unittest.TestCase):
    def test_verdict_fields_are_immutable_dataclass(self) -> None:
        v = AutoPushVerdict(performed=False, branch="x", skipped_reason="r")
        with self.assertRaises(Exception):
            v.performed = True  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
