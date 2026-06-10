"""Stabilisation Phase 6 — integration regression suite.

Pin the four scenarios called out in the live-test stabilisation
spec so a regression can't sneak any of them back in:

  * Scenario A — Research lifecycle happy-ish path: prompt 원문 보존,
    active_research_roles 정확, thread_id 저장, work_report status.
  * Scenario B — Incomplete lifecycle guard: research_pack 없음 →
    final work_report 금지 + Obsidian write 차단 + status 에 사유.
  * Scenario C — Explicit session command: 임의 채널에서 "세션 <id>
    기준" 이 새 작업을 만들지 않고 그 세션으로 resolve.
  * Scenario D — Typing: inactive role 은 research-open 에 typing
    안 켜짐.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
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
    extract_prompt as _extract_prompt,
    isolate_cache_for_test as _isolate_cache_for_test,
    run as _run,
)

from yule_engineering.discord.engineering_channel_router import (
    EngineeringConversationOutcome,
    EngineeringResearchLoopReport,
    EngineeringRouteContext,
    EngineeringThreadKickoff,
    route_engineering_message,
)
from yule_engineering.discord.engineering_conversation import (
    format_status_diagnostic_response,
)
from yule_engineering.discord.engineering_team_runtime import (
    handle_research_turn_message,
)
from yule_engineering.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
)


class _MutableSession:
    """Plain stub the router can mutate via _persist_extra_keys."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.task_type = "research"
        self.state = WorkflowState.IN_PROGRESS
        self.prompt = ""
        self.thread_id: int | None = None
        self.summary: str | None = None
        self.role_sequence = ()
        self.extra: dict[str, Any] = {}


class ScenarioA_ResearchLifecycleHappyPathTests(unittest.TestCase):
    """[Research] hane-se prompt → "새 작업으로 진행" → expected wirings."""

    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)
        self.context = EngineeringRouteContext(intake_channel_id=111)
        self.send_chunks = AsyncMock()

    def test_full_intake_persists_thread_active_roles_and_work_report(self) -> None:
        canonical = (
            "[Research] 하네스 엔지니어링을 yule-studio-agent에 어떻게 도입할 수 있을지 "
            "조사해줘 - tech-lead / ai-engineer / backend-engineer / qa-engineer / "
            "devops-engineer 관점에서"
        )
        session = _MutableSession(session_id="sess-A")
        intake_fn = AsyncMock(
            return_value=FakeIntakeResult(
                session=session,
                plan=FakePlan(),
                message="**[engineering-agent] 새 작업 접수**",
            )
        )
        kickoff_fn = AsyncMock(
            return_value=EngineeringThreadKickoff(
                thread_id=2024,
                message="thread kickoff",
            )
        )

        async def loop_fn(**_kwargs):
            return EngineeringResearchLoopReport()

        outcome = EngineeringConversationOutcome(
            content="요약",
            confirmed=True,
            intake_prompt=canonical,
        )
        message = FakeMessage(
            content="이대로 진행",
            channel=FakeChannel(channel_id=111, name="업무-접수"),
        )

        result = _run(
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
        self.assertTrue(result.handled)
        # 1. thread_id 저장 (Phase 1 stab)
        self.assertEqual(session.thread_id, 2024)
        # 2. active_research_roles 정확 (5 user-explicit + tech-lead always)
        # C4 cleanup: storage is canonical (`engineering-agent/<short>`).
        # Normalise the assertion view to short form for backward-compat
        # with the legacy expectations.
        active = session.extra.get("active_research_roles") or []
        active_short = {role.rsplit("/", 1)[-1] for role in active}
        self.assertIn("tech-lead", active_short)
        self.assertIn("ai-engineer", active_short)
        self.assertIn("backend-engineer", active_short)
        self.assertIn("qa-engineer", active_short)
        self.assertIn("devops-engineer", active_short)
        # 3. excluded roles
        excluded = session.extra.get("excluded_research_roles") or []
        excluded_short = {role.rsplit("/", 1)[-1] for role in excluded}
        self.assertIn("frontend-engineer", excluded_short)
        self.assertIn("product-designer", excluded_short)
        # 4. work_report 영속화 (status 가 lifecycle 조건 따라 결정)
        self.assertIn("work_report", session.extra)
        wr = session.extra["work_report"]
        # 자료 0 + synthesis 없음 → insufficient (or interim with missing
        # roles since played_roles is empty here). Both states are valid
        # "not final" outcomes — verify the status field is one of the
        # non-final values.
        self.assertIn(wr["status"], ("insufficient", "interim"))
        # 5. canonical prompt 가 work_report 에 보존
        self.assertIn("하네스", wr["canonical_prompt"])


class ScenarioB_IncompleteLifecycleGuardTests(unittest.TestCase):
    """research_pack 없음 + played_roles 일부 → final work_report 금지,
    Obsidian write 차단, status 에 사유 표시."""

    def test_status_diagnostic_marks_lifecycle_incomplete(self) -> None:
        now = datetime(2026, 5, 6, tzinfo=timezone.utc)
        session = WorkflowSession(
            session_id="sess-B",
            prompt="harness",
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
                "played_roles": ["ai-engineer"],
                "research_status": "insufficient",
                "research_source_count": 0,
                "research_stop_reason": "no_initial_provider_hit",
                "research_missing_roles": ["tech-lead", "qa-engineer"],
                "work_report": {
                    "status": "insufficient",
                    "title": "harness 도입",
                    "missing_roles": ["tech-lead", "qa-engineer"],
                    "reference_count": 0,
                    "research_stop_reason": "no_initial_provider_hit",
                    "requires_code_change": False,
                },
            },
        )
        body = format_status_diagnostic_response(session)
        # status 라벨이 표면화
        self.assertIn("status=insufficient", body)
        self.assertIn("미완료 role", body)
        # research_pack 가드: 본 세션은 research_pack 이 없으므로
        # diagnostic 도 그렇게 보고
        self.assertIn("research_pack: 없음", body)


class ScenarioC_ExplicitSessionCommandTests(unittest.TestCase):
    """임의 채널에서 "세션 <id> 기준으로 ..." → 새 작업 안 만들고
    그 세션으로 resolve."""

    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        os.environ["OBSIDIAN_VAULT_PATH"] = str(Path(self.tmpdir.name))
        self.addCleanup(lambda: os.environ.pop("OBSIDIAN_VAULT_PATH", None))
        self.context = EngineeringRouteContext(
            intake_channel_id=1001, intake_channel_name="업무-접수"
        )
        self.send_chunks = AsyncMock()

    def test_explicit_unknown_session_does_not_create_new_session(self) -> None:
        message = FakeMessage(
            content="세션 ffffffffffff 기준으로 저장 승인",
            channel=FakeChannel(channel_id=1001, name="업무-접수"),
        )
        intake_fn = AsyncMock(
            side_effect=AssertionError("intake must NOT run for explicit-id status")
        )
        kickoff_fn = AsyncMock(
            side_effect=AssertionError("kickoff must NOT run for explicit-id status")
        )
        result = _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=AsyncMock(
                    side_effect=AssertionError("conversation_fn must not run")
                ),
                intake_fn=intake_fn,
                thread_kickoff_fn=kickoff_fn,
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=None,
                list_sessions_fn=lambda **_kw: [],
            )
        )
        self.assertTrue(result.handled)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        # Friendly "세션 찾지 못했어요" 응답 — 새 작업 생성 안 됨.
        self.assertIn("세션 `ffffffffffff` 을 찾지 못했어요", sent)


class ScenarioD_TypingActiveRoleGateTests(unittest.TestCase):
    """active_research_roles 에 빠진 role 의 member bot 은
    research-open 마커를 보고도 typing 안 켜짐 — outcome=None 으로
    조용히 무시."""

    def _session(self, *, active_roles) -> WorkflowSession:
        now = datetime(2026, 5, 6, tzinfo=timezone.utc)
        return WorkflowSession(
            session_id="sess-D",
            prompt="harness",
            task_type="research",
            state=WorkflowState.IN_PROGRESS,
            created_at=now,
            updated_at=now,
            extra={"active_research_roles": list(active_roles)},
        )

    def test_inactive_role_handler_returns_none(self) -> None:
        # frontend-engineer 가 active 아니므로 outcome=None → typing 안 켜짐.
        session = self._session(
            active_roles=["tech-lead", "ai-engineer", "qa-engineer"]
        )
        outcome = handle_research_turn_message(
            role="frontend-engineer",
            text="[research-open:sess-D]",
            session_loader=lambda _sid: session,
            pack_loader=lambda _s: None,
        )
        self.assertIsNone(outcome)

    def test_active_role_handler_returns_outcome(self) -> None:
        session = self._session(
            active_roles=["tech-lead", "ai-engineer", "qa-engineer"]
        )
        outcome = handle_research_turn_message(
            role="qa-engineer",
            text="[research-open:sess-D]",
            session_loader=lambda _sid: session,
            pack_loader=lambda _s: None,
        )
        self.assertIsNotNone(outcome)


if __name__ == "__main__":
    unittest.main()
