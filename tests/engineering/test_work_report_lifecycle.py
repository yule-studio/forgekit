"""Phase 4 — gateway lifecycle wiring for role_selection + work_report.

Pin two contracts the live MVP depends on:

  1. ``route_engineering_message`` 의 CREATE 분기는 intake 직후
     ``recommend_active_roles`` 를 호출해 ``session.extra`` 에
     ``active_research_roles`` 와 ``role_selection_*`` 를 박는다.
  2. research_loop 가 끝나면 ``_emit_work_report_preview`` 가 트리거되어
     deterministic ``WorkReport`` 가 ``session.extra['work_report']``
     로 영속화되고 같은 본문이 Discord ``send_chunks`` 로 흘러간다.

Status diagnostic 도 같은 키들을 읽어 사용자에게 ``활성 role`` /
``업무 보고서`` 라인을 보여주는지 따로 검증한다.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests._helpers import (
    FakeChannel,
    FakeIntakeResult,
    FakeMessage,
    FakePlan,
    FakeSession,
    extract_prompt as _extract_prompt,
    isolate_cache_for_test as _isolate_cache_for_test,
    run as _run,
)

from yule_orchestrator.discord.engineering_channel_router import (
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteContext,
    EngineeringThreadKickoff,
    route_engineering_message,
)
from yule_orchestrator.discord.engineering_conversation import (
    format_status_diagnostic_response,
)
from yule_orchestrator.agents.workflow_state import WorkflowSession, WorkflowState


class _MutableSession:
    """Plain stub the router can mutate via ``_persist_extra_keys`` —
    production sessions are frozen but this fixture mimics the
    in-place fast path so we can read back the persisted
    ``work_report`` / ``active_research_roles`` after the route
    returns."""

    def __init__(self, session_id: str, task_type: str = "research") -> None:
        self.session_id = session_id
        self.task_type = task_type
        self.state = WorkflowState.IN_PROGRESS
        self.prompt = ""
        self.thread_id: int | None = None
        self.summary: str | None = None
        self.role_sequence = ()
        self.extra: dict[str, Any] = {}


class GatewayWorkReportLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)
        self.context = EngineeringRouteContext(intake_channel_id=111)
        self.send_chunks = AsyncMock()

    def _route_create_flow(
        self,
        *,
        canonical_prompt: str,
        message_content: str = "이대로 진행",
    ) -> _MutableSession:
        session = _MutableSession(session_id="sess-phase4")
        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
                session=session,
                plan=FakePlan(),
                message="**[engineering-agent] 새 작업 접수**",
            )
        )
        kickoff_fn = AsyncMock(
            return_value=EngineeringThreadKickoff(
                thread_id=4242,
                message="thread kickoff",
            )
        )

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport(
                follow_up_message="loop ran",
            )

        outcome = EngineeringConversationOutcome(
            content="요약",
            confirmed=True,
            intake_prompt=canonical_prompt,
            thread_topic="engineer-feature-abc",
        )

        message = FakeMessage(
            content=message_content,
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )

        _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=lambda **_: outcome,
                intake_fn=intake_fn,
                thread_kickoff_fn=kickoff_fn,
                send_chunks=self.send_chunks,
                research_loop_fn=loop_fn,
                thread_continuation_fn=None,
            )
        )
        return session

    def test_intake_persists_active_research_roles(self) -> None:
        session = self._route_create_flow(
            canonical_prompt=(
                "[Research] backend-engineer / qa-engineer 관점에서 결제 멱등성 검토"
            )
        )
        self.assertIn("active_research_roles", session.extra)
        active = session.extra["active_research_roles"]
        self.assertIn("tech-lead", active)
        self.assertIn("backend-engineer", active)
        self.assertIn("qa-engineer", active)
        # Source captured for status diagnostic readability.
        self.assertEqual(
            session.extra.get("role_selection_source"), "user_explicit"
        )

    def test_research_loop_close_persists_work_report(self) -> None:
        session = self._route_create_flow(
            canonical_prompt=(
                "[Research] 하네스 엔지니어링 도입 검토 — qa 회귀 + 운영 모니터링"
            )
        )
        self.assertIn("work_report", session.extra)
        report = session.extra["work_report"]
        # Title slug must NOT be a routing-command phrase or [Research] tag.
        self.assertIn("하네스", report["title"])
        self.assertNotIn("[Research]", report["title"])
        # Reference count + participants are surfaced.
        self.assertIn("participants", report)
        self.assertGreaterEqual(len(report["participants"]), 1)

    def test_work_report_preview_sent_to_discord(self) -> None:
        self._route_create_flow(
            canonical_prompt="[Research] 결제 멱등성 백엔드 추가",
        )
        sent = "\n".join(
            str(call.args[1]) for call in self.send_chunks.await_args_list
        )
        # The work-report preview lands as a Discord chunk after the
        # research loop closes. Look for the canonical header line.
        self.assertIn("업무 보고서", sent)
        # Original task body is quoted so the user sees what they asked.
        self.assertIn("결제 멱등성", sent)


class StatusDiagnosticSurfacesWorkReportTests(unittest.TestCase):
    def _session_with_work_report(self) -> WorkflowSession:
        now = datetime(2026, 5, 6)
        return WorkflowSession(
            session_id="abc12345",
            prompt="[Research] 하네스 엔지니어링 자동화 검토",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=now,
            updated_at=now,
            extra={
                "active_research_roles": [
                    "tech-lead",
                    "ai-engineer",
                    "qa-engineer",
                ],
                "role_selection_source": "tech_lead_rule",
                "research_synthesis": {"consensus": "RAG 도입"},
                "work_report": {
                    "title": "하네스 엔지니어링 도입 검토",
                    "requires_code_change": False,
                    "reference_count": 7,
                    "research_stop_reason": "sufficient",
                },
            },
        )

    def test_status_lists_active_roles_and_work_report(self) -> None:
        session = self._session_with_work_report()
        body = format_status_diagnostic_response(session)
        self.assertIn(
            "활성 role: tech-lead, ai-engineer, qa-engineer",
            body,
        )
        self.assertIn("선정: tech_lead_rule", body)
        self.assertIn(
            "업무 보고서: 작성됨 — \"하네스 엔지니어링 도입 검토\"",
            body,
        )
        self.assertIn("자료 7건", body)
        self.assertIn("코드 수정 없음", body)
        self.assertIn("stop: sufficient", body)

    def test_status_marks_work_report_missing_when_synthesis_only(self) -> None:
        now = datetime(2026, 5, 6)
        session = WorkflowSession(
            session_id="abc",
            prompt="결제 멱등성",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=now,
            updated_at=now,
            extra={
                "research_synthesis": {"consensus": "ok"},
            },
        )
        body = format_status_diagnostic_response(session)
        self.assertIn("업무 보고서: 아직 미작성", body)


if __name__ == "__main__":
    unittest.main()
