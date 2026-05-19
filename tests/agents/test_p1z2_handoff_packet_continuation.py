"""P1-Z2 — actual ``/engineer_intake`` session shape continuation 회귀.

배경
----
P1-Z 의 ``decide_post_approval_action`` 은 ``extra["coding_proposal"]`` 가
없으면 즉시 ``NOOP_REASON_NO_CODING_PROPOSAL`` 로 종료했다.  하지만
실제 dead-end session 인 ``f2f36607d175`` 의 shape:

  * ``extra.github_target`` 있음
  * ``extra.coding_handoff_packet`` 있음
  * ``extra.lifecycle_mode == "implementation"`` 있음
  * ``extra.coding_proposal`` **없음**

이 회귀 라인은 위 shape (handoff packet + lifecycle_mode + github_target)
가 ``needs_work_order`` 로 인정되고, 동시에 false-positive (research_only /
no target / no repo / terminal / anchor 있음) 케이스는 여전히 noop 으로
머무는지 확정한다.

stdlib unittest 만 사용.
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
    ACTION_NEEDS_WORK_ORDER,
    ACTION_NOOP,
    NOOP_REASON_ANCHOR_ALREADY_STAMPED,
    NOOP_REASON_NO_CODING_INTENT_SIGNAL,
    NOOP_REASON_NO_CODING_PROPOSAL,
    NOOP_REASON_NO_GITHUB_TARGET,
    NOOP_REASON_NO_REPO,
    NOOP_REASON_RESEARCH_ONLY_LIFECYCLE,
    NOOP_REASON_TERMINAL_SESSION,
    decide_post_approval_action,
    dispatch_post_approval_work_order,
)


# ---------------------------------------------------------------------------
# Fixtures — mimic real intake-time session.extra shape
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
    prompt: str = (
        "yule-studio/naver-search-clone 에 검색 풀스택 MVP "
        "(인증 + 검색 + 블로그 + 메일) 구현해줘"
    )
    channel_id: Optional[int] = 9100
    thread_id: Optional[int] = 9200

    @classmethod
    def make(
        cls,
        *,
        session_id: str = "f2f36607d175",
        state: str = "approved",
        extra: Optional[Mapping[str, Any]] = None,
        prompt: Optional[str] = None,
        channel_id: Optional[int] = 9100,
        thread_id: Optional[int] = 9200,
    ) -> "_FakeSession":
        return cls(
            session_id=session_id,
            state=_State(state),
            extra=dict(extra or {}),
            prompt=prompt or (
                "yule-studio/naver-search-clone 에 검색 풀스택 MVP "
                "(인증 + 검색 + 블로그 + 메일) 구현해줘"
            ),
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
    include_target: bool = True,
    include_packet: bool = True,
    coding_proposal: Optional[Mapping[str, Any]] = None,
    anchor: Optional[Mapping[str, Any]] = None,
) -> dict:
    extra: dict[str, Any] = {}
    if include_target:
        extra["github_target"] = dict(_TARGET_REPO)
    if include_packet:
        extra["coding_handoff_packet"] = {
            "canonical_request": "검색 풀스택 MVP 구현",
            "github_target": dict(_TARGET_REPO),
            "repo_contract_summary": "yule-studio/naver-search-clone (default branch=main)",
            "mode": "approval_required",
            "topology": "single_repo",
            "scope_mode": "full_stack_single_repo",
            "tracking_mode": "repo_root",
            "next_action": "open_issue",
            "notes": {},
        }
    if lifecycle_mode is not None:
        extra["lifecycle_mode"] = lifecycle_mode
    if coding_proposal is not None:
        extra["coding_proposal"] = dict(coding_proposal)
    if anchor is not None:
        extra["github_work_order_issue"] = dict(anchor)
    return extra


def _in_memory_queue():
    tmpdir = tempfile.mkdtemp(prefix="yule-p1z2-")
    from yule_orchestrator.agents.job_queue.store import JobQueue

    return JobQueue(db_path=Path(tmpdir) / "queue.sqlite")


# ---------------------------------------------------------------------------
# Actual dead-end shape — handoff packet path
# ---------------------------------------------------------------------------


class HandoffPacketContinuationTests(unittest.TestCase):
    """실제 ``f2f36607d175`` 류 shape 의 continuation 회귀."""

    def test_actual_dead_end_shape_needs_work_order(self) -> None:
        """state=approved + packet + target + lifecycle=implementation +
        no coding_proposal → needs_work_order (옛 NO_CODING_PROPOSAL noop 차단)."""

        session = _FakeSession.make(state="approved", extra=_handoff_extra())
        decision = decide_post_approval_action(session)
        self.assertEqual(decision.action, ACTION_NEEDS_WORK_ORDER, decision)
        self.assertEqual(decision.repo, "yule-studio/naver-search-clone")
        self.assertIsNone(decision.existing_issue_number)

    def test_handoff_packet_path_dispatches_through_builder(self) -> None:
        """end-to-end: handoff-only session 이 실제 queue insert 까지 진행."""

        captured: dict[str, Any] = {}

        def fake_builder(**kwargs):
            captured.update(kwargs)

            class _Proposal:
                proposal_id = "p-handoff-1"
                session_id = kwargs["session"].session_id
                source_channel_id = kwargs["source_channel_id"]
                source_thread_id = kwargs["source_thread_id"]
                source_message_id = kwargs["source_message_id"]
                request_summary = kwargs["request_text"][:120]
                selected_roles = ("tech-lead", "fullstack-engineer")
                intent_actions = ("코드 변경",)
                repo = kwargs["repo"]
                base_branch = "main"
                dry_run_default = False
                extra: Mapping[str, Any] = {}
                issue_auto_create_plan = None
                existing_issue_number = kwargs.get("existing_issue_number")

            return _Proposal()

        session = _FakeSession.make(state="approved", extra=_handoff_extra())
        queue = _in_memory_queue()
        result = dispatch_post_approval_work_order(
            session=session,
            queue=queue,
            requested_by="cli-user",
            proposal_builder=fake_builder,
        )
        # builder 가 실제 호출됐는지 — coding_proposal 없어도 진입.
        self.assertIn("repo", captured)
        self.assertEqual(captured["repo"], "yule-studio/naver-search-clone")
        # decide 가 needs_work_order → dispatch 가 dispatched 또는 noop(dedup)
        # 둘 다 NOT failed/no_coding_proposal.
        self.assertIn(result["action"], (ACTION_DISPATCHED, ACTION_NOOP))
        self.assertNotEqual(result.get("reason"), NOOP_REASON_NO_CODING_PROPOSAL)

    def test_handoff_packet_with_existing_issue_anchor_in_extra(self) -> None:
        extra = _handoff_extra()
        extra["existing_issue_number"] = 5
        decision = decide_post_approval_action(
            _FakeSession.make(state="approved", extra=extra)
        )
        self.assertEqual(decision.action, ACTION_NEEDS_WORK_ORDER)
        self.assertEqual(decision.existing_issue_number, 5)

    def test_handoff_packet_with_issue_kind_target(self) -> None:
        extra = _handoff_extra()
        extra["github_target"] = {
            "kind": "issue",
            "owner": "yule-studio",
            "repo": "naver-search-clone",
            "number": 12,
        }
        # handoff packet 안의 target 도 issue 로 매칭.
        extra["coding_handoff_packet"]["github_target"] = dict(extra["github_target"])
        decision = decide_post_approval_action(
            _FakeSession.make(state="approved", extra=extra)
        )
        self.assertEqual(decision.action, ACTION_NEEDS_WORK_ORDER)
        self.assertEqual(decision.existing_issue_number, 12)

    def test_handoff_packet_target_in_packet_only_still_works(self) -> None:
        """session.extra.github_target 누락이지만 packet 내부에는 있음 →
        decision 이 packet target 으로 fallback 해서 needs_work_order."""

        extra = _handoff_extra(include_target=False)
        decision = decide_post_approval_action(
            _FakeSession.make(state="approved", extra=extra)
        )
        self.assertEqual(decision.action, ACTION_NEEDS_WORK_ORDER, decision)
        self.assertEqual(decision.repo, "yule-studio/naver-search-clone")


# ---------------------------------------------------------------------------
# False-positive guards — handoff packet 만으로 다 통과시키면 안 됨
# ---------------------------------------------------------------------------


class HandoffPacketFalsePositiveGuardTests(unittest.TestCase):
    def test_handoff_packet_with_research_only_lifecycle_is_noop(self) -> None:
        decision = decide_post_approval_action(
            _FakeSession.make(
                state="approved",
                extra=_handoff_extra(lifecycle_mode="research_only"),
            )
        )
        self.assertEqual(decision.action, ACTION_NOOP)
        self.assertEqual(decision.reason, NOOP_REASON_RESEARCH_ONLY_LIFECYCLE)

    def test_handoff_packet_without_lifecycle_signal_is_noop(self) -> None:
        decision = decide_post_approval_action(
            _FakeSession.make(
                state="approved",
                extra=_handoff_extra(lifecycle_mode=None),
            )
        )
        self.assertEqual(decision.action, ACTION_NOOP)
        self.assertEqual(decision.reason, NOOP_REASON_NO_CODING_INTENT_SIGNAL)

    def test_handoff_packet_with_unknown_lifecycle_is_noop(self) -> None:
        # "ideation" / 기타 임의 token → implementation 명시가 아니므로 noop.
        decision = decide_post_approval_action(
            _FakeSession.make(
                state="approved",
                extra=_handoff_extra(lifecycle_mode="ideation"),
            )
        )
        self.assertEqual(decision.action, ACTION_NOOP)
        self.assertEqual(decision.reason, NOOP_REASON_NO_CODING_INTENT_SIGNAL)

    def test_no_handoff_packet_no_proposal_is_noop(self) -> None:
        # 옛 NO_CODING_PROPOSAL 경로 그대로.
        decision = decide_post_approval_action(
            _FakeSession.make(
                state="approved",
                extra=_handoff_extra(include_packet=False, lifecycle_mode="implementation"),
            )
        )
        self.assertEqual(decision.action, ACTION_NOOP)
        self.assertEqual(decision.reason, NOOP_REASON_NO_CODING_PROPOSAL)

    def test_handoff_packet_no_target_anywhere_is_noop(self) -> None:
        # packet 도 target 없음 + extra 도 target 없음 → no_github_target.
        extra = _handoff_extra(include_target=False)
        extra["coding_handoff_packet"]["github_target"] = None
        decision = decide_post_approval_action(
            _FakeSession.make(state="approved", extra=extra)
        )
        self.assertEqual(decision.action, ACTION_NOOP)
        self.assertEqual(decision.reason, NOOP_REASON_NO_GITHUB_TARGET)

    def test_handoff_packet_target_without_owner_repo_is_no_repo(self) -> None:
        extra = _handoff_extra()
        extra["github_target"] = {"kind": "repo", "owner": "", "repo": ""}
        extra["coding_handoff_packet"]["github_target"] = {"kind": "repo", "owner": "", "repo": ""}
        decision = decide_post_approval_action(
            _FakeSession.make(state="approved", extra=extra)
        )
        self.assertEqual(decision.action, ACTION_NOOP)
        self.assertEqual(decision.reason, NOOP_REASON_NO_REPO)

    def test_handoff_packet_with_existing_anchor_is_noop(self) -> None:
        decision = decide_post_approval_action(
            _FakeSession.make(
                state="approved",
                extra=_handoff_extra(
                    anchor={"issue_number": 5, "repo": "yule-studio/naver-search-clone"}
                ),
            )
        )
        self.assertEqual(decision.action, ACTION_NOOP)
        self.assertEqual(decision.reason, NOOP_REASON_ANCHOR_ALREADY_STAMPED)

    def test_handoff_packet_terminal_session_is_noop(self) -> None:
        for state in ("rejected", "completed"):
            with self.subTest(state=state):
                decision = decide_post_approval_action(
                    _FakeSession.make(state=state, extra=_handoff_extra())
                )
                self.assertEqual(decision.action, ACTION_NOOP)
                self.assertEqual(decision.reason, NOOP_REASON_TERMINAL_SESSION)


# ---------------------------------------------------------------------------
# Existing coding_proposal path — 회귀 없음
# ---------------------------------------------------------------------------


class CodingProposalPathRegressionTests(unittest.TestCase):
    def test_coding_proposal_path_still_needs_work_order(self) -> None:
        # P1-Z 의 옛 경로 — coding_proposal + github_target → needs_work_order.
        extra = {
            "coding_proposal": {
                "executor_role": "fullstack-engineer",
                "review_roles": ["tech-lead"],
            },
            "github_target": dict(_TARGET_REPO),
        }
        decision = decide_post_approval_action(
            _FakeSession.make(state="approved", extra=extra)
        )
        self.assertEqual(decision.action, ACTION_NEEDS_WORK_ORDER)
        self.assertEqual(decision.repo, "yule-studio/naver-search-clone")

    def test_coding_proposal_overrides_missing_lifecycle(self) -> None:
        """coding_proposal 가 있으면 lifecycle_mode 누락이라도 needs_work_order."""

        extra = {
            "coding_proposal": {"executor_role": "fullstack-engineer"},
            "github_target": dict(_TARGET_REPO),
        }
        decision = decide_post_approval_action(
            _FakeSession.make(state="approved", extra=extra)
        )
        self.assertEqual(decision.action, ACTION_NEEDS_WORK_ORDER)

    def test_coding_proposal_with_research_only_lifecycle_is_noop(self) -> None:
        """coding_proposal 가 있어도 lifecycle 가 research_only 면 noop —
        안전 우선."""

        extra = {
            "coding_proposal": {"executor_role": "fullstack-engineer"},
            "github_target": dict(_TARGET_REPO),
            "lifecycle_mode": "research_only",
        }
        decision = decide_post_approval_action(
            _FakeSession.make(state="approved", extra=extra)
        )
        self.assertEqual(decision.action, ACTION_NOOP)
        self.assertEqual(decision.reason, NOOP_REASON_RESEARCH_ONLY_LIFECYCLE)


# ---------------------------------------------------------------------------
# Source-grep wiring guard
# ---------------------------------------------------------------------------


class WiringGuardTests(unittest.TestCase):
    def test_decide_source_branches_on_handoff_packet(self) -> None:
        import inspect

        from yule_orchestrator.agents.job_queue import (
            post_approval_dispatch as mod,
        )

        source = inspect.getsource(mod.decide_post_approval_action)
        self.assertIn("coding_handoff_packet", source)
        self.assertIn("lifecycle_mode", source)
        self.assertIn("research_only", source)
        # 옛 NO_CODING_PROPOSAL early-return 이 사라졌는지 (handoff 분기 이후로 이동).
        # 단순히 string 이 존재할 수는 있으므로 placement 회귀는 위 _no_handoff_packet
        # test 가 보장.

    def test_resolve_repo_source_reads_packet(self) -> None:
        import inspect

        from yule_orchestrator.agents.job_queue import (
            post_approval_dispatch as mod,
        )

        source = inspect.getsource(mod._resolve_repo)
        self.assertIn("coding_handoff_packet", source)
        self.assertIn("github_target", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
