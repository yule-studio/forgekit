"""Phase E tests — session-state diagnostic helper.

These exercise :mod:`yule_orchestrator.agents.session_status` so the
detect/report/propose rules stay pinned. Each test maps to one of the
operator-facing complaints from Phase E:

- research_pack 있음 but open-call 없음
- open-call 있음 but role_turn 없음
- role_turn 있음 but synthesis 없음
- synthesis 있음 but Obsidian proposal 없음
- pending Obsidian approval
- Obsidian write failed

All inputs are synthesized in-memory — the helper does not touch the
SQLite cache, Discord, or the network.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.session_status import (
    FORUM_PUBLISH_FAILED,
    OBSIDIAN_PENDING_APPROVAL,
    OBSIDIAN_PROPOSAL_MISSING,
    OBSIDIAN_WRITE_FAILED,
    OPEN_CALL_FAILED,
    OPEN_CALL_MISSING,
    RESEARCH_LOOP_ERROR,
    RESEARCH_PACK_MISSING,
    ROLE_TURN_MISSING,
    SESSION_CLOSED,
    SYNTHESIS_MISSING,
    diagnose_session,
    render_diagnostic_summary,
    render_member_bot_summary,
)
from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState


def _session(**overrides: Any) -> WorkflowSession:
    base = dict(
        session_id="abc123def456",
        prompt="운영 리서치 검토 작업",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=datetime(2026, 5, 5, 10, 0),
        updated_at=datetime(2026, 5, 6, 9, 0),
        channel_id=1001,
        user_id=2002,
        thread_id=3003,
    )
    base.update(overrides)
    return WorkflowSession(**base)


class SignalCodesTests(unittest.TestCase):
    """Each Phase E condition gets a stable code that the supervisor /
    discord layer can match without depending on Korean phrasing."""

    def test_no_research_pack_emits_info_signal(self) -> None:
        report = diagnose_session(_session())
        self.assertTrue(report.has_signal(RESEARCH_PACK_MISSING))
        self.assertEqual(
            report.signal(RESEARCH_PACK_MISSING).severity, "info"
        )

    def test_research_pack_without_open_call_is_stale(self) -> None:
        session = _session(
            extra={"research_pack": {"title": "Stripe pricing"}},
        )
        report = diagnose_session(session)
        self.assertTrue(report.has_signal(OPEN_CALL_MISSING))
        signal = report.signal(OPEN_CALL_MISSING)
        self.assertEqual(signal.severity, "stale")
        self.assertIn("publisher", signal.propose or "")

    def test_forum_publish_error_promotes_to_failed(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "forum_publish_error": "starter 4000자 초과",
            }
        )
        report = diagnose_session(session)
        self.assertTrue(report.has_signal(FORUM_PUBLISH_FAILED))
        # OPEN_CALL_MISSING must NOT also fire — the explicit failure
        # supersedes the generic "is the publisher even running?" hint.
        self.assertFalse(report.has_signal(OPEN_CALL_MISSING))

    def test_member_bots_kickoff_failure_emits_open_call_failed(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "forum_kickoff_posted": False,
                "forum_kickoff_error": "rate limit 503",
            }
        )
        report = diagnose_session(session)
        self.assertTrue(report.has_signal(OPEN_CALL_FAILED))
        signal = report.signal(OPEN_CALL_FAILED)
        self.assertEqual(signal.severity, "failed")
        self.assertIn("rate limit 503", signal.detail or "")

    def test_open_call_active_without_role_turn_is_stale(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "forum_kickoff_posted": True,
            }
        )
        report = diagnose_session(session)
        self.assertTrue(report.has_signal(ROLE_TURN_MISSING))
        # No synthesis-missing yet because no role spoke at all.
        self.assertFalse(report.has_signal(SYNTHESIS_MISSING))

    def test_role_turn_without_synthesis_is_stale(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "forum_kickoff_posted": True,
                "team_conversation": {
                    "played_roles": ["tech-lead", "ai-engineer"],
                },
            }
        )
        report = diagnose_session(session)
        self.assertFalse(report.has_signal(ROLE_TURN_MISSING))
        self.assertTrue(report.has_signal(SYNTHESIS_MISSING))
        self.assertEqual(
            report.signal(SYNTHESIS_MISSING).severity, "stale"
        )

    def test_synthesis_without_obsidian_proposal_is_stale(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "forum_kickoff_posted": True,
                "team_conversation": {
                    "played_roles": ["tech-lead", "ai-engineer"],
                },
                "research_synthesis": {"summary": "ok"},
            }
        )
        report = diagnose_session(session)
        self.assertFalse(report.has_signal(SYNTHESIS_MISSING))
        self.assertTrue(report.has_signal(OBSIDIAN_PROPOSAL_MISSING))
        signal = report.signal(OBSIDIAN_PROPOSAL_MISSING)
        self.assertIn("yule obsidian sync", signal.propose or "")

    def test_pending_obsidian_approval_is_blocked(self) -> None:
        session = _session(
            write_requested=True,
            write_blocked_reason="작성 승인이 필요합니다",
            extra={
                "research_pack": {"title": "x"},
                "research_synthesis": {"summary": "ok"},
            },
        )
        report = diagnose_session(session)
        self.assertTrue(report.has_signal(OBSIDIAN_PENDING_APPROVAL))
        signal = report.signal(OBSIDIAN_PENDING_APPROVAL)
        self.assertEqual(signal.severity, "blocked")
        self.assertIn("승인", signal.detail or "")

    def test_obsidian_write_failed_is_failed(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_synthesis": {"summary": "ok"},
                "obsidian_write_error": "Permission denied at /vault",
            }
        )
        report = diagnose_session(session)
        self.assertTrue(report.has_signal(OBSIDIAN_WRITE_FAILED))
        # When a write failed we should NOT also nag about a missing
        # proposal — the failure already implies the proposal flow ran.
        self.assertFalse(report.has_signal(OBSIDIAN_PROPOSAL_MISSING))

    def test_research_loop_error_surfaces(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_loop_report": {"error": "forum starter 게시 실패"},
            }
        )
        report = diagnose_session(session)
        self.assertTrue(report.has_signal(RESEARCH_LOOP_ERROR))
        self.assertIn(
            "forum starter", report.signal(RESEARCH_LOOP_ERROR).detail or ""
        )

    def test_closed_session_emits_only_session_closed(self) -> None:
        session = _session(
            state=WorkflowState.COMPLETED,
            summary="done",
            extra={"research_pack": {"title": "x"}},
        )
        report = diagnose_session(session)
        codes = {signal.code for signal in report.signals}
        self.assertEqual(codes, {SESSION_CLOSED})

    def test_no_session_returns_empty_report(self) -> None:
        report = diagnose_session(None)
        self.assertIsNone(report.session_id)
        self.assertEqual(report.signals, ())
        self.assertEqual(report.played_roles, ())

    def test_primary_signal_picks_highest_severity_actionable(self) -> None:
        # Pack missing (info), AND obsidian write failed (failed) → the
        # primary signal must be the failed one.
        session = _session(
            extra={
                "obsidian_write_error": "vault offline",
            }
        )
        report = diagnose_session(session)
        primary = report.primary_signal()
        self.assertIsNotNone(primary)
        self.assertEqual(primary.severity, "failed")
        self.assertEqual(primary.code, OBSIDIAN_WRITE_FAILED)


class RenderDiagnosticSummaryTests(unittest.TestCase):
    def test_no_session_uses_safe_fallback(self) -> None:
        body = render_diagnostic_summary(diagnose_session(None))
        self.assertIn("열린 engineering-agent 세션이 보이지 않아요", body)

    def test_summary_includes_session_header_and_actionable_tag(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "forum_publish_error": "starter 4000자 초과",
            }
        )
        body = render_diagnostic_summary(diagnose_session(session))
        self.assertIn("`abc123def456`", body)
        self.assertIn("[FAILED]", body)
        self.assertIn("starter 4000자 초과", body)

    def test_pure_info_session_skips_actionable_block(self) -> None:
        session = _session()
        body = render_diagnostic_summary(diagnose_session(session))
        # Only info-level signals fire so the supervisor block is omitted.
        self.assertNotIn("감지된 다음 단계:", body)

    def test_no_op_safe_for_minimal_session(self) -> None:
        # No extra dict, no progress, no role sequence — the helper
        # must not raise and must not emit blocked/failed signals.
        session = _session(extra={})
        report = diagnose_session(session)
        for signal in report.signals:
            self.assertIn(signal.severity, ("info", "stale", "blocked", "failed"))
        body = render_diagnostic_summary(report)
        self.assertTrue(body.startswith("현재 engineering-agent 세션 상태"))


class RenderMemberBotSummaryTests(unittest.TestCase):
    def test_member_bot_summary_with_kickoff_failure(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "forum_kickoff_posted": False,
                "forum_kickoff_error": "rate limit 503",
            }
        )
        report = diagnose_session(session)
        body = render_member_bot_summary(report)
        self.assertIn("멤버 봇 진행 상태", body)
        self.assertIn("게시 실패", body)
        self.assertIn("rate limit 503", body)
        self.assertIn("후속 댓글은 운영-리서치 thread", body)

    def test_member_bot_summary_with_no_thread(self) -> None:
        session = _session(extra={"research_pack": {"title": "x"}})
        body = render_member_bot_summary(diagnose_session(session))
        self.assertIn("운영-리서치 forum이 아직 열리지 않아", body)

    def test_member_bot_summary_with_played_roles(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "x"},
                "research_forum_thread_id": 4242,
                "forum_comment_mode": "member-bots",
                "forum_kickoff_posted": True,
                "team_conversation": {
                    "played_roles": ["tech-lead", "ai-engineer"],
                },
            }
        )
        body = render_member_bot_summary(diagnose_session(session))
        self.assertIn("응답한 역할(2)", body)
        self.assertIn("tech-lead", body)


if __name__ == "__main__":
    unittest.main()
