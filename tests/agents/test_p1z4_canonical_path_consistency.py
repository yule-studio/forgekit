"""P1-Z4 — canonical coding path end-to-end consistency 회귀.

배경
----
canonical session ``000f13fb121b`` 가 노출한 5 가지 회귀:

1. slash intake approval card 가 prompt phrase 게이트에 막혀 누락
2. ``PR #183 브랜치`` 문구가 target repo 의 existing issue anchor 로 오인
3. anchor stamp 이후에도 ``tracking_validation.status = needs_issue`` stale
4. ``live_editor_no_edits_produced`` 가 write_scope mismatch 인지 모호
5. 위 4 가지가 누적되어 operator surface 가 일관성 잃음

본 회귀는 위 5 가지가 영구히 회귀하지 않도록 lock.

stdlib unittest 만.
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

from yule_orchestrator.agents.coding.coding_session_context import (
    _disqualified_numbers_in_text,
    _extract_explicit_issue_number,
)
from yule_orchestrator.agents.coding.tracking_refresh import (
    SESSION_EXTRA_TRACKING_KEY,
    refresh_tracking_validation,
)
from yule_orchestrator.agents.job_queue.coding_write_scope_resolution import (
    WriteScopeResolution,
    resolve_write_scope_against_worktree,
)
from yule_orchestrator.discord.integrations.intake_approval_eligibility import (
    SKIP_REASON_NOT_WRITE_REQUESTED,
    SKIP_REASON_NO_GITHUB_TARGET,
    SKIP_REASON_NO_HANDOFF_PACKET,
    SKIP_REASON_NO_IMPLEMENTATION_SIGNAL,
    SKIP_REASON_OBSIDIAN_INTENT,
    SKIP_REASON_RESEARCH_ONLY_LIFECYCLE,
    decide_intake_approval_card_eligibility,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _State:
    def __init__(self, value: str) -> None:
        self.value = value


@dataclass
class _FakeSession:
    session_id: str = "000f13fb121b"
    state: Any = field(default_factory=lambda: _State("intake"))
    extra: Mapping[str, Any] = field(default_factory=dict)
    prompt: str = ""
    channel_id: Optional[int] = 100
    thread_id: Optional[int] = 200
    write_requested: bool = True

    @classmethod
    def make(
        cls,
        *,
        state: str = "intake",
        extra: Optional[Mapping[str, Any]] = None,
        prompt: str = "",
        write_requested: bool = True,
    ) -> "_FakeSession":
        return cls(
            state=_State(state),
            extra=dict(extra or {}),
            prompt=prompt,
            write_requested=write_requested,
        )


_TARGET = {
    "kind": "repo",
    "owner": "yule-studio",
    "repo": "naver-search-clone",
    "number": None,
}


def _intake_extra(
    *,
    lifecycle_mode: Optional[str] = "implementation",
    include_target: bool = True,
    include_packet: bool = True,
) -> dict:
    extra: dict[str, Any] = {}
    if lifecycle_mode is not None:
        extra["lifecycle_mode"] = lifecycle_mode
    if include_target:
        extra["github_target"] = dict(_TARGET)
    if include_packet:
        extra["coding_handoff_packet"] = {
            "canonical_request": "검색 풀스택 MVP 구현",
            "github_target": dict(_TARGET),
            "tracking_mode": "repo_root",
            "next_action": "open_issue",
            "notes": {},
        }
    return extra


# ---------------------------------------------------------------------------
# A — intake approval card eligibility (structured signals)
# ---------------------------------------------------------------------------


class IntakeApprovalCardEligibilityTests(unittest.TestCase):
    """canonical ``000f13fb121b`` shape: write_requested + lifecycle +
    target + handoff 모두 있어야 카드 게시.  prompt phrase 의존 없음."""

    def test_full_canonical_shape_is_eligible(self) -> None:
        session = _FakeSession.make(
            extra=_intake_extra(),
            prompt="https://github.com/yule-studio/naver-search-clone "
            "실제 구현 가능한 상태까지 구현",
        )
        decision = decide_intake_approval_card_eligibility(
            session=session, prompt_text=session.prompt
        )
        self.assertTrue(decision.eligible, decision)
        self.assertIsNone(decision.skip_reason)

    def test_weak_natural_language_prompt_still_eligible(self) -> None:
        """canonical 000f13fb121b 가 카드 누락된 원인 — repo URL only 도
        구조 신호만 갖춰져 있으면 통과."""

        session = _FakeSession.make(
            extra=_intake_extra(),
            prompt="https://github.com/yule-studio/naver-search-clone",
        )
        decision = decide_intake_approval_card_eligibility(
            session=session, prompt_text=session.prompt
        )
        self.assertTrue(decision.eligible)

    def test_issue_anchor_natural_language_eligible(self) -> None:
        session = _FakeSession.make(
            extra=_intake_extra(),
            prompt="새 GitHub issue를 생성해서 그 issue anchor 기준으로 시작",
        )
        decision = decide_intake_approval_card_eligibility(
            session=session, prompt_text=session.prompt
        )
        self.assertTrue(decision.eligible)

    def test_not_write_requested_skips(self) -> None:
        session = _FakeSession.make(extra=_intake_extra(), write_requested=False)
        decision = decide_intake_approval_card_eligibility(
            session=session, prompt_text="anything"
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.skip_reason, SKIP_REASON_NOT_WRITE_REQUESTED)

    def test_research_only_lifecycle_skips(self) -> None:
        session = _FakeSession.make(
            extra=_intake_extra(lifecycle_mode="research_only")
        )
        decision = decide_intake_approval_card_eligibility(
            session=session, prompt_text="구현해줘"
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.skip_reason, SKIP_REASON_RESEARCH_ONLY_LIFECYCLE)

    def test_missing_lifecycle_signal_skips(self) -> None:
        session = _FakeSession.make(extra=_intake_extra(lifecycle_mode=None))
        decision = decide_intake_approval_card_eligibility(
            session=session, prompt_text="구현해줘"
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(
            decision.skip_reason, SKIP_REASON_NO_IMPLEMENTATION_SIGNAL
        )

    def test_no_github_target_skips(self) -> None:
        session = _FakeSession.make(extra=_intake_extra(include_target=False))
        decision = decide_intake_approval_card_eligibility(
            session=session, prompt_text="구현해줘"
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.skip_reason, SKIP_REASON_NO_GITHUB_TARGET)

    def test_no_handoff_packet_skips(self) -> None:
        session = _FakeSession.make(extra=_intake_extra(include_packet=False))
        decision = decide_intake_approval_card_eligibility(
            session=session, prompt_text="구현해줘"
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.skip_reason, SKIP_REASON_NO_HANDOFF_PACKET)

    def test_obsidian_intent_skips(self) -> None:
        session = _FakeSession.make(extra=_intake_extra())
        decision = decide_intake_approval_card_eligibility(
            session=session, prompt_text="vault 에 저장해줘"
        )
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.skip_reason, SKIP_REASON_OBSIDIAN_INTENT)


# ---------------------------------------------------------------------------
# B — existing issue anchor parser: PR / cross-repo disqualifiers
# ---------------------------------------------------------------------------


class ExistingIssueAnchorParserTests(unittest.TestCase):
    """canonical 000f13fb121b 의 ``PR #183 브랜치`` 가 target repo 의
    issue anchor 로 오인되지 않게."""

    def test_pr_hash_number_is_disqualified(self) -> None:
        # 옛 회귀: `PR #183 브랜치` 의 183 이 issue anchor 로 잡힘.
        result = _extract_explicit_issue_number("이 작업은 PR #183 브랜치에서 시작")
        self.assertIsNone(result)

    def test_pull_request_number_is_disqualified(self) -> None:
        result = _extract_explicit_issue_number("Pull Request #42 를 참고")
        self.assertIsNone(result)

    def test_cross_repo_reference_is_disqualified(self) -> None:
        # ``yule-studio-agent#123`` 같은 cross-repo refs.
        result = _extract_explicit_issue_number(
            "yule-studio/yule-studio-agent#123 참고"
        )
        self.assertIsNone(result)

    def test_legitimate_issue_hash_passes(self) -> None:
        result = _extract_explicit_issue_number("이슈 #5 작업")
        self.assertEqual(result, 5)

    def test_issue_keyword_passes(self) -> None:
        result = _extract_explicit_issue_number("issue 42 를 reuse")
        self.assertEqual(result, 42)

    def test_pr_disqualifier_does_not_block_separate_issue_ref(self) -> None:
        # PR #183 은 disqualified, 하지만 "이슈 #5" 는 살아남아야.
        result = _extract_explicit_issue_number("PR #183 닫힌 뒤 이슈 #5 작업")
        self.assertEqual(result, 5)

    def test_disqualified_set_contains_pr_numbers(self) -> None:
        disqualified = _disqualified_numbers_in_text(
            "PR #100 / pull request #200 / yule/repo#300"
        )
        self.assertEqual(disqualified, {100, 200, 300})

    def test_first_segment_falsehood_does_not_skip_following_valid(self) -> None:
        # 첫 매칭 (PR #183) 이 disqualified 더라도 후속 (#5) 가 valid 면
        # 그것을 사용.
        result = _extract_explicit_issue_number(
            "이전 PR #183 후속 이슈 #5 처리"
        )
        self.assertEqual(result, 5)


# ---------------------------------------------------------------------------
# C — tracking_validation refresh after anchor stamp
# ---------------------------------------------------------------------------


class TrackingValidationRefreshTests(unittest.TestCase):
    def test_refresh_after_issue_anchor_no_longer_needs_issue(self) -> None:
        """canonical 000f13fb121b: anchor stamp 됐는데 needs_issue 가 남던
        회귀.  refresh 호출 후 status 가 'ok' 또는 needs_branch 등 다른
        단계로 갱신돼야 함."""

        # 1) 시작 — anchor 없음 → needs_issue
        session = _FakeSession.make(
            state="approved",
            extra={
                "github_target": dict(_TARGET),
                "work_mode": "approval_required",
                "coding_handoff_packet": _intake_extra()["coding_handoff_packet"],
                # 옛 stale tracking_validation
                SESSION_EXTRA_TRACKING_KEY: {
                    "status": "needs_issue",
                    "blocked": True,
                },
            },
        )
        result_before = refresh_tracking_validation(
            session=session, triggered_by="probe_before"
        )
        self.assertEqual(result_before.previous_status, "needs_issue")

        # 2) anchor stamp → target.kind == "issue" + number
        session.extra = dict(session.extra)
        session.extra["github_target"] = {
            "kind": "issue",
            "owner": "yule-studio",
            "repo": "naver-search-clone",
            "number": 5,
        }
        session.extra["branch_name"] = "feature/auth-issue-5"
        result_after = refresh_tracking_validation(
            session=session, triggered_by="anchor_stamp"
        )
        self.assertNotEqual(result_after.new_status, "needs_issue")

    def test_refresh_idempotent_when_no_change(self) -> None:
        session = _FakeSession.make(
            state="intake",
            extra={
                "github_target": dict(_TARGET),
                "work_mode": "approval_required",
                "coding_handoff_packet": _intake_extra()["coding_handoff_packet"],
            },
        )
        first = refresh_tracking_validation(session=session, triggered_by="x")
        # 같은 input 재평가 → 같은 status
        second = refresh_tracking_validation(session=session, triggered_by="y")
        self.assertEqual(first.new_status, second.new_status)


# ---------------------------------------------------------------------------
# D — write_scope vs repo layout resolution
# ---------------------------------------------------------------------------


class WriteScopeResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_placeholder_scope_does_not_match_apps_layout(self) -> None:
        """canonical 000f13fb121b 의 write_scope (``src/<service>/api/**``)
        가 ``apps/`` monorepo 와 0 매칭인지."""

        (self.root / "apps" / "web").mkdir(parents=True)
        (self.root / "apps" / "api").mkdir(parents=True)

        result = resolve_write_scope_against_worktree(
            worktree_path=str(self.root),
            write_scope=(
                "src/<service>/api/**",
                "src/<service>/domain/**",
                "tests/<service>/api/**",
            ),
        )
        self.assertTrue(result.worktree_exists)
        self.assertFalse(result.has_any_match)
        self.assertTrue(result.is_placeholder_scope)
        self.assertTrue(result.can_decide_mismatch)
        self.assertEqual(len(result.unmatched_prefixes), 3)

    def test_matching_scope_finds_sample_paths(self) -> None:
        (self.root / "apps" / "web" / "src").mkdir(parents=True)
        (self.root / "apps" / "web" / "src" / "page.tsx").touch()

        result = resolve_write_scope_against_worktree(
            worktree_path=str(self.root),
            write_scope=("apps/**",),
        )
        self.assertTrue(result.has_any_match)
        self.assertIn("apps/web", result.sample_paths)

    def test_worktree_missing_marks_unknown(self) -> None:
        result = resolve_write_scope_against_worktree(
            worktree_path="/tmp/definitely-not-exist-p1z4",
            write_scope=("src/**",),
        )
        self.assertFalse(result.worktree_exists)
        self.assertFalse(result.can_decide_mismatch)
        # caller 가 generic no-edit 으로 떨어뜨려야 — write_scope_resolved_empty
        # 안 띄움

    def test_empty_scope_returns_unable_to_decide(self) -> None:
        result = resolve_write_scope_against_worktree(
            worktree_path=str(self.root),
            write_scope=(),
        )
        self.assertFalse(result.can_decide_mismatch)


# ---------------------------------------------------------------------------
# Wiring guards
# ---------------------------------------------------------------------------


class WiringGuardTests(unittest.TestCase):
    def test_slash_intake_uses_new_eligibility_helper(self) -> None:
        import inspect

        from yule_orchestrator.discord import commands as cmd_mod

        source = inspect.getsource(cmd_mod._maybe_post_intake_approval_card)
        self.assertIn("decide_intake_approval_card_eligibility", source)
        self.assertIn("trust_session_signals=True", source)

    def test_anchor_parser_source_has_pr_disqualifier(self) -> None:
        import inspect

        from yule_orchestrator.agents.coding import (
            coding_session_context as ctx_mod,
        )

        # module-level patterns 까지 봐야 PR disqualifier 패턴이 보인다.
        source = inspect.getsource(ctx_mod)
        self.assertIn("_PR_DISQUALIFIER_PATTERNS", source)
        self.assertIn(r"\bPR\s", source)
        self.assertIn("pull", source.lower())

    def test_worker_branches_on_write_scope_resolved_empty(self) -> None:
        import inspect

        from yule_orchestrator.agents.job_queue import (
            coding_executor_worker as worker_mod,
        )

        source = inspect.getsource(worker_mod.CodingExecutorWorker)
        self.assertIn("resolve_write_scope_against_worktree", source)
        self.assertIn("REASON_WRITE_SCOPE_RESOLVED_EMPTY", source)

    def test_dispatcher_refreshes_tracking_validation(self) -> None:
        import inspect

        from yule_orchestrator.agents.job_queue import (
            coding_execute_dispatcher as disp_mod,
        )

        source = inspect.getsource(disp_mod._persist_dispatch_marker)
        self.assertIn("refresh_tracking_validation", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
