"""P0-I stage 3 commit 7 — enforcement-layer status surface tests.

Verifies that ``SessionStatusReport`` + ``format_status_diagnostic_response``
now surface the stage-3 fields populated by:

  * ``tracking_enforcement.validate_tracking_chain``
  * ``growth_ledger.append_growth_event``
  * ``pr_slice_classifier.classify_pr_slice``
  * ``vault_push_dispatcher.dispatch_vault_push``
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.lifecycle.session_status import (
    diagnose_session,
)
from yule_discord.engineering_conversation import (
    format_status_diagnostic_response,
)


def _session(extra=None):
    return SimpleNamespace(
        session_id="sess-p0i-1",
        state=SimpleNamespace(value="needs_research"),
        task_type="research",
        prompt="P0-I stage 3 작업",
        extra=dict(extra or {}),
        role_sequence=(),
        write_requested=False,
        write_blocked_reason=None,
        progress_notes=(),
    )


# ---------------------------------------------------------------------------
# Tracking surface
# ---------------------------------------------------------------------------


class TrackingSurfaceTests(unittest.TestCase):
    def test_tracking_ok_renders_check(self) -> None:
        extra = {
            "tracking_validation": {
                "status": "ok",
                "blocked": False,
                "missing_links": [],
                "allowed_via_contract_exception": False,
            }
        }
        report = diagnose_session(_session(extra=extra))
        self.assertEqual(report.tracking_status, "ok")
        self.assertFalse(report.tracking_blocked)
        self.assertIn("✅", report.tracking_summary or "")

        body = format_status_diagnostic_response(_session(extra=extra))
        self.assertIn("tracking chain: ✅ complete", body)

    def test_tracking_blocked_lists_missing(self) -> None:
        extra = {
            "tracking_validation": {
                "status": "needs_issue",
                "blocked": True,
                "missing_links": ["issue"],
                "allowed_via_contract_exception": False,
            },
            "tracking_blocked_reason": "추적 가능한 issue 가 없어요.",
        }
        report = diagnose_session(_session(extra=extra))
        self.assertEqual(report.tracking_status, "needs_issue")
        self.assertTrue(report.tracking_blocked)
        body = format_status_diagnostic_response(_session(extra=extra))
        self.assertIn("⚠️", body)
        self.assertIn("issue", body)

    def test_tracking_standalone_renders_info(self) -> None:
        extra = {
            "tracking_validation": {
                "status": "standalone_no_target",
                "blocked": False,
            }
        }
        body = format_status_diagnostic_response(_session(extra=extra))
        self.assertIn("ℹ️", body)
        self.assertIn("GitHub target 없음", body)

    def test_tracking_contract_exception_marked(self) -> None:
        extra = {
            "tracking_validation": {
                "status": "needs_branch",
                "blocked": True,
                "missing_links": ["branch"],
                "allowed_via_contract_exception": True,
            }
        }
        body = format_status_diagnostic_response(_session(extra=extra))
        self.assertIn("RepoContract 예외", body)


# ---------------------------------------------------------------------------
# Growth ledger surface
# ---------------------------------------------------------------------------


class GrowthLedgerSurfaceTests(unittest.TestCase):
    def test_empty_ledger_no_line(self) -> None:
        body = format_status_diagnostic_response(_session(extra={}))
        self.assertNotIn("🌱", body)

    def test_ledger_with_events_emits_seedling(self) -> None:
        extra = {
            "growth_ledger": [
                {
                    "kind": "reference_used",
                    "summary": "RFC 7519",
                    "recorded_at": "2026-05-14T00:00:00+00:00",
                },
                {
                    "kind": "decision_made",
                    "summary": "use approval mode",
                    "recorded_at": "2026-05-14T00:00:00+00:00",
                },
            ]
        }
        body = format_status_diagnostic_response(_session(extra=extra))
        self.assertIn("🌱", body)

    def test_promotion_candidates_count_surfaced(self) -> None:
        extra = {
            "growth_ledger": [
                {
                    "kind": "regret",
                    "summary": "x",
                    "pattern_tag": "repeat",
                    "recorded_at": "2026-05-14T00:00:00+00:00",
                }
                for _ in range(3)
            ],
            "growth_promotion_candidates": [
                {"pattern_tag": "repeat", "occurrence_count": 3}
            ],
        }
        report = diagnose_session(_session(extra=extra))
        self.assertEqual(report.growth_promotion_candidate_count, 1)


# ---------------------------------------------------------------------------
# PR slice classification surface
# ---------------------------------------------------------------------------


class PRSliceSurfaceTests(unittest.TestCase):
    def test_slice_primary_rendered(self) -> None:
        extra = {
            "pr_slice_classification": {
                "primary_slice": "create",
                "size_warning": False,
            }
        }
        report = diagnose_session(_session(extra=extra))
        self.assertEqual(report.pr_slice_primary, "create")
        self.assertFalse(report.pr_size_warning)

        body = format_status_diagnostic_response(_session(extra=extra))
        self.assertIn("PR slice: `create`", body)

    def test_size_warning_rendered(self) -> None:
        extra = {
            "pr_slice_classification": {
                "primary_slice": "update",
                "size_warning": True,
                "changed_lines_excluding_tests": 1200,
            }
        }
        body = format_status_diagnostic_response(_session(extra=extra))
        self.assertIn("⚠️", body)
        self.assertIn("800", body)


# ---------------------------------------------------------------------------
# Vault push surface
# ---------------------------------------------------------------------------


class VaultPushSurfaceTests(unittest.TestCase):
    def test_queued_auto_renders_box(self) -> None:
        extra = {
            "vault_push_audit": [
                {
                    "action": "vault_research_log_commit",
                    "status": "queued_auto",
                    "autonomy_level": "L2",
                    "work_mode": "autonomous_merge",
                    "recorded_at": "2026-05-14T00:00:00+00:00",
                }
            ]
        }
        report = diagnose_session(_session(extra=extra))
        self.assertEqual(report.vault_push_audit_count, 1)

        body = format_status_diagnostic_response(_session(extra=extra))
        self.assertIn("📦", body)
        self.assertIn("vault_research_log_commit", body)

    def test_queued_for_approval_renders_envelope(self) -> None:
        extra = {
            "vault_push_audit": [
                {
                    "action": "vault_remote_push",
                    "status": "queued_for_approval",
                    "autonomy_level": "L3",
                    "work_mode": "approval_required",
                    "recorded_at": "2026-05-14T00:00:00+00:00",
                }
            ]
        }
        body = format_status_diagnostic_response(_session(extra=extra))
        self.assertIn("📬", body)
        self.assertIn("approval", body)

    def test_not_configured_surfaces_reason(self) -> None:
        extra = {
            "vault_push_audit": [
                {
                    "action": "vault_research_log_commit",
                    "status": "not_configured",
                    "not_configured_reason": "no_workspace_root",
                    "recorded_at": "2026-05-14T00:00:00+00:00",
                }
            ],
            "vault_push_not_configured_reason": "no_workspace_root",
        }
        report = diagnose_session(_session(extra=extra))
        self.assertEqual(
            report.vault_push_not_configured_reason, "no_workspace_root"
        )

        body = format_status_diagnostic_response(_session(extra=extra))
        self.assertIn("⚠️", body)
        self.assertIn("not configured", body)
        self.assertIn("no_workspace_root", body)


# ---------------------------------------------------------------------------
# No-regression — empty extras still renders standard body
# ---------------------------------------------------------------------------


class NoRegressionTests(unittest.TestCase):
    def test_empty_extras_no_new_lines(self) -> None:
        body = format_status_diagnostic_response(_session(extra={}))
        # Old-shape session keeps the standard lines without P0-I additions.
        self.assertIn("세션:", body)
        self.assertNotIn("tracking chain:", body)
        self.assertNotIn("🌱", body)
        self.assertNotIn("PR slice:", body)


if __name__ == "__main__":
    unittest.main()
