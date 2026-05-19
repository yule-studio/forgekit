"""P1-Z3 — actual ``build_github_work_order_proposal`` 가 approved
continuation 경로에서 prompt phrase 게이트를 우회하는지 회귀.

배경
----
P1-Z2 는 ``decide_post_approval_action`` 가 handoff_packet 경로를
허용하도록 확장했지만, ``dispatch_post_approval_work_order`` 가 실제
``build_github_work_order_proposal`` 을 호출할 때 그 builder 가 다시
``should_route_to_github_workos(...)`` → ``detect_coding_intent(request_text)``
를 강제했다.

실측: lifecycle_mode=implementation + handoff packet + github_target 이
이미 있는 approved session 이라도, ``session.prompt`` 가 GitHub URL +
"실제 구현 가능한 상태까지 구현" 같은 일반 자연어면
``detect_coding_intent`` 가 ``coding_required=False`` → builder 가
``None`` 반환 → dead-end.

P1-Z3 fix
---------
``should_route_to_github_workos`` 와 ``build_github_work_order_proposal``
에 ``approved_continuation`` 플래그 추가.  True 면 prompt phrase 기반
coding-intent 재판정을 우회 — structured signals (lifecycle / packet /
target) 이 source of truth.  단 ``lifecycle == research_only`` 와
obsidian intent 는 여전히 reject (안전 우선).

본 회귀는 **fake_builder 없이** 실제 builder 를 사용해 게이트가 진짜로
닫혔는지 확정한다.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.post_approval_dispatch import (
    ACTION_DISPATCHED,
    ACTION_FAILED,
    ACTION_NOOP,
    NOOP_REASON_RESEARCH_ONLY_LIFECYCLE,
    dispatch_post_approval_work_order,
)
from yule_orchestrator.discord.integrations.github_workos_adapter import (
    SKIPPED_NO_CODING_INTENT,
    SKIPPED_OBSIDIAN_INTENT,
    SKIPPED_RESEARCH_ONLY,
    build_github_work_order_proposal,
    should_route_to_github_workos,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _State:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:  # noqa: D401
        return self.value


@dataclass
class _FakeSession:
    session_id: str = "f2f36607d175"
    state: Any = field(default_factory=lambda: _State("approved"))
    extra: Mapping[str, Any] = field(default_factory=dict)
    prompt: str = ""
    channel_id: Optional[int] = 9100
    thread_id: Optional[int] = 9200

    @classmethod
    def make(
        cls,
        *,
        session_id: str = "f2f36607d175",
        state: str = "approved",
        extra: Optional[Mapping[str, Any]] = None,
        prompt: str = "",
        channel_id: Optional[int] = 9100,
        thread_id: Optional[int] = 9200,
    ) -> "_FakeSession":
        return cls(
            session_id=session_id,
            state=_State(state),
            extra=dict(extra or {}),
            prompt=prompt,
            channel_id=channel_id,
            thread_id=thread_id,
        )


_TARGET_REPO = {
    "kind": "repo",
    "owner": "yule-studio",
    "repo": "naver-search-clone",
    "number": None,
}


def _handoff_extra(
    *,
    lifecycle_mode: Optional[str] = "implementation",
) -> dict:
    extra: dict[str, Any] = {
        "github_target": dict(_TARGET_REPO),
        "coding_handoff_packet": {
            "canonical_request": "검색 풀스택 MVP 구현",
            "github_target": dict(_TARGET_REPO),
            "mode": "approval_required",
            "topology": "single_repo",
            "scope_mode": "full_stack_single_repo",
            "tracking_mode": "repo_root",
            "next_action": "open_issue",
            "notes": {},
        },
    }
    if lifecycle_mode is not None:
        extra["lifecycle_mode"] = lifecycle_mode
    return extra


def _in_memory_queue():
    tmpdir = tempfile.mkdtemp(prefix="yule-p1z3-")
    from yule_orchestrator.agents.job_queue.store import JobQueue

    return JobQueue(db_path=Path(tmpdir) / "queue.sqlite")


# Real intake prompt styles from observed dead-end sessions.
_PROMPT_REPO_ONLY = "https://github.com/yule-studio/naver-search-clone"
_PROMPT_IMPLEMENTATION = (
    "https://github.com/yule-studio/naver-search-clone "
    "실제 구현 가능한 상태까지 구현"
)
_PROMPT_AUTO_ISSUE = (
    "https://github.com/yule-studio/naver-search-clone "
    "새 GitHub issue를 생성해서 그 issue anchor 기준으로 시작"
)


# ---------------------------------------------------------------------------
# Route gate: approved_continuation flag
# ---------------------------------------------------------------------------


class ShouldRouteApprovedContinuationTests(unittest.TestCase):
    """``should_route_to_github_workos(..., approved_continuation=True)``
    가 prompt phrase 기반 reject 를 건너뛰지만 lifecycle / obsidian
    가드는 유지."""

    def test_approved_continuation_passes_repo_only_prompt(self) -> None:
        session = _FakeSession.make(extra=_handoff_extra(), prompt=_PROMPT_REPO_ONLY)
        eligible, reason, _ = should_route_to_github_workos(
            session=session,
            request_text=session.prompt,
            approved_continuation=True,
        )
        self.assertTrue(eligible, reason)
        self.assertEqual(reason, "")

    def test_approved_continuation_passes_implementation_natural_language(self) -> None:
        session = _FakeSession.make(extra=_handoff_extra(), prompt=_PROMPT_IMPLEMENTATION)
        eligible, reason, _ = should_route_to_github_workos(
            session=session,
            request_text=session.prompt,
            approved_continuation=True,
        )
        self.assertTrue(eligible, reason)

    def test_approved_continuation_passes_auto_issue_prompt(self) -> None:
        session = _FakeSession.make(extra=_handoff_extra(), prompt=_PROMPT_AUTO_ISSUE)
        eligible, reason, _ = should_route_to_github_workos(
            session=session,
            request_text=session.prompt,
            approved_continuation=True,
        )
        self.assertTrue(eligible, reason)

    def test_approved_continuation_research_only_lifecycle_still_blocks(self) -> None:
        session = _FakeSession.make(
            extra=_handoff_extra(lifecycle_mode="research_only"),
            prompt="이거 구현해줘 — 모든 기능 풀스택",
        )
        eligible, reason, _ = should_route_to_github_workos(
            session=session,
            request_text=session.prompt,
            approved_continuation=True,
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIPPED_RESEARCH_ONLY)

    def test_approved_continuation_obsidian_intent_still_blocks(self) -> None:
        # obsidian save intent — different domain, 반드시 reject.
        session = _FakeSession.make(extra=_handoff_extra(), prompt="vault 에 저장해줘")
        eligible, reason, _ = should_route_to_github_workos(
            session=session,
            request_text=session.prompt,
            approved_continuation=True,
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIPPED_OBSIDIAN_INTENT)


# ---------------------------------------------------------------------------
# Fresh intake routing 회귀 — approved_continuation=False 면 옛 동작
# ---------------------------------------------------------------------------


class FreshIntakeRoutingRegressionTests(unittest.TestCase):
    def test_fresh_intake_repo_only_prompt_still_rejected(self) -> None:
        """fresh intake 에서 prompt 가 약하면 옛 동작 그대로 reject — 무지성
        통과 금지."""

        session = _FakeSession.make(extra=_handoff_extra(), prompt=_PROMPT_REPO_ONLY)
        eligible, reason, _ = should_route_to_github_workos(
            session=session,
            request_text=session.prompt,
            # default approved_continuation=False
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIPPED_NO_CODING_INTENT)

    def test_fresh_intake_with_strong_coding_phrase_passes(self) -> None:
        session = _FakeSession.make(extra=_handoff_extra(), prompt="이거 구현해줘")
        eligible, reason, _ = should_route_to_github_workos(
            session=session,
            request_text=session.prompt,
        )
        self.assertTrue(eligible, reason)

    def test_fresh_intake_research_only_lifecycle_blocks(self) -> None:
        session = _FakeSession.make(
            extra=_handoff_extra(lifecycle_mode="research_only"),
            prompt="이거 구현해줘",
        )
        eligible, reason, _ = should_route_to_github_workos(
            session=session,
            request_text=session.prompt,
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, SKIPPED_RESEARCH_ONLY)


# ---------------------------------------------------------------------------
# Live builder: actual build_github_work_order_proposal
# ---------------------------------------------------------------------------


class LiveBuilderApprovedContinuationTests(unittest.TestCase):
    """**Real builder** — fake_builder 안 씀.  approved continuation 경로의
    proposal 이 정말 None 이 아니라 채워지는지."""

    def test_real_builder_with_repo_only_prompt_returns_proposal(self) -> None:
        session = _FakeSession.make(
            extra=_handoff_extra(),
            prompt=_PROMPT_REPO_ONLY,
        )
        proposal = build_github_work_order_proposal(
            session=session,
            request_text=session.prompt,
            repo="yule-studio/naver-search-clone",
            approved_continuation=True,
        )
        self.assertIsNotNone(proposal, "approved continuation 에서 proposal=None 회귀")
        self.assertEqual(proposal.session_id, "f2f36607d175")
        self.assertEqual(proposal.repo, "yule-studio/naver-search-clone")
        self.assertTrue(proposal.coding_required)
        # P1-Z3 — prompt phrase 가 약해도 intent_actions 가 비지 않도록 fallback
        self.assertTrue(proposal.intent_actions)

    def test_real_builder_with_implementation_prompt_returns_proposal(self) -> None:
        session = _FakeSession.make(
            extra=_handoff_extra(),
            prompt=_PROMPT_IMPLEMENTATION,
        )
        proposal = build_github_work_order_proposal(
            session=session,
            request_text=session.prompt,
            repo="yule-studio/naver-search-clone",
            approved_continuation=True,
        )
        self.assertIsNotNone(proposal)

    def test_real_builder_with_auto_issue_prompt_returns_proposal(self) -> None:
        session = _FakeSession.make(
            extra=_handoff_extra(),
            prompt=_PROMPT_AUTO_ISSUE,
        )
        proposal = build_github_work_order_proposal(
            session=session,
            request_text=session.prompt,
            repo="yule-studio/naver-search-clone",
            approved_continuation=True,
        )
        self.assertIsNotNone(proposal)

    def test_real_builder_default_path_still_rejects_weak_prompt(self) -> None:
        """approved_continuation 명시 안 하면 옛 builder 동작 그대로."""

        session = _FakeSession.make(extra=_handoff_extra(), prompt=_PROMPT_REPO_ONLY)
        proposal = build_github_work_order_proposal(
            session=session,
            request_text=session.prompt,
            repo="yule-studio/naver-search-clone",
            # default approved_continuation=False
        )
        self.assertIsNone(proposal, "fresh intake 에서 약한 prompt 통과 회귀")

    def test_real_builder_research_only_lifecycle_returns_none(self) -> None:
        session = _FakeSession.make(
            extra=_handoff_extra(lifecycle_mode="research_only"),
            prompt=_PROMPT_IMPLEMENTATION,
        )
        proposal = build_github_work_order_proposal(
            session=session,
            request_text=session.prompt,
            repo="yule-studio/naver-search-clone",
            approved_continuation=True,
        )
        self.assertIsNone(proposal)


# ---------------------------------------------------------------------------
# End-to-end: dispatch_post_approval_work_order with real builder
# ---------------------------------------------------------------------------


class DispatchPostApprovalWithRealBuilderTests(unittest.TestCase):
    def test_actual_dead_end_shape_dispatches_through_real_builder(self) -> None:
        """f2f36607d175 류 shape — real builder path 가 실제 queue insert."""

        session = _FakeSession.make(
            extra=_handoff_extra(),
            prompt=_PROMPT_IMPLEMENTATION,
        )
        result = dispatch_post_approval_work_order(
            session=session,
            queue=_in_memory_queue(),
            requested_by="cli-user",
            # default proposal_builder = real build_github_work_order_proposal
        )
        self.assertEqual(result.get("action"), ACTION_DISPATCHED, result)
        self.assertIsNotNone(result.get("job_id"))
        self.assertEqual(result.get("repo"), "yule-studio/naver-search-clone")

    def test_research_only_still_noop(self) -> None:
        session = _FakeSession.make(
            extra=_handoff_extra(lifecycle_mode="research_only"),
            prompt=_PROMPT_IMPLEMENTATION,
        )
        result = dispatch_post_approval_work_order(
            session=session,
            queue=_in_memory_queue(),
            requested_by="cli-user",
        )
        self.assertEqual(result.get("action"), ACTION_NOOP)
        self.assertEqual(result.get("reason"), NOOP_REASON_RESEARCH_ONLY_LIFECYCLE)

    def test_terminal_session_still_noop_even_with_real_builder(self) -> None:
        from yule_orchestrator.agents.job_queue.post_approval_dispatch import (
            NOOP_REASON_TERMINAL_SESSION,
        )

        session = _FakeSession.make(
            state="rejected",
            extra=_handoff_extra(),
            prompt=_PROMPT_IMPLEMENTATION,
        )
        result = dispatch_post_approval_work_order(
            session=session,
            queue=_in_memory_queue(),
            requested_by="cli-user",
        )
        self.assertEqual(result.get("action"), ACTION_NOOP)
        self.assertEqual(result.get("reason"), NOOP_REASON_TERMINAL_SESSION)

    def test_idempotent_second_dispatch_dedups(self) -> None:
        """같은 session 으로 두 번 dispatch → 두 번째는 duplicate dedup."""

        session = _FakeSession.make(
            extra=_handoff_extra(),
            prompt=_PROMPT_IMPLEMENTATION,
        )
        queue = _in_memory_queue()
        first = dispatch_post_approval_work_order(
            session=session,
            queue=queue,
            requested_by="cli-user",
        )
        self.assertEqual(first.get("action"), ACTION_DISPATCHED, first)
        second = dispatch_post_approval_work_order(
            session=session,
            queue=queue,
            requested_by="cli-user",
        )
        # find_active_work_order 가 같은 session_id 매칭 → skipped_reason 으로 noop.
        self.assertEqual(second.get("action"), ACTION_NOOP, second)


# ---------------------------------------------------------------------------
# Source-grep wiring guards
# ---------------------------------------------------------------------------


class WiringGuardTests(unittest.TestCase):
    def test_should_route_signature_has_approved_continuation(self) -> None:
        import inspect

        from yule_orchestrator.discord.integrations import (
            github_workos_adapter as adapter_mod,
        )

        sig = inspect.signature(adapter_mod.should_route_to_github_workos)
        self.assertIn("approved_continuation", sig.parameters)
        self.assertFalse(sig.parameters["approved_continuation"].default)

    def test_build_proposal_signature_has_approved_continuation(self) -> None:
        import inspect

        from yule_orchestrator.discord.integrations import (
            github_workos_adapter as adapter_mod,
        )

        sig = inspect.signature(adapter_mod.build_github_work_order_proposal)
        self.assertIn("approved_continuation", sig.parameters)

    def test_dispatch_post_approval_passes_approved_continuation(self) -> None:
        import inspect

        from yule_orchestrator.agents.job_queue import (
            post_approval_dispatch as mod,
        )

        source = inspect.getsource(mod.dispatch_post_approval_work_order)
        self.assertIn("approved_continuation=True", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
