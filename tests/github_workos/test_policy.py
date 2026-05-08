"""Permission model — L0~L4, protected branches, force-push deny."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.github_workos.models import PermissionLevel
from yule_orchestrator.agents.github_workos.policy import (
    ACTION_BRANCH_PLAN,
    ACTION_DEPLOY,
    ACTION_DESTRUCTIVE_DELETE,
    ACTION_DRAFT_PR_PLAN,
    ACTION_FORCE_PUSH,
    ACTION_ISSUE_COMMENT,
    ACTION_MERGE,
    ACTION_PUSH_COMMIT,
    ACTION_READ_CODE,
    ACTION_READ_ISSUE,
    ACTION_READY_PR,
    ACTION_REAL_CODE_WRITE_REQUEST,
    ACTION_RESEARCH_LOG,
    ACTION_SECRET_CHANGE,
    ACTION_TEST_PLAN,
    ACTION_VAULT_GIT_PUSH,
    PROTECTED_BRANCH_NAMES,
    decide_permission,
    permission_level_for_action,
)


class LevelMappingTests(unittest.TestCase):
    def test_l0_actions_are_read_only(self) -> None:
        for action in (
            ACTION_READ_ISSUE,
            ACTION_READ_CODE,
        ):
            self.assertEqual(
                permission_level_for_action(action), PermissionLevel.L0_READ
            )

    def test_l1_actions_are_light_writes(self) -> None:
        for action in (ACTION_ISSUE_COMMENT, ACTION_RESEARCH_LOG):
            self.assertEqual(
                permission_level_for_action(action),
                PermissionLevel.L1_LIGHT_WRITE,
            )

    def test_l2_actions_are_plans(self) -> None:
        for action in (
            ACTION_BRANCH_PLAN,
            ACTION_DRAFT_PR_PLAN,
            ACTION_TEST_PLAN,
        ):
            self.assertEqual(
                permission_level_for_action(action), PermissionLevel.L2_PLAN
            )

    def test_l3_actions_are_real_writes(self) -> None:
        for action in (
            ACTION_PUSH_COMMIT,
            ACTION_READY_PR,
            ACTION_VAULT_GIT_PUSH,
            ACTION_REAL_CODE_WRITE_REQUEST,
        ):
            self.assertEqual(
                permission_level_for_action(action),
                PermissionLevel.L3_REAL_WRITE,
            )

    def test_l4_actions_are_destructive(self) -> None:
        for action in (
            ACTION_MERGE,
            ACTION_DEPLOY,
            ACTION_SECRET_CHANGE,
            ACTION_DESTRUCTIVE_DELETE,
            ACTION_FORCE_PUSH,
        ):
            self.assertEqual(
                permission_level_for_action(action),
                PermissionLevel.L4_DESTRUCTIVE,
            )

    def test_unknown_action_raises(self) -> None:
        with self.assertRaises(KeyError):
            permission_level_for_action("publish_to_npm")


class L0L1L2AllowTests(unittest.TestCase):
    def test_l0_read_always_allowed(self) -> None:
        decision = decide_permission(ACTION_READ_ISSUE)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_approval)
        self.assertEqual(decision.deny_reason, "")
        self.assertEqual(decision.level, PermissionLevel.L0_READ)

    def test_l1_light_write_allowed_without_approval(self) -> None:
        decision = decide_permission(ACTION_ISSUE_COMMENT)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_approval)

    def test_l2_plan_allowed_without_approval(self) -> None:
        decision = decide_permission(ACTION_DRAFT_PR_PLAN)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_approval)


class L3DenyWithoutApprovalTests(unittest.TestCase):
    def test_push_commit_denied_without_approval(self) -> None:
        decision = decide_permission(ACTION_PUSH_COMMIT)
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_approval)
        self.assertIn("explicit operator approval", decision.deny_reason)

    def test_real_code_write_denied_without_approval(self) -> None:
        decision = decide_permission(ACTION_REAL_CODE_WRITE_REQUEST)
        self.assertFalse(decision.allowed)

    def test_push_commit_allowed_with_approval_and_feature_branch(self) -> None:
        decision = decide_permission(
            ACTION_PUSH_COMMIT,
            approval_granted=True,
            target_branch="feat/foo",
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_approval)


class ProtectedBranchTests(unittest.TestCase):
    def test_main_master_prod_release_in_protected_set(self) -> None:
        for name in ("main", "master", "prod", "release"):
            self.assertIn(name, PROTECTED_BRANCH_NAMES)

    def test_push_to_main_denied_even_with_approval(self) -> None:
        decision = decide_permission(
            ACTION_PUSH_COMMIT,
            approval_granted=True,
            target_branch="main",
        )
        self.assertFalse(decision.allowed)
        self.assertIn("protected branch", decision.deny_reason)

    def test_push_to_refs_heads_main_denied(self) -> None:
        decision = decide_permission(
            ACTION_PUSH_COMMIT,
            approval_granted=True,
            target_branch="refs/heads/main",
        )
        self.assertFalse(decision.allowed)

    def test_force_push_to_main_denied_even_with_approval(self) -> None:
        decision = decide_permission(
            ACTION_FORCE_PUSH,
            approval_granted=True,
            target_branch="main",
        )
        self.assertFalse(decision.allowed)
        self.assertIn("force-push", decision.deny_reason)

    def test_force_flag_against_main_denied_for_any_action(self) -> None:
        decision = decide_permission(
            ACTION_PUSH_COMMIT,
            approval_granted=True,
            target_branch="release",
            force=True,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("force flag", decision.deny_reason)


class DestructiveActionTests(unittest.TestCase):
    def test_destructive_delete_denied_without_approval(self) -> None:
        decision = decide_permission(ACTION_DESTRUCTIVE_DELETE)
        self.assertFalse(decision.allowed)
        self.assertIn("destructive delete", decision.deny_reason)

    def test_secret_change_denied_without_approval(self) -> None:
        decision = decide_permission(ACTION_SECRET_CHANGE)
        self.assertFalse(decision.allowed)
        self.assertIn("secret change", decision.deny_reason)

    def test_secret_change_allowed_when_explicitly_approved(self) -> None:
        # Even with approval, the deny_reason was empty — but in
        # practice the operator should still go through a stronger
        # gate. Pin the contract: approval + non-protected branch
        # must allow.
        decision = decide_permission(
            ACTION_SECRET_CHANGE, approval_granted=True
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_approval)


if __name__ == "__main__":
    unittest.main()
