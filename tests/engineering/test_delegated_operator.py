"""DelegatedOperatorPolicy — self-improvement runtime delegate tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.autonomy_policy import (
    ACTION_AGENT_OPS_RECORD,
    ACTION_BRANCH_MERGE,
    ACTION_DEPLOY,
    ACTION_DRAFT_PR_CREATE,
    ACTION_FEATURE_BRANCH_CREATE,
    ACTION_LOCAL_COMMIT,
    ACTION_MAIN_BRANCH_PUSH,
    ACTION_PUSH_TO_SHARED_REPO,
    ACTION_SECRET_MODIFY,
    ACTION_TEST_EXECUTE,
    AutonomyLevel,
)
from yule_orchestrator.agents.lifecycle.delegated_operator import (
    DEFAULT_GLOBAL_DAILY_CAP,
    DEFAULT_RETRY_CAP,
    DelegatedDecision,
    DelegatedRateLedger,
    ESCALATE_BILLING_PURCHASE,
    ESCALATE_DEPLOY,
    ESCALATE_DESTRUCTIVE_CLEANUP,
    ESCALATE_MERGE,
    ESCALATE_OUT_OF_DELEGATED_SCOPE,
    ESCALATE_PROTECTED_BRANCH_WRITE,
    ESCALATE_RETRY_CAP_EXCEEDED,
    ESCALATE_SECRET_MODIFY,
    evaluate_delegated_approval,
    is_scope_delegated,
    list_delegated_scopes,
)


_FIXED_NOW = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)


class DelegatedScopeWhitelistTests(unittest.TestCase):
    """auto-approve OK 화이트리스트 액션은 delegated=True 가 나와야 한다."""

    def test_local_commit_is_delegated(self) -> None:
        ledger = DelegatedRateLedger()
        decision = evaluate_delegated_approval(
            action=ACTION_LOCAL_COMMIT,
            problem_signature="sig.demo",
            rate_ledger=ledger,
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertTrue(decision.delegated)
        self.assertEqual(decision.retry_count, 1)
        self.assertIsNone(decision.escalation_reason)
        self.assertIsNotNone(decision.scope)

    def test_feature_branch_create_is_delegated(self) -> None:
        ledger = DelegatedRateLedger()
        decision = evaluate_delegated_approval(
            action=ACTION_FEATURE_BRANCH_CREATE,
            problem_signature="sig.feature",
            rate_ledger=ledger,
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertTrue(decision.delegated)

    def test_draft_pr_create_delegated_in_self_improve_scope(self) -> None:
        ledger = DelegatedRateLedger()
        decision = evaluate_delegated_approval(
            action=ACTION_DRAFT_PR_CREATE,
            problem_signature="sig.draft-pr",
            rate_ledger=ledger,
            now_fn=lambda: _FIXED_NOW,
        )
        # draft PR creation is delegated despite default L3 — the
        # self-improvement scope explicitly allows it.
        self.assertTrue(decision.delegated)

    def test_test_execute_delegated(self) -> None:
        ledger = DelegatedRateLedger()
        decision = evaluate_delegated_approval(
            action=ACTION_TEST_EXECUTE,
            problem_signature="sig.test",
            rate_ledger=ledger,
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertTrue(decision.delegated)

    def test_agent_ops_record_delegated(self) -> None:
        ledger = DelegatedRateLedger()
        decision = evaluate_delegated_approval(
            action=ACTION_AGENT_OPS_RECORD,
            problem_signature="sig.audit",
            rate_ledger=ledger,
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertTrue(decision.delegated)


class DelegatedPermanentEscalationsTests(unittest.TestCase):
    """L4 / 위험 action 은 어떤 경우에도 delegated 가 되지 않아야 한다.

    중요: 이건 사용자가 명시한 'auto-merge 절대 금지' 의 코드측 강제.
    """

    def test_branch_merge_never_delegated(self) -> None:
        ledger = DelegatedRateLedger()
        decision = evaluate_delegated_approval(
            action=ACTION_BRANCH_MERGE,
            problem_signature="sig.merge-attempt",
            rate_ledger=ledger,
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertFalse(decision.delegated)
        self.assertEqual(decision.escalation_reason, ESCALATE_MERGE)

    def test_main_branch_push_never_delegated(self) -> None:
        decision = evaluate_delegated_approval(
            action=ACTION_MAIN_BRANCH_PUSH,
            problem_signature="sig.main",
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertFalse(decision.delegated)
        self.assertEqual(decision.escalation_reason, ESCALATE_PROTECTED_BRANCH_WRITE)

    def test_secret_modify_never_delegated(self) -> None:
        decision = evaluate_delegated_approval(
            action=ACTION_SECRET_MODIFY,
            problem_signature="sig.secret",
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertFalse(decision.delegated)
        self.assertEqual(decision.escalation_reason, ESCALATE_SECRET_MODIFY)

    def test_deploy_never_delegated(self) -> None:
        decision = evaluate_delegated_approval(
            action=ACTION_DEPLOY,
            problem_signature="sig.deploy",
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertFalse(decision.delegated)
        self.assertEqual(decision.escalation_reason, ESCALATE_DEPLOY)

    def test_l4_autonomy_level_short_circuits(self) -> None:
        # 액션 자체는 delegate 화이트리스트지만 caller 가 L4 로 escalate 했다면
        # 따라가야 한다.
        decision = evaluate_delegated_approval(
            action=ACTION_LOCAL_COMMIT,
            autonomy_level=AutonomyLevel.L4_STRONG_APPROVAL_OR_FORBIDDEN,
            problem_signature="sig.l4-override",
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertFalse(decision.delegated)
        self.assertEqual(
            decision.escalation_reason, ESCALATE_OUT_OF_DELEGATED_SCOPE
        )


class DelegatedBranchProtectionTests(unittest.TestCase):
    """push_to_shared_repo 는 branch 가 protected 면 escalate."""

    def test_push_to_main_escalates(self) -> None:
        decision = evaluate_delegated_approval(
            action=ACTION_PUSH_TO_SHARED_REPO,
            problem_signature="sig.push",
            branch_hint="main",
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertFalse(decision.delegated)
        self.assertEqual(
            decision.escalation_reason, ESCALATE_PROTECTED_BRANCH_WRITE
        )

    def test_push_to_release_branch_escalates(self) -> None:
        decision = evaluate_delegated_approval(
            action=ACTION_PUSH_TO_SHARED_REPO,
            problem_signature="sig.push",
            branch_hint="release/2026.05",
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertFalse(decision.delegated)
        self.assertEqual(
            decision.escalation_reason, ESCALATE_PROTECTED_BRANCH_WRITE
        )

    def test_push_to_self_improve_branch_delegated(self) -> None:
        decision = evaluate_delegated_approval(
            action=ACTION_PUSH_TO_SHARED_REPO,
            problem_signature="sig.push",
            branch_hint="codex/self-improve/sig-demo",
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertTrue(decision.delegated)


class DelegatedRateLimitTests(unittest.TestCase):
    """retry cap 을 넘으면 자동 escalate."""

    def test_retry_cap_escalates(self) -> None:
        ledger = DelegatedRateLedger()
        sig = "sig.retry-cap"
        for _ in range(DEFAULT_RETRY_CAP):
            d = evaluate_delegated_approval(
                action=ACTION_LOCAL_COMMIT,
                problem_signature=sig,
                rate_ledger=ledger,
                now_fn=lambda: _FIXED_NOW,
            )
            self.assertTrue(d.delegated)
        # The next call exceeds cap.
        d = evaluate_delegated_approval(
            action=ACTION_LOCAL_COMMIT,
            problem_signature=sig,
            rate_ledger=ledger,
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertFalse(d.delegated)
        self.assertEqual(d.escalation_reason, ESCALATE_RETRY_CAP_EXCEEDED)

    def test_unknown_action_escalates(self) -> None:
        decision = evaluate_delegated_approval(
            action="brand_new_unknown_action",
            problem_signature="sig.unknown",
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertFalse(decision.delegated)
        self.assertEqual(
            decision.escalation_reason, ESCALATE_OUT_OF_DELEGATED_SCOPE
        )

    def test_daily_cap_escalates(self) -> None:
        ledger = DelegatedRateLedger()
        # Fast-forward the global counter past the cap so the next
        # call escalates.
        ledger.day_anchor = _FIXED_NOW.date().isoformat()
        ledger.delegated_count_today = DEFAULT_GLOBAL_DAILY_CAP
        decision = evaluate_delegated_approval(
            action=ACTION_LOCAL_COMMIT,
            problem_signature="sig.daily-cap",
            rate_ledger=ledger,
            now_fn=lambda: _FIXED_NOW,
        )
        self.assertFalse(decision.delegated)
        self.assertEqual(
            decision.escalation_reason, ESCALATE_RETRY_CAP_EXCEEDED
        )


class StaticHelpersTests(unittest.TestCase):
    def test_is_scope_delegated_returns_true_for_known_action(self) -> None:
        self.assertTrue(is_scope_delegated(ACTION_LOCAL_COMMIT))

    def test_is_scope_delegated_returns_false_for_permanent_escalation(self) -> None:
        self.assertFalse(is_scope_delegated(ACTION_BRANCH_MERGE))
        self.assertFalse(is_scope_delegated(ACTION_MAIN_BRANCH_PUSH))
        self.assertFalse(is_scope_delegated(ACTION_SECRET_MODIFY))

    def test_list_delegated_scopes_includes_drafts_and_branches(self) -> None:
        scopes = list_delegated_scopes()
        self.assertIn("feature_branch_create", scopes)
        self.assertIn("draft_pr_create", scopes)
        self.assertIn("local_commit", scopes)


if __name__ == "__main__":
    unittest.main()
