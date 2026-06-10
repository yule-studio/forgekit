"""Discord 라우팅 — Coding authorization gate tests.

The router must:
  - intercept "코딩 권한 제안" so the gateway never re-classifies it
    as a fresh task,
  - intercept "수정 승인" / "이대로 구현 진행" / "구현 시작" only when
    a coding proposal is already pending,
  - persist proposal → coding_job to ``session.extra``,
  - leave the existing Obsidian / runtime preflight gates untouched.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from unittest.mock import AsyncMock

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from tests._helpers import (
    FakeChannel,
    FakeMessage,
    extract_prompt as _extract_prompt,
    isolate_cache_for_test as _isolate_cache_for_test,
    run as _run,
)

from yule_engineering.agents.coding.authorization import reset_role_profile_cache
from yule_discord.engineering_channel_router import (
    EngineeringRouteContext,
    is_coding_approval_phrase,
    is_coding_proposal_request,
    route_engineering_message,
)


@dataclass
class _RouteFakeSession:
    """Mutable session stand-in so the router's
    ``_persist_extra_keys`` plain-object fallback can flip
    ``extra['coding_proposal']``/``coding_job`` in place during tests."""

    session_id: str
    prompt: str = ""
    task_type: str = "research"
    state: str = "in_progress"
    summary: Optional[str] = None
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    user_id: Optional[int] = None
    updated_at: Optional[datetime] = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    executor_role: Optional[str] = "tech-lead"
    executor_runner: Optional[str] = "claude-code"


def _now(offset_minutes: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)


def _channel(channel_id: int = 111, name: str = "업무-접수") -> FakeChannel:
    return FakeChannel(channel_id=channel_id, name=name)


class _CodingGateHarness(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)
        reset_role_profile_cache()
        self.context = EngineeringRouteContext(
            intake_channel_id=111, intake_channel_name="업무-접수"
        )
        self.send_chunks = AsyncMock()
        # All non-coding hooks must NOT run when the coding gate handles
        # the message. Wire them as raising mocks so a regression fires
        # loudly.
        self.conversation_fn = AsyncMock(
            side_effect=AssertionError(
                "conversation_fn must NOT run when coding gate handles the message"
            )
        )
        self.intake_fn = AsyncMock(
            side_effect=AssertionError(
                "intake must NOT run when coding gate handles the message"
            )
        )
        self.kickoff_fn = AsyncMock(
            side_effect=AssertionError(
                "kickoff must NOT run when coding gate handles the message"
            )
        )

    def _route(self, *, message, list_sessions_fn):
        return _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=self.conversation_fn,
                intake_fn=self.intake_fn,
                thread_kickoff_fn=self.kickoff_fn,
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=None,
                list_sessions_fn=list_sessions_fn,
            )
        )


class CodingPhraseDetectorTests(unittest.TestCase):
    def test_proposal_request_phrases(self) -> None:
        for text in (
            "코딩 권한 제안",
            "이 작업 수정 권한 제안 좀",
            "구현 권한 제안해줘",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_coding_proposal_request(text))

    def test_proposal_request_phrase_does_not_overlap_with_approval(self) -> None:
        # "수정 승인" must NOT match the proposal-request detector.
        self.assertFalse(is_coding_proposal_request("수정 승인"))

    def test_approval_phrases(self) -> None:
        for text in (
            "수정 승인",
            "코딩 진행 승인",
            "이대로 구현 진행",
            "구현 시작",
            "권한 승인",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_coding_approval_phrase(text))

    def test_approval_phrase_does_not_match_random_progress(self) -> None:
        # Bare "진행" is the existing CONFIRM_INTAKE phrase; the coding
        # gate must require the explicit "수정"/"코딩"/"구현" prefix so
        # routine intake confirmations don't get hijacked.
        self.assertFalse(is_coding_approval_phrase("진행"))
        self.assertFalse(is_coding_approval_phrase("이대로 진행"))


class ProposalRequestGateTests(_CodingGateHarness):
    def test_proposal_request_creates_proposal_and_persists_to_session_extra(self) -> None:
        target = _RouteFakeSession(
            session_id="sess-frontend",
            prompt="React hero 컴포넌트 정리 + UI CSS 다듬기",
            channel_id=111,
            updated_at=_now(-10),
        )
        message = FakeMessage(content="코딩 권한 제안 좀", channel=_channel())

        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: [target],
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "sess-frontend")
        # Proposal stashed under coding_proposal.
        self.assertIn("coding_proposal", target.extra)
        proposal = target.extra["coding_proposal"]
        self.assertEqual(proposal["executor_role"], "frontend-engineer")
        self.assertGreaterEqual(len(proposal["write_scope"]), 1)
        self.assertGreaterEqual(len(proposal["forbidden_scope"]), 1)
        # Discord preview was sent.
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("코딩 권한 제안", sent)
        self.assertIn("frontend-engineer", sent)
        # The new-work / intake hooks must not have run.
        self.conversation_fn.assert_not_awaited()
        self.intake_fn.assert_not_awaited()

    def test_proposal_request_without_open_session_clarifies(self) -> None:
        message = FakeMessage(content="코딩 권한 제안", channel=_channel())
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: [],
        )
        self.assertTrue(result.handled)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("열린 engineering-agent 세션이 보이지 않아요", sent)
        self.conversation_fn.assert_not_awaited()
        self.intake_fn.assert_not_awaited()


class ApprovalPhraseGateTests(_CodingGateHarness):
    def _seed_pending_proposal(self) -> _RouteFakeSession:
        # Manually build the persisted proposal payload — same shape
        # the proposal-request gate writes after running the
        # recommender. Placed on a plain dict so the router's plain-
        # object fallback persistence path can mutate it.
        proposal_payload = {
            "session_id": "sess-backend",
            "user_request": "Spring Security API 인증 흐름 추가",
            "executor_role": "backend-engineer",
            "review_roles": ["tech-lead", "qa-engineer"],
            "participant_roles": ["backend-engineer", "tech-lead"],
            "write_scope": ["src/<service>/api/**"],
            "forbidden_scope": [
                "frontend 컴포넌트 임의 변경",
                "secret / .env / 운영 자격 증명 접근",
            ],
            "reason": "Spring Security 키워드 매칭",
            "safety_rules": [
                "사용자 승인 phrase가 도착하기 전 어떤 production write도 시작하지 않는다",
                "git reset --hard / git push --force / 자동 deploy 같은 destructive 명령을 실행하지 않는다",
            ],
            "approval_required": True,
            "metadata": {},
        }
        return _RouteFakeSession(
            session_id="sess-backend",
            prompt="Spring Security API 인증 흐름 추가",
            channel_id=111,
            updated_at=_now(-5),
            extra={"coding_proposal": proposal_payload},
        )

    def test_approval_with_pending_proposal_persists_coding_job(self) -> None:
        target = self._seed_pending_proposal()
        message = FakeMessage(content="수정 승인", channel=_channel())

        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: [target],
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.session_id, "sess-backend")
        # coding_job replaces coding_proposal.
        self.assertIsNone(target.extra.get("coding_proposal"))
        job = target.extra.get("coding_job")
        self.assertIsNotNone(job)
        self.assertEqual(job["executor_role"], "backend-engineer")
        self.assertEqual(job["status"], "ready")
        self.assertIn("approved_at", job)
        self.assertIsNotNone(job["approved_at"])
        # generated_prompt embeds safety rules and write scope.
        prompt = job["generated_prompt"]
        self.assertIn("write scope", prompt)
        self.assertIn("forbidden scope", prompt)
        self.assertIn("backend-engineer", prompt)
        # Discord ack message confirms approval.
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("코딩 권한 승인 완료", sent)
        # Existing flow hooks must not run.
        self.conversation_fn.assert_not_awaited()
        self.intake_fn.assert_not_awaited()

    def test_approval_without_pending_proposal_clarifies(self) -> None:
        message = FakeMessage(content="수정 승인", channel=_channel())
        result = self._route(
            message=message,
            list_sessions_fn=lambda **_kw: [],
        )
        self.assertTrue(result.handled)
        sent = "\n".join(str(c.args[1]) for c in self.send_chunks.await_args_list)
        self.assertIn("대기 중인 코딩 권한 제안이 없어요", sent)
        self.conversation_fn.assert_not_awaited()
        self.intake_fn.assert_not_awaited()


class NoBackpressureOnUnrelatedFlowsTests(unittest.TestCase):
    """The new gate runs before runtime preflight; ensure unrelated
    messages still reach the legacy conversation flow."""

    def setUp(self) -> None:  # noqa: D401
        _isolate_cache_for_test(self)
        reset_role_profile_cache()
        self.context = EngineeringRouteContext(
            intake_channel_id=111, intake_channel_name="업무-접수"
        )
        self.send_chunks = AsyncMock()

    def test_normal_new_work_message_falls_through_to_conversation(self) -> None:
        called = {"conversation": 0}

        def conversation_fn(**_):
            from yule_discord.engineering_channel_router import (
                EngineeringConversationOutcome,
            )

            called["conversation"] += 1
            return EngineeringConversationOutcome(content="hi")

        message = FakeMessage(
            content="결제 모듈 멱등성 검증 흐름 백엔드에 추가해줘",
            channel=_channel(),
        )
        result = _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=conversation_fn,
                intake_fn=AsyncMock(),
                thread_kickoff_fn=AsyncMock(),
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=None,
                list_sessions_fn=lambda **_kw: [],
            )
        )
        self.assertTrue(result.handled)
        # conversation_fn ran exactly once because the coding gate
        # didn't claim the message.
        self.assertEqual(called["conversation"], 1)


class NoCodeChangeOverrideTests(_CodingGateHarness):
    """Live MVP regression: when the user explicitly says
    "코드 수정하지 말고 리서치만" the coding gate must NOT act on the
    message even if it carries an approval phrase like "수정 승인" in
    the same sentence. The runtime preflight / conversation_fn handles
    it instead — that's where research collection and status response
    live."""

    def _seed_pending_proposal(self) -> _RouteFakeSession:
        proposal_payload = {
            "session_id": "sess-research-only",
            "user_request": "결제 모듈 멱등성 검증 흐름 백엔드에 추가해줘",
            "executor_role": "backend-engineer",
            "review_roles": ["tech-lead"],
            "participant_roles": ["backend-engineer"],
            "write_scope": ["src/<service>/api/**"],
            "forbidden_scope": ["frontend 컴포넌트 임의 변경"],
            "reason": "결제/멱등성 키워드 매칭",
            "safety_rules": ["승인 전 production write 금지"],
            "approval_required": True,
            "metadata": {},
        }
        return _RouteFakeSession(
            session_id="sess-research-only",
            prompt="결제 모듈 멱등성 검증 흐름 백엔드에 추가해줘",
            channel_id=111,
            updated_at=_now(-5),
            extra={"coding_proposal": proposal_payload},
        )

    def test_no_code_change_phrase_skips_coding_gate(self) -> None:
        # Pending proposal exists; user replies asking research only.
        target = self._seed_pending_proposal()
        message = FakeMessage(
            content="코드 수정하지 말고 리서치만 정리해줘",
            channel=_channel(),
        )

        # The coding gate must return None — but the rest of the route
        # (conversation_fn) WILL run, so we replace the AssertionError
        # mocks with one that's allowed to be called once.
        from yule_discord.engineering_channel_router import (
            EngineeringConversationOutcome,
        )

        called = {"conversation": 0}

        def conversation_fn(**_):
            called["conversation"] += 1
            return EngineeringConversationOutcome(content="ack")

        result = _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=conversation_fn,
                intake_fn=AsyncMock(),
                thread_kickoff_fn=AsyncMock(),
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=None,
                list_sessions_fn=lambda **_kw: [target],
            )
        )
        self.assertTrue(result.handled)
        # The coding_proposal stays untouched — no coding_job created.
        self.assertIsNone(target.extra.get("coding_job"))
        self.assertIn("coding_proposal", target.extra)
        # conversation_fn was called because the gate stepped aside.
        self.assertEqual(called["conversation"], 1)

    def test_no_code_change_phrase_skips_proposal_creation(self) -> None:
        # Even when the user includes "코딩 권한 제안" alongside the
        # research-only directive, the gate must not auto-build a fresh
        # proposal — the user explicitly asked NOT to touch code.
        target = _RouteFakeSession(
            session_id="sess-no-proposal",
            prompt="결제 모듈 멱등성 검증 흐름",
            channel_id=111,
            updated_at=_now(-5),
        )
        message = FakeMessage(
            content="코딩 권한 제안 — 다만 코드 수정하지 말고 리서치만 먼저 정리해줘",
            channel=_channel(),
        )

        from yule_discord.engineering_channel_router import (
            EngineeringConversationOutcome,
        )

        def conversation_fn(**_):
            return EngineeringConversationOutcome(content="ack")

        result = _run(
            route_engineering_message(
                message=message,
                bot_user=object(),
                route_context=self.context,
                extract_prompt=_extract_prompt,
                conversation_fn=conversation_fn,
                intake_fn=AsyncMock(),
                thread_kickoff_fn=AsyncMock(),
                send_chunks=self.send_chunks,
                research_loop_fn=None,
                thread_continuation_fn=None,
                list_sessions_fn=lambda **_kw: [target],
            )
        )
        self.assertTrue(result.handled)
        # No proposal was built because the gate skipped.
        self.assertNotIn("coding_proposal", target.extra)
        self.assertNotIn("coding_job", target.extra)


if __name__ == "__main__":
    unittest.main()
