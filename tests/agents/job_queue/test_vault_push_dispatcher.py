"""P0-I stage 3 commit 5 — vault push dispatcher tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.vault_push_dispatcher import (
    ACTION_VAULT_REMOTE_PUSH,
    ACTION_VAULT_RESEARCH_LOG_COMMIT,
    STATUS_INVALID_REQUEST,
    STATUS_NOT_CONFIGURED,
    STATUS_QUEUED_AUTO,
    STATUS_QUEUED_FOR_APPROVAL,
    VaultPushOutcome,
    VaultPushRequest,
    dispatch_vault_push,
)


_FIXED_NOW = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


class _VaultWorkspaceFixture(unittest.TestCase):
    """Helper that gives each test a temp vault workspace dir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace_root = self._tmp.name

    def _extra(self, **overrides) -> dict:
        base = {"vault_workspace_root": self.workspace_root}
        base.update(overrides)
        return base


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ValidationTests(_VaultWorkspaceFixture):
    def test_unknown_action_invalid(self) -> None:
        extra = self._extra()
        out = dispatch_vault_push(
            session_extra=extra,
            request=VaultPushRequest(action="unknown"),
            now=_FIXED_NOW,
        )
        self.assertEqual(out.status, STATUS_INVALID_REQUEST)
        self.assertEqual(extra["vault_push_audit"][0]["reason"], "unknown_action")

    def test_remote_push_missing_branch_not_configured(self) -> None:
        extra = self._extra()
        out = dispatch_vault_push(
            session_extra=extra,
            request=VaultPushRequest(action=ACTION_VAULT_REMOTE_PUSH),
            now=_FIXED_NOW,
        )
        self.assertEqual(out.status, STATUS_NOT_CONFIGURED)
        self.assertEqual(out.not_configured_reason, "no_branch")


# ---------------------------------------------------------------------------
# Workspace not configured
# ---------------------------------------------------------------------------


class WorkspaceNotConfiguredTests(unittest.TestCase):
    def test_no_workspace_root_in_extra_or_env(self) -> None:
        extra: dict = {}
        with patch.dict(os.environ, {"YULE_VAULT_WORKSPACE_ROOT": ""}, clear=False):
            os.environ.pop("YULE_VAULT_WORKSPACE_ROOT", None)
            out = dispatch_vault_push(
                session_extra=extra,
                request=VaultPushRequest(action=ACTION_VAULT_RESEARCH_LOG_COMMIT),
                now=_FIXED_NOW,
            )
        self.assertEqual(out.status, STATUS_NOT_CONFIGURED)
        self.assertEqual(out.not_configured_reason, "no_workspace_root")
        # Reason persisted on session.extra for status surface.
        self.assertEqual(
            extra["vault_push_not_configured_reason"], "no_workspace_root"
        )

    def test_workspace_path_does_not_exist(self) -> None:
        extra = {"vault_workspace_root": "/nonexistent/path/xyz"}
        out = dispatch_vault_push(
            session_extra=extra,
            request=VaultPushRequest(action=ACTION_VAULT_RESEARCH_LOG_COMMIT),
            now=_FIXED_NOW,
        )
        self.assertEqual(out.status, STATUS_NOT_CONFIGURED)
        self.assertEqual(out.not_configured_reason, "workspace_not_found")

    def test_env_fallback_when_extra_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extra: dict = {}
            with patch.dict(
                os.environ, {"YULE_VAULT_WORKSPACE_ROOT": tmp}
            ):
                out = dispatch_vault_push(
                    session_extra=extra,
                    request=VaultPushRequest(
                        action=ACTION_VAULT_RESEARCH_LOG_COMMIT,
                    ),
                    now=_FIXED_NOW,
                )
            # Workspace found via env → L2 auto.
            self.assertEqual(out.status, STATUS_QUEUED_AUTO)


# ---------------------------------------------------------------------------
# L2 auto — local commit always
# ---------------------------------------------------------------------------


class LocalCommitL2Tests(_VaultWorkspaceFixture):
    def test_research_log_commit_is_always_l2_auto(self) -> None:
        for mode in ("approval_required", "autonomous_merge", None):
            extra = self._extra(work_mode=mode)
            out = dispatch_vault_push(
                session_extra=extra,
                request=VaultPushRequest(
                    action=ACTION_VAULT_RESEARCH_LOG_COMMIT,
                    note_kind="research-log",
                    note_path="10-projects/foo/research-log/2026-05-14.md",
                    commit_msg="research log auto",
                ),
                now=_FIXED_NOW,
            )
            self.assertEqual(
                out.status, STATUS_QUEUED_AUTO, msg=f"mode={mode}"
            )
            self.assertEqual(out.autonomy_level, "L2")
            self.assertFalse(out.approval_required)


# ---------------------------------------------------------------------------
# Remote push — mode-dependent
# ---------------------------------------------------------------------------


class RemotePushModeBranchingTests(_VaultWorkspaceFixture):
    def test_approval_required_mode_yields_l3(self) -> None:
        extra = self._extra(work_mode="approval_required")
        out = dispatch_vault_push(
            session_extra=extra,
            request=VaultPushRequest(
                action=ACTION_VAULT_REMOTE_PUSH,
                branch="vault/main",
                commit_msg="push",
            ),
            now=_FIXED_NOW,
        )
        self.assertEqual(out.status, STATUS_QUEUED_FOR_APPROVAL)
        self.assertEqual(out.autonomy_level, "L3")
        self.assertTrue(out.approval_required)

    def test_autonomous_merge_mode_yields_l2(self) -> None:
        extra = self._extra(work_mode="autonomous_merge")
        out = dispatch_vault_push(
            session_extra=extra,
            request=VaultPushRequest(
                action=ACTION_VAULT_REMOTE_PUSH,
                branch="vault/main",
                commit_msg="push",
            ),
            now=_FIXED_NOW,
        )
        self.assertEqual(out.status, STATUS_QUEUED_AUTO)
        self.assertEqual(out.autonomy_level, "L2")
        self.assertFalse(out.approval_required)

    def test_unset_mode_defaults_to_l3(self) -> None:
        extra = self._extra()  # no work_mode
        out = dispatch_vault_push(
            session_extra=extra,
            request=VaultPushRequest(
                action=ACTION_VAULT_REMOTE_PUSH,
                branch="vault/main",
            ),
            now=_FIXED_NOW,
        )
        self.assertEqual(out.status, STATUS_QUEUED_FOR_APPROVAL)


# ---------------------------------------------------------------------------
# Audit log + code-vs-vault separation
# ---------------------------------------------------------------------------


class AuditLogSeparationTests(_VaultWorkspaceFixture):
    def test_audit_appended_per_call(self) -> None:
        extra = self._extra(work_mode="approval_required")
        dispatch_vault_push(
            session_extra=extra,
            request=VaultPushRequest(
                action=ACTION_VAULT_RESEARCH_LOG_COMMIT,
                commit_msg="commit a",
            ),
            now=_FIXED_NOW,
        )
        dispatch_vault_push(
            session_extra=extra,
            request=VaultPushRequest(
                action=ACTION_VAULT_REMOTE_PUSH,
                branch="vault/main",
            ),
            now=_FIXED_NOW,
        )
        audit = extra["vault_push_audit"]
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[0]["action"], ACTION_VAULT_RESEARCH_LOG_COMMIT)
        self.assertEqual(audit[1]["action"], ACTION_VAULT_REMOTE_PUSH)

    def test_vault_audit_separate_from_code_audit(self) -> None:
        # Stage-1 approval-matrix §3.1 — vault_push_audit must not
        # collide with code_push_audit.
        extra = self._extra(
            work_mode="approval_required",
            code_push_audit=[{"action": "branch_push", "branch": "feature/x"}],
        )
        dispatch_vault_push(
            session_extra=extra,
            request=VaultPushRequest(action=ACTION_VAULT_RESEARCH_LOG_COMMIT),
            now=_FIXED_NOW,
        )
        # Two separate audit logs.
        self.assertIn("code_push_audit", extra)
        self.assertIn("vault_push_audit", extra)
        self.assertEqual(len(extra["code_push_audit"]), 1)
        self.assertEqual(len(extra["vault_push_audit"]), 1)
        self.assertNotEqual(
            extra["code_push_audit"][0]["action"],
            extra["vault_push_audit"][0]["action"],
        )


# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------


class SummaryLineTests(unittest.TestCase):
    def test_queued_auto_line(self) -> None:
        out = VaultPushOutcome(
            status=STATUS_QUEUED_AUTO,
            action=ACTION_VAULT_RESEARCH_LOG_COMMIT,
            autonomy_level="L2",
            work_mode="autonomous_merge",
        )
        line = out.status_summary_line()
        self.assertIn("📦", line)
        self.assertIn("L2 auto", line)

    def test_not_configured_line(self) -> None:
        out = VaultPushOutcome(
            status=STATUS_NOT_CONFIGURED,
            action=ACTION_VAULT_REMOTE_PUSH,
            not_configured_reason="no_workspace_root",
        )
        line = out.status_summary_line()
        self.assertIn("⚠️", line)
        self.assertIn("not configured", line)
        self.assertIn("no_workspace_root", line)


class RoundTripTests(unittest.TestCase):
    def test_to_dict_round_trip(self) -> None:
        out = VaultPushOutcome(
            status=STATUS_QUEUED_FOR_APPROVAL,
            action=ACTION_VAULT_REMOTE_PUSH,
            autonomy_level="L3",
            work_mode="approval_required",
            approval_required=True,
        )
        payload = out.to_dict()
        self.assertEqual(payload["status"], STATUS_QUEUED_FOR_APPROVAL)
        self.assertEqual(payload["autonomy_level"], "L3")
        self.assertTrue(payload["approval_required"])


if __name__ == "__main__":
    unittest.main()
