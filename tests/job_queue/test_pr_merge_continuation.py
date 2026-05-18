"""P1-L — post-PR work_mode continuation.

draft PR open 직후 session.extra 의 ``work_mode`` 에 따라 다음 액션이
정확히 분기되어야 한다:

  * autonomous_merge  → ``AUTONOMOUS_MERGE`` action + pr_merge_pending stage
  * approval_required → ``APPROVAL_REQUIRED`` action + pr_merge_pending stage
  * 미설정             → default(approval_required) 로 fallback (자동 머지 사고 방지)
  * dry_run / PR 메타 누락 → SKIP (stage 안 찍힘)

또한 ``advance_stage`` / ``is_pending_*`` helper 가 background 루프가
정확히 pick 할 row 만 골라내는지 가드.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.job_queue.pr_merge_continuation import (
    EXTRA_PR_MERGE_AUDIT,
    EXTRA_PR_MERGE_BASE_BRANCH,
    EXTRA_PR_MERGE_DECIDED_AT,
    EXTRA_PR_MERGE_HEAD_SHA,
    EXTRA_PR_MERGE_PR_NUMBER,
    EXTRA_PR_MERGE_PR_URL,
    EXTRA_PR_MERGE_REASON,
    EXTRA_PR_MERGE_REPO,
    EXTRA_PR_MERGE_STAGE,
    PR_MERGE_STAGES,
    PostPRAction,
    STAGE_PR_MERGE_APPROVED,
    STAGE_PR_MERGE_BLOCKED,
    STAGE_PR_MERGE_PENDING,
    STAGE_PR_MERGED,
    advance_stage,
    decide_post_pr_action,
    is_pending_approval_card,
    is_pending_autonomous_merge,
    is_pending_continuation,
    resolve_work_mode,
)
from yule_orchestrator.agents.lifecycle.session_mode import (
    EXTRA_WORK_MODE,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
    WORK_MODE_DEFAULT,
)


# ---------------------------------------------------------------------------
# 1. work_mode 분기 — autonomous_merge
# ---------------------------------------------------------------------------


class AutonomousMergeBranchTests(unittest.TestCase):
    """work_mode=autonomous_merge 면 머지 루프가 pick 할 stage 를 stamp."""

    def test_autonomous_merge_routes_to_autonomous_merge_action(self) -> None:
        extra = {EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS}
        decision = decide_post_pr_action(
            session_id="s1",
            session_extra=extra,
            repo_full_name="yule-studio/naver-search-clone",
            pr_number=2,
            pr_url="https://github.com/yule-studio/naver-search-clone/pull/2",
            head_sha="abc1234",
            base_branch="main",
        )
        self.assertEqual(decision.action, PostPRAction.AUTONOMOUS_MERGE)
        self.assertEqual(decision.reason, "draft_pr_opened:autonomous_merge")
        self.assertEqual(
            decision.extra_updates[EXTRA_PR_MERGE_STAGE],
            STAGE_PR_MERGE_PENDING,
        )
        self.assertEqual(decision.extra_updates[EXTRA_PR_MERGE_PR_NUMBER], 2)
        self.assertEqual(
            decision.extra_updates[EXTRA_PR_MERGE_REPO],
            "yule-studio/naver-search-clone",
        )
        self.assertEqual(decision.extra_updates[EXTRA_PR_MERGE_HEAD_SHA], "abc1234")
        self.assertEqual(decision.extra_updates[EXTRA_PR_MERGE_BASE_BRANCH], "main")
        self.assertIn("draft_pr_opened", decision.extra_updates[EXTRA_PR_MERGE_REASON])

    def test_is_pending_autonomous_merge_picks_only_correct_row(self) -> None:
        """background 머지 루프가 pick 할 row — autonomous_merge + pending."""

        autonomous_pending = {
            EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
        }
        approval_pending = {
            EXTRA_WORK_MODE: WORK_MODE_APPROVAL,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
        }
        already_merged = {
            EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGED,
        }
        self.assertTrue(is_pending_autonomous_merge(autonomous_pending))
        self.assertFalse(is_pending_autonomous_merge(approval_pending))
        self.assertFalse(is_pending_autonomous_merge(already_merged))


# ---------------------------------------------------------------------------
# 2. work_mode 분기 — approval_required
# ---------------------------------------------------------------------------


class ApprovalRequiredBranchTests(unittest.TestCase):
    def test_approval_required_routes_to_approval_action(self) -> None:
        extra = {EXTRA_WORK_MODE: WORK_MODE_APPROVAL}
        decision = decide_post_pr_action(
            session_id="s2",
            session_extra=extra,
            repo_full_name="yule-studio/repo",
            pr_number=10,
            pr_url="https://github.com/yule-studio/repo/pull/10",
            head_sha="def5678",
            base_branch="main",
        )
        self.assertEqual(decision.action, PostPRAction.APPROVAL_REQUIRED)
        self.assertEqual(
            decision.extra_updates[EXTRA_PR_MERGE_STAGE],
            STAGE_PR_MERGE_PENDING,
        )
        self.assertEqual(decision.reason, "draft_pr_opened:approval_required")

    def test_is_pending_approval_card_skips_after_card_enqueued(self) -> None:
        """approval card 한 번 enqueue 되면 같은 row 를 다시 pick 하지 않음.

        background producer 가 중복 카드를 올리지 않도록 audit event 를
        가드 — head_sha 가 바뀌면 새 카드 사이클이 새 stage 로 들어옴.
        """

        before_card = {
            EXTRA_WORK_MODE: WORK_MODE_APPROVAL,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
        }
        after_card = dict(before_card)
        after_card[EXTRA_PR_MERGE_AUDIT] = [
            {"event": "approval_card_enqueued", "approval_job_id": "j-1"}
        ]
        self.assertTrue(is_pending_approval_card(before_card))
        self.assertFalse(is_pending_approval_card(after_card))


# ---------------------------------------------------------------------------
# 3. work_mode 미설정 — default 로 fallback
# ---------------------------------------------------------------------------


class DefaultFallbackTests(unittest.TestCase):
    def test_missing_work_mode_defaults_to_approval_required(self) -> None:
        """work_mode 가 비어있으면 자동 머지로 빠지지 않고 approval 로 fallback.

        autonomy-policy §0.4 에 따라 안전측 default 는 approval_required.
        """

        self.assertEqual(WORK_MODE_DEFAULT, WORK_MODE_APPROVAL)
        decision = decide_post_pr_action(
            session_id="s3",
            session_extra={},  # work_mode 없음
            repo_full_name="yule-studio/repo",
            pr_number=11,
            pr_url="https://github.com/yule-studio/repo/pull/11",
            head_sha="aaa",
            base_branch="main",
        )
        self.assertEqual(decision.action, PostPRAction.APPROVAL_REQUIRED)

    def test_unknown_work_mode_value_is_ignored(self) -> None:
        decision = decide_post_pr_action(
            session_id="s4",
            session_extra={EXTRA_WORK_MODE: "not_a_real_mode"},
            repo_full_name="r",
            pr_number=1,
            pr_url="https://github.com/r/pull/1",
            head_sha="x",
        )
        self.assertEqual(decision.action, PostPRAction.APPROVAL_REQUIRED)

    def test_resolve_work_mode_helper(self) -> None:
        self.assertEqual(resolve_work_mode({}), WORK_MODE_APPROVAL)
        self.assertEqual(
            resolve_work_mode({EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS}),
            WORK_MODE_AUTONOMOUS,
        )
        self.assertEqual(
            resolve_work_mode({EXTRA_WORK_MODE: WORK_MODE_APPROVAL}),
            WORK_MODE_APPROVAL,
        )
        self.assertEqual(
            resolve_work_mode({EXTRA_WORK_MODE: "garbage"}), WORK_MODE_APPROVAL
        )
        self.assertEqual(resolve_work_mode(None), WORK_MODE_APPROVAL)


# ---------------------------------------------------------------------------
# 4. SKIP 경로 — dry_run / PR 메타 누락
# ---------------------------------------------------------------------------


class SkipPathTests(unittest.TestCase):
    def test_dry_run_skips_continuation(self) -> None:
        decision = decide_post_pr_action(
            session_id="s5",
            session_extra={EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS},
            repo_full_name="r",
            pr_number=1,
            pr_url="https://github.com/r/pull/1",
            head_sha="x",
            dry_run=True,
        )
        self.assertEqual(decision.action, PostPRAction.SKIP)
        self.assertEqual(decision.reason, "dry_run")
        self.assertEqual(dict(decision.extra_updates), {})

    def test_missing_pr_number_skips(self) -> None:
        decision = decide_post_pr_action(
            session_id="s6",
            session_extra={EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS},
            repo_full_name="r",
            pr_number=None,
            pr_url="https://github.com/r/pull/1",
            head_sha="x",
        )
        self.assertEqual(decision.action, PostPRAction.SKIP)
        self.assertEqual(decision.reason, "missing_pr_metadata")

    def test_missing_repo_full_name_skips(self) -> None:
        decision = decide_post_pr_action(
            session_id="s7",
            session_extra={EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS},
            repo_full_name=None,
            pr_number=1,
            pr_url="https://github.com/r/pull/1",
            head_sha="x",
        )
        self.assertEqual(decision.action, PostPRAction.SKIP)


# ---------------------------------------------------------------------------
# 5. advance_stage — background 루프가 stage 를 정확히 진행
# ---------------------------------------------------------------------------


class AdvanceStageTests(unittest.TestCase):
    def test_advance_to_merged_records_prior_stage_and_audit(self) -> None:
        before = {
            EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
            EXTRA_PR_MERGE_PR_NUMBER: 2,
            EXTRA_PR_MERGE_REPO: "yule-studio/naver-search-clone",
        }
        after = advance_stage(
            before,
            new_stage=STAGE_PR_MERGED,
            reason="gate_passed_and_merged",
            merge_sha="merge1234",
            method="squash",
        )
        self.assertEqual(after[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGED)
        self.assertEqual(after[EXTRA_PR_MERGE_REASON], "gate_passed_and_merged")
        audit = after[EXTRA_PR_MERGE_AUDIT]
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["stage"], STAGE_PR_MERGED)
        self.assertEqual(audit[0]["prior_stage"], STAGE_PR_MERGE_PENDING)
        self.assertEqual(audit[0]["merge_sha"], "merge1234")
        self.assertEqual(audit[0]["method"], "squash")
        self.assertIn("at", audit[0])
        # 원본 dict 는 mutate 되지 않음
        self.assertEqual(before[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGE_PENDING)
        self.assertNotIn(EXTRA_PR_MERGE_AUDIT, before)

    def test_advance_to_blocked_includes_gate_failed_step(self) -> None:
        before = {
            EXTRA_WORK_MODE: WORK_MODE_AUTONOMOUS,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
        }
        after = advance_stage(
            before,
            new_stage=STAGE_PR_MERGE_BLOCKED,
            reason="gate_failed:checks_green",
            gate_failed_step="checks_green",
            gate_reason="2 failing checks",
        )
        self.assertEqual(after[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGE_BLOCKED)
        audit_entry = after[EXTRA_PR_MERGE_AUDIT][0]
        self.assertEqual(audit_entry["gate_failed_step"], "checks_green")
        self.assertEqual(audit_entry["gate_reason"], "2 failing checks")

    def test_advance_to_approved_stage(self) -> None:
        before = {
            EXTRA_WORK_MODE: WORK_MODE_APPROVAL,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
        }
        after = advance_stage(
            before,
            new_stage=STAGE_PR_MERGE_APPROVED,
            reason="user_approved",
            approved_by="codwithyc",
        )
        self.assertEqual(after[EXTRA_PR_MERGE_STAGE], STAGE_PR_MERGE_APPROVED)
        self.assertEqual(
            after[EXTRA_PR_MERGE_AUDIT][0]["approved_by"], "codwithyc"
        )

    def test_advance_to_unknown_stage_raises(self) -> None:
        with self.assertRaises(ValueError):
            advance_stage(
                {EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING},
                new_stage="some_random_value",
                reason="bad",
            )

    def test_advance_accumulates_audit_across_transitions(self) -> None:
        extra = {
            EXTRA_WORK_MODE: WORK_MODE_APPROVAL,
            EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING,
        }
        extra = advance_stage(
            extra,
            new_stage=STAGE_PR_MERGE_APPROVED,
            reason="user_approved",
        )
        extra = advance_stage(
            extra,
            new_stage=STAGE_PR_MERGED,
            reason="merged_after_approval",
            merge_sha="m1",
        )
        audit = extra[EXTRA_PR_MERGE_AUDIT]
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[0]["stage"], STAGE_PR_MERGE_APPROVED)
        self.assertEqual(audit[1]["stage"], STAGE_PR_MERGED)
        self.assertEqual(audit[1]["prior_stage"], STAGE_PR_MERGE_APPROVED)


# ---------------------------------------------------------------------------
# 6. stage vocabulary surface
# ---------------------------------------------------------------------------


class StageVocabularyTests(unittest.TestCase):
    def test_all_four_stages_exposed(self) -> None:
        self.assertEqual(
            set(PR_MERGE_STAGES),
            {
                STAGE_PR_MERGE_PENDING,
                STAGE_PR_MERGE_APPROVED,
                STAGE_PR_MERGED,
                STAGE_PR_MERGE_BLOCKED,
            },
        )

    def test_pending_helper_includes_blocked_false(self) -> None:
        self.assertTrue(
            is_pending_continuation({EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_PENDING})
        )
        self.assertFalse(
            is_pending_continuation({EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGED})
        )
        self.assertFalse(
            is_pending_continuation({EXTRA_PR_MERGE_STAGE: STAGE_PR_MERGE_BLOCKED})
        )
        self.assertFalse(is_pending_continuation({}))


if __name__ == "__main__":
    unittest.main()
