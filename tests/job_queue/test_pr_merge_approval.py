"""F16 PR-2 — Domain tests for PR merge approval (issue #128).

The :mod:`pr_approval` module is **pure** — no GitHub, no Discord —
so this test suite pins every branch of the 5-step gate, the reply
intent parser, and the summary card renderer without mocks or fakes.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.pr_approval import (
    APPROVAL_KIND_PR_MERGE,
    PRMergeGateResult,
    PRMergeProposal,
    PRMergeReplyIntent,
    PRMergeStatusSnapshot,
    PR_MERGE_AUDIT_KEY,
    build_body_excerpt,
    evaluate_merge_gate,
    make_audit_entry,
    parse_pr_merge_reply_intent,
    render_pr_merge_summary,
)


def _proposal(**overrides) -> PRMergeProposal:
    base = dict(
        repo="yule-studio/yule-studio-agent",
        pr_number=127,
        pr_title="F15 corporate structure",
        pr_url="https://github.com/yule-studio/yule-studio-agent/pull/127",
        head_sha="abc1234567",
        base_branch="main",
        draft=False,
        mergeable_state="clean",
        summary_md="",
        scope_labels=("docs", "agents", "tests"),
        risk="LOW",
        check_runs_summary="✅ Tests: 18/18 PASS",
        branch_protection_summary="🔒 reviews 1/1 ✓",
        body_excerpt="adds 6 dept + 19 roles + PM skills catalog",
        requested_by="alice",
    )
    base.update(overrides)
    return PRMergeProposal(**base)


def _snapshot(**overrides) -> PRMergeStatusSnapshot:
    base = dict(
        draft=False,
        mergeable=True,
        mergeable_state="clean",
        head_sha="abc1234567",
        check_conclusions=("success", "success", "success"),
        required_status_checks=("ci",),
        required_approving_reviews=1,
        actual_approving_reviews=1,
        branch_protection_available=True,
    )
    base.update(overrides)
    return PRMergeStatusSnapshot(**base)


class ConstantTests(unittest.TestCase):
    def test_approval_kind_value(self) -> None:
        self.assertEqual(APPROVAL_KIND_PR_MERGE, "pr_merge")

    def test_audit_key(self) -> None:
        self.assertEqual(PR_MERGE_AUDIT_KEY, "pr_merge_audit")

    def test_reply_intent_extends_base(self) -> None:
        # 5 values total: APPROVE / REJECT / HOLD / UNCLEAR / REVISE_AND_REPEAT.
        values = {m.value for m in PRMergeReplyIntent}
        self.assertEqual(
            values,
            {"approve", "reject", "hold", "unclear", "revise_and_repeat"},
        )


class ParseReplyIntentTests(unittest.TestCase):
    def test_korean_revise_phrase(self) -> None:
        self.assertEqual(
            parse_pr_merge_reply_intent("수정 후 다시"),
            PRMergeReplyIntent.REVISE_AND_REPEAT,
        )

    def test_korean_revise_phrase_no_space(self) -> None:
        self.assertEqual(
            parse_pr_merge_reply_intent("수정후다시"),
            PRMergeReplyIntent.REVISE_AND_REPEAT,
        )

    def test_english_revise_phrase(self) -> None:
        self.assertEqual(
            parse_pr_merge_reply_intent("revise and repeat"),
            PRMergeReplyIntent.REVISE_AND_REPEAT,
        )

    def test_approve_falls_through_to_base(self) -> None:
        self.assertEqual(parse_pr_merge_reply_intent("승인"), PRMergeReplyIntent.APPROVE)

    def test_reject_falls_through(self) -> None:
        self.assertEqual(parse_pr_merge_reply_intent("거절"), PRMergeReplyIntent.REJECT)

    def test_hold_falls_through(self) -> None:
        self.assertEqual(parse_pr_merge_reply_intent("보류"), PRMergeReplyIntent.HOLD)

    def test_unclear_chatter(self) -> None:
        self.assertEqual(
            parse_pr_merge_reply_intent("뭔지 모르겠어"),
            PRMergeReplyIntent.UNCLEAR,
        )

    def test_revise_takes_priority_over_approve(self) -> None:
        # "수정 후 다시 승인" — REVISE wins because it's a stronger signal.
        self.assertEqual(
            parse_pr_merge_reply_intent("수정 후 다시 승인"),
            PRMergeReplyIntent.REVISE_AND_REPEAT,
        )


class BodyExcerptTests(unittest.TestCase):
    def test_short_body_unchanged(self) -> None:
        self.assertEqual(build_body_excerpt("short body"), "short body")

    def test_empty_body(self) -> None:
        self.assertEqual(build_body_excerpt(""), "")
        self.assertEqual(build_body_excerpt(None), "")

    def test_whitespace_collapsed(self) -> None:
        self.assertEqual(build_body_excerpt("a\n\nb   c"), "a b c")

    def test_long_body_truncated_with_ellipsis(self) -> None:
        body = "x" * 400
        excerpt = build_body_excerpt(body)
        self.assertTrue(excerpt.endswith("…"))
        # 280 + 1 char ellipsis.
        self.assertLessEqual(len(excerpt), 281)


class RenderSummaryTests(unittest.TestCase):
    def test_renders_header_and_link(self) -> None:
        text = render_pr_merge_summary(_proposal())
        self.assertIn("PR 머지 승인 — #127", text)
        self.assertIn("F15 corporate structure", text)
        self.assertIn(
            "https://github.com/yule-studio/yule-studio-agent/pull/127", text
        )

    def test_renders_scope_and_risk(self) -> None:
        # P1-R — render_pr_merge_summary 가 한국어 4 섹션 (작업 내용 /
        # 목적 / 영향 범위 / 다음 단계) 형식으로 변경됨.  scope/risk 는
        # "영향 범위" 섹션 안의 bullet 으로 들어감.
        text = render_pr_merge_summary(_proposal())
        self.assertIn("영향 범위", text)
        self.assertIn("docs / agents / tests", text)
        self.assertIn("🟢", text)
        self.assertIn("LOW", text)

    def test_high_risk_emoji_red(self) -> None:
        text = render_pr_merge_summary(_proposal(risk="HIGH"))
        self.assertIn("🔴", text)
        self.assertIn("HIGH", text)

    def test_includes_check_run_and_branch_protection_lines(self) -> None:
        text = render_pr_merge_summary(_proposal())
        self.assertIn("Tests: 18/18 PASS", text)
        self.assertIn("reviews 1/1", text)

    def test_includes_response_vocabulary_hint(self) -> None:
        text = render_pr_merge_summary(_proposal())
        self.assertIn("승인 / 거절 / 수정 후 다시 / 머지 보류", text)

    def test_is_deterministic(self) -> None:
        # Same proposal → same text — important for ApprovalWorker dedup.
        proposal = _proposal()
        self.assertEqual(
            render_pr_merge_summary(proposal),
            render_pr_merge_summary(proposal),
        )


class EvaluateMergeGateTests(unittest.TestCase):
    def test_clean_all_green_allows_merge(self) -> None:
        result = evaluate_merge_gate(_proposal(), _snapshot())
        self.assertTrue(result.allowed)
        self.assertIsNone(result.failed_step)
        self.assertIn("3 checks green", result.checks_summary)

    def test_draft_pr_blocked(self) -> None:
        result = evaluate_merge_gate(_proposal(), _snapshot(draft=True))
        self.assertFalse(result.allowed)
        self.assertEqual(result.failed_step, "draft")

    def test_unstable_mergeable_state_blocked(self) -> None:
        result = evaluate_merge_gate(
            _proposal(),
            _snapshot(mergeable_state="behind"),
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.failed_step, "mergeable")
        self.assertIn("behind", result.reason)

    def test_mergeable_false_blocked(self) -> None:
        result = evaluate_merge_gate(
            _proposal(),
            _snapshot(mergeable=False),
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.failed_step, "mergeable")

    def test_failing_check_run_blocked(self) -> None:
        result = evaluate_merge_gate(
            _proposal(),
            _snapshot(check_conclusions=("success", "failure", "success")),
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.failed_step, "checks_green")
        self.assertIn("failure", result.checks_summary)

    def test_empty_check_runs_blocked(self) -> None:
        result = evaluate_merge_gate(
            _proposal(), _snapshot(check_conclusions=())
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.failed_step, "checks_green")

    def test_skipped_and_neutral_count_as_green(self) -> None:
        result = evaluate_merge_gate(
            _proposal(),
            _snapshot(check_conclusions=("success", "neutral", "skipped")),
        )
        self.assertTrue(result.allowed)

    def test_missing_branch_protection_blocked(self) -> None:
        # 401/403 → branch_protection_available=False → refuse.
        result = evaluate_merge_gate(
            _proposal(),
            _snapshot(branch_protection_available=False),
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.failed_step, "branch_protection")
        self.assertIn("권한", result.reason)

    def test_insufficient_approving_reviews_blocked(self) -> None:
        result = evaluate_merge_gate(
            _proposal(),
            _snapshot(required_approving_reviews=2, actual_approving_reviews=1),
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.failed_step, "branch_protection")

    def test_sha_race_blocked(self) -> None:
        result = evaluate_merge_gate(
            _proposal(head_sha="abc1234567"),
            _snapshot(head_sha="def9876543"),
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.failed_step, "sha_race")
        self.assertIn("abc1234", result.reason)
        self.assertIn("def9876", result.reason)


class ProposalRoundTripTests(unittest.TestCase):
    def test_to_extra_and_from_extra(self) -> None:
        original = _proposal()
        round_tripped = PRMergeProposal.from_extra(dict(original.to_extra()))
        self.assertEqual(round_tripped.repo, original.repo)
        self.assertEqual(round_tripped.pr_number, original.pr_number)
        self.assertEqual(round_tripped.head_sha, original.head_sha)
        self.assertEqual(round_tripped.scope_labels, original.scope_labels)
        self.assertEqual(round_tripped.risk, original.risk)
        self.assertEqual(round_tripped.draft, original.draft)


class AuditEntryTests(unittest.TestCase):
    def test_stage_recorded(self) -> None:
        entry = make_audit_entry("card_posted", pr_number=127, sha="abc")
        self.assertEqual(entry["stage"], "card_posted")
        self.assertEqual(entry["pr_number"], 127)
        self.assertEqual(entry["sha"], "abc")

    def test_merge_failed_entry(self) -> None:
        entry = make_audit_entry(
            "merge_failed", reason="409 Conflict", attempted_method="squash"
        )
        self.assertEqual(entry["stage"], "merge_failed")
        self.assertIn("reason", entry)


if __name__ == "__main__":
    unittest.main()
