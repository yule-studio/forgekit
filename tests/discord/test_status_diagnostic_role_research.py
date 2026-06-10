"""Phase 5 stabilisation — status diagnostic surfaces Phase 4 keys.

Pin the live-bug regression: when the user asks "누가 어디까지 했어?"
the diagnostic must answer from ``session.extra`` rather than re-running
research. Phase 4 records two new keys:

  • ``role_research_results[<role>]`` — per-role outcome (provider,
    source_count, status, top_findings).
  • ``role_activity_log`` — a flat audit trail of structured events
    (research_started / research_completed / research_failed / …).

Phase 5 surfaces both inside :func:`format_status_diagnostic_response`
so the operator can see "ai-engineer: ok (provider: tavily, 4건)" and
"활동 로그: research_started=2, research_completed=2" without scanning
the forum thread.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.workflow_state import WorkflowSession, WorkflowState
from yule_engineering.discord.engineering_conversation import (
    format_status_diagnostic_response,
)


def _session(**overrides: Any) -> WorkflowSession:
    base = dict(
        session_id="abc123def456",
        prompt="k8s 운영 자료 수집",
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


class RoleResearchResultsBlockTests(unittest.TestCase):
    """The "역할 연구 결과" block lists every role that ran a per-role
    collection pass with provider + source count + first finding."""

    def test_renders_provider_and_source_count_per_role(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "stub"},
                "role_research_results": {
                    "ai-engineer": {
                        "status": "ok",
                        "provider": "tavily",
                        "source_count": 4,
                        "top_findings": ["RAG memory 운영 가이드"],
                    },
                    "devops-engineer": {
                        "status": "ok",
                        "provider": "brave",
                        "source_count": 6,
                        "top_findings": ["Ingress NGINX rate-limit"],
                    },
                },
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("역할 연구 결과", body)
        # Both roles + their provider + source count appear.
        self.assertIn("ai-engineer: ok", body)
        self.assertIn("provider: tavily, 4건", body)
        self.assertIn("devops-engineer: ok", body)
        self.assertIn("provider: brave, 6건", body)
        # Top finding preview must surface so the user sees concrete
        # evidence — not just numbers.
        self.assertIn("RAG memory 운영 가이드", body)
        self.assertIn("Ingress NGINX rate-limit", body)

    def test_failed_role_records_error(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "stub"},
                "role_research_results": {
                    "qa-engineer": {
                        "status": "failed",
                        "provider": None,
                        "source_count": 0,
                        "error": "tavily timeout",
                    }
                },
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("qa-engineer: failed", body)
        self.assertIn("tavily timeout", body)

    def test_empty_role_records_zero_sources(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "stub"},
                "role_research_results": {
                    "frontend-engineer": {
                        "status": "empty",
                        "provider": None,
                        "source_count": 0,
                    }
                },
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("frontend-engineer: empty", body)
        self.assertIn("0건", body)

    def test_legacy_session_without_block_does_not_render_section(self) -> None:
        # Legacy session (Phase 4 not run yet) — the role-research
        # block must not appear so we don't print an empty header.
        session = _session(extra={"research_pack": {"title": "stub"}})
        body = format_status_diagnostic_response(session)
        self.assertNotIn("역할 연구 결과", body)


class RoleActivityLogBlockTests(unittest.TestCase):
    """The "활동 로그" line summarises Phase 4's audit trail — counts
    by event_type, last event, last failure — so the operator sees
    "왜 멈췄지" without scanning the full log.
    """

    def test_counts_render_sorted_by_event_type(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "stub"},
                "role_activity_log": [
                    {
                        "timestamp": "2026-05-06T10:00:00+09:00",
                        "role": "ai-engineer",
                        "event_type": "research_started",
                        "status": "ok",
                    },
                    {
                        "timestamp": "2026-05-06T10:01:00+09:00",
                        "role": "ai-engineer",
                        "event_type": "research_completed",
                        "status": "ok",
                    },
                    {
                        "timestamp": "2026-05-06T10:02:00+09:00",
                        "role": "qa-engineer",
                        "event_type": "research_started",
                        "status": "ok",
                    },
                ],
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("활동 로그", body)
        # Counts surface aggregated by event_type — sorted for stable
        # diagnostic output.
        self.assertIn("research_completed=1", body)
        self.assertIn("research_started=2", body)

    def test_last_event_is_surfaced_with_role_and_timestamp(self) -> None:
        session = _session(
            extra={
                "research_pack": {"title": "stub"},
                "role_activity_log": [
                    {
                        "timestamp": "2026-05-06T10:00:00+09:00",
                        "role": "ai-engineer",
                        "event_type": "research_started",
                        "status": "ok",
                    },
                    {
                        "timestamp": "2026-05-06T10:05:00+09:00",
                        "role": "qa-engineer",
                        "event_type": "research_completed",
                        "status": "ok",
                    },
                ],
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("마지막 이벤트", body)
        self.assertIn("qa-engineer", body)
        self.assertIn("2026-05-06T10:05:00+09:00", body)
        self.assertIn("research_completed", body)

    def test_last_failure_surfaces_with_error(self) -> None:
        # When the last event is OK but an earlier one failed, the
        # diagnostic must still call out the failure so it's not lost
        # in the noise.
        session = _session(
            extra={
                "research_pack": {"title": "stub"},
                "role_activity_log": [
                    {
                        "timestamp": "2026-05-06T10:00:00+09:00",
                        "role": "ai-engineer",
                        "event_type": "research_failed",
                        "status": "failed",
                        "error": "tavily timeout",
                    },
                    {
                        "timestamp": "2026-05-06T10:05:00+09:00",
                        "role": "qa-engineer",
                        "event_type": "research_completed",
                        "status": "ok",
                    },
                ],
            }
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("마지막 실패", body)
        self.assertIn("ai-engineer", body)
        self.assertIn("tavily timeout", body)

    def test_legacy_session_without_log_does_not_render_section(self) -> None:
        session = _session(extra={"research_pack": {"title": "stub"}})
        body = format_status_diagnostic_response(session)
        self.assertNotIn("활동 로그", body)


if __name__ == "__main__":
    unittest.main()
