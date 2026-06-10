"""P0-H stage 2 commit 7 — gateway 통합 helper + no-repeat-question 회귀.

Integration tests for :func:`prepare_coding_session_context`:

  1. Fresh session + GitHub URL → extras include github_target,
     work_mode/topology/scope (defaults), repo_contract dict,
     coding_handoff_packet.
  2. Existing session with mode set → second call does **not** prompt.
  3. User hint in message → mode applied + needs_question=False when
     all three fields hinted.
  4. Multi-repo distinct URLs → topology auto-bumped to multi_repo.
  5. No GitHub URL → coding_capable=False but extras still contain
     mode keys (defaults) + handoff packet.
  6. Mode question text only emitted when ``needs_question=True``.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.coding.coding_session_context import (
    CodingSessionContext,
    merge_into_extra,
    prepare_coding_session_context,
)


# ---------------------------------------------------------------------------
# Fresh session — URL parsed, defaults applied, packet built
# ---------------------------------------------------------------------------


class FreshSessionTests(unittest.TestCase):
    def test_github_url_yields_full_extras(self) -> None:
        ctx = prepare_coding_session_context(
            message_text="이 PR 검토해줘",
            user_links=("https://github.com/yule-studio/yule-studio-agent/pull/142",),
            existing_extra={},
            discover_contract=False,
        )
        # github_target captured.
        self.assertIsNotNone(ctx.github_target)
        assert ctx.github_target is not None
        self.assertEqual(ctx.github_target.kind, "pull_request")
        self.assertEqual(ctx.github_target.number, 142)
        # Mode defaults applied (needs_question=True).
        self.assertEqual(ctx.session_mode.work_mode, "approval_required")
        self.assertEqual(ctx.session_mode.topology, "single_repo")
        self.assertEqual(ctx.session_mode.scope, "single_scope")
        self.assertIsNotNone(ctx.mode_question)
        # extras_update contains the keys gateway should persist.
        self.assertEqual(ctx.extras_update["work_mode"], "approval_required")
        self.assertEqual(ctx.extras_update["topology"], "single_repo")
        self.assertEqual(ctx.extras_update["scope"], "single_scope")
        self.assertEqual(
            ctx.extras_update["github_target"]["kind"], "pull_request"
        )
        self.assertEqual(ctx.extras_update["pull_request_number"], 142)
        # Handoff packet built with correct tracking_mode.
        self.assertEqual(ctx.handoff_packet.tracking_mode, "pull_request")
        self.assertEqual(ctx.handoff_packet.next_action, "analyze_pr")
        # coding_capable True because a GitHub target exists.
        self.assertTrue(ctx.coding_capable)
        # coding_handoff_packet present in extras for round-trip.
        self.assertIn("coding_handoff_packet", ctx.extras_update)

    def test_no_url_still_persists_mode_defaults(self) -> None:
        ctx = prepare_coding_session_context(
            message_text="그냥 작업해줘",
            user_links=(),
            existing_extra={},
            discover_contract=False,
        )
        self.assertIsNone(ctx.github_target)
        self.assertIsNone(ctx.repo_contract)
        self.assertFalse(ctx.coding_capable)
        # Mode defaults still persisted so the session has a decided mode.
        self.assertEqual(ctx.extras_update["work_mode"], "approval_required")
        self.assertIsNotNone(ctx.mode_question)
        # Handoff still built (canonical_request fallback).
        self.assertEqual(ctx.handoff_packet.tracking_mode, "standalone")
        self.assertEqual(ctx.handoff_packet.next_action, "ask_user")


# ---------------------------------------------------------------------------
# No repeated question regression
# ---------------------------------------------------------------------------


class NoRepeatedQuestionRegressionTests(unittest.TestCase):
    def test_back_to_back_calls_only_prompt_once(self) -> None:
        # First call writes defaults + asks.
        existing: dict = {}
        ctx1 = prepare_coding_session_context(
            message_text="작업해줘",
            user_links=("https://github.com/foo/bar",),
            existing_extra=existing,
            discover_contract=False,
        )
        self.assertIsNotNone(ctx1.mode_question)
        self.assertTrue(ctx1.mode_decision.needs_question)

        # Merge into existing — this is what the gateway does.
        merged = merge_into_extra(existing, ctx1.extras_update)

        # Second call with the merged extra — must NOT prompt.
        ctx2 = prepare_coding_session_context(
            message_text="이어서 작업",
            user_links=("https://github.com/foo/bar",),
            existing_extra=merged,
            discover_contract=False,
        )
        self.assertIsNone(ctx2.mode_question)
        self.assertFalse(ctx2.mode_decision.needs_question)
        # Mode values preserved from the first call.
        self.assertEqual(ctx2.session_mode.work_mode, "approval_required")
        # Third / fourth / fifth — same.
        for _ in range(3):
            ctx_n = prepare_coding_session_context(
                message_text="더 작업",
                user_links=("https://github.com/foo/bar",),
                existing_extra=merged,
                discover_contract=False,
            )
            self.assertIsNone(ctx_n.mode_question)

    def test_existing_mode_with_different_message_no_repeat(self) -> None:
        existing = {
            "work_mode": "autonomous_merge",
            "topology": "multi_repo",
            "scope": "cross_repo_program",
            "mode_decided_by": "user_explicit",
            "mode_decided_at": "2026-05-13T00:00:00+00:00",
        }
        ctx = prepare_coding_session_context(
            message_text="새 메시지인데 모드 다시 묻지 마",
            user_links=(),
            existing_extra=existing,
            discover_contract=False,
        )
        self.assertIsNone(ctx.mode_question)
        self.assertEqual(ctx.session_mode.work_mode, "autonomous_merge")
        self.assertEqual(ctx.session_mode.topology, "multi_repo")


# ---------------------------------------------------------------------------
# User hints in message
# ---------------------------------------------------------------------------


class UserHintTests(unittest.TestCase):
    def test_korean_hints_for_all_three_skip_question(self) -> None:
        ctx = prepare_coding_session_context(
            message_text=(
                "자율 머지로 단일 repo 작업하자. "
                "topology: single_repo / scope: single_scope"
            ),
            user_links=(),
            existing_extra={},
            discover_contract=False,
        )
        # All three hints → no question.
        self.assertIsNone(ctx.mode_question)
        self.assertEqual(ctx.session_mode.work_mode, "autonomous_merge")
        self.assertEqual(ctx.session_mode.decided_by, "user_explicit")

    def test_partial_hint_still_asks(self) -> None:
        ctx = prepare_coding_session_context(
            message_text="자율 머지로 가자",
            user_links=(),
            existing_extra={},
            discover_contract=False,
        )
        self.assertEqual(ctx.session_mode.work_mode, "autonomous_merge")
        self.assertIsNotNone(ctx.mode_question)  # topology / scope 부분 정의


# ---------------------------------------------------------------------------
# Multi-repo auto-bump
# ---------------------------------------------------------------------------


class TopologyAutoBumpTests(unittest.TestCase):
    def test_two_distinct_repos_bump_to_multi_repo(self) -> None:
        ctx = prepare_coding_session_context(
            message_text="이 두 repo 동기화",
            user_links=(
                "https://github.com/foo/alpha/issues/1",
                "https://github.com/foo/beta/pull/2",
            ),
            existing_extra={},
            discover_contract=False,
        )
        self.assertEqual(ctx.session_mode.topology, "multi_repo")

    def test_same_repo_twice_keeps_single_repo(self) -> None:
        ctx = prepare_coding_session_context(
            message_text="같은 repo 의 이슈 + PR",
            user_links=(
                "https://github.com/foo/bar/issues/1",
                "https://github.com/foo/bar/pull/2",
            ),
            existing_extra={},
            discover_contract=False,
        )
        self.assertEqual(ctx.session_mode.topology, "single_repo")


# ---------------------------------------------------------------------------
# RepoContract integration (mocked discovery)
# ---------------------------------------------------------------------------


class RepoContractIntegrationTests(unittest.TestCase):
    def test_repo_contract_summary_in_extras(self) -> None:
        # Inject a mocked discover_repo_contract via the helper's import path.
        # Easiest: patch the symbol in the coding_session_context module.
        from yule_engineering.agents.coding import (
            coding_session_context as csc,
        )
        from yule_engineering.agents.git.repo_contract import RepoContract

        def fake_discover(*, owner, repo, **kwargs):
            return RepoContract(
                owner=owner,
                repo=repo,
                pr_templates=(".github/PULL_REQUEST_TEMPLATE.md",),
                backend="local_clone",
            )

        with patch.object(csc, "discover_repo_contract", side_effect=fake_discover):
            ctx = prepare_coding_session_context(
                message_text="작업",
                user_links=("https://github.com/foo/bar",),
                existing_extra={},
                discover_contract=True,
            )
        self.assertIsNotNone(ctx.repo_contract)
        assert ctx.repo_contract is not None
        self.assertEqual(ctx.repo_contract.backend, "local_clone")
        # extras_update has both serialized form and summary string.
        self.assertIn("repo_contract", ctx.extras_update)
        self.assertIn("repo_contract_summary", ctx.extras_update)
        self.assertIn("✅ foo/bar", ctx.extras_update["repo_contract_summary"])

    def test_discover_contract_false_skips_lookup(self) -> None:
        # When the caller opts out, no RepoContract is added even though
        # a GitHub URL is present.
        ctx = prepare_coding_session_context(
            message_text="작업",
            user_links=("https://github.com/foo/bar",),
            existing_extra={},
            discover_contract=False,
        )
        self.assertIsNone(ctx.repo_contract)
        self.assertNotIn("repo_contract", ctx.extras_update)


# ---------------------------------------------------------------------------
# merge_into_extra helper
# ---------------------------------------------------------------------------


class MergeHelperTests(unittest.TestCase):
    def test_merge_preserves_other_keys(self) -> None:
        existing = {"unrelated_key": "preserve_me"}
        merged = merge_into_extra(existing, {"work_mode": "approval_required"})
        self.assertEqual(merged["unrelated_key"], "preserve_me")
        self.assertEqual(merged["work_mode"], "approval_required")
        # Source dict not mutated.
        self.assertNotIn("work_mode", existing)

    def test_merge_overwrites_on_collision(self) -> None:
        merged = merge_into_extra(
            {"work_mode": "approval_required"},
            {"work_mode": "autonomous_merge"},
        )
        self.assertEqual(merged["work_mode"], "autonomous_merge")


if __name__ == "__main__":
    unittest.main()
