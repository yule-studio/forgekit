"""P0-I stage 3 commit 2 — tracking enforcement unit tests.

Covers the chain rules in stage-1 ``github-workflow.md §1~§5`` +
``autonomy-policy.md §0``:

  * No GitHub target → standalone, not blocked.
  * Repo-root without RepoContract exception → blocked (needs_issue).
  * Repo-root with trunk-based RepoContract → allowed via exception.
  * Issue target without branch → blocked (needs_branch).
  * Issue + branch → ok, next=open_pr_branch.
  * PR target → ok.
  * No work_mode → blocked.
  * approval_required mode without handoff packet → blocked.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.coding.tracking_enforcement import (
    STATUS_NEEDS_BRANCH,
    STATUS_NEEDS_HANDOFF_PACKET,
    STATUS_NEEDS_ISSUE,
    STATUS_NEEDS_MODE,
    STATUS_OK,
    STATUS_STANDALONE_NO_TARGET,
    TrackingValidation,
    validate_tracking_chain,
)


def _extra_with(**overrides):
    base = {
        "work_mode": "approval_required",
        "topology": "single_repo",
        "scope": "single_scope",
        "coding_handoff_packet": {"kind": "stub"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Standalone — no GitHub target
# ---------------------------------------------------------------------------


class StandaloneTargetTests(unittest.TestCase):
    def test_empty_extra_not_blocked(self) -> None:
        v = validate_tracking_chain({})
        self.assertEqual(v.status, STATUS_STANDALONE_NO_TARGET)
        self.assertFalse(v.blocked)
        self.assertEqual(v.next_action, "proceed")

    def test_no_github_target_not_blocked_when_mode_set(self) -> None:
        v = validate_tracking_chain({"work_mode": "approval_required"})
        self.assertEqual(v.status, STATUS_STANDALONE_NO_TARGET)
        self.assertFalse(v.blocked)
        self.assertTrue(v.has_mode)
        self.assertFalse(v.has_github_target)

    def test_invalid_extra_not_blocked(self) -> None:
        # Non-mapping input — defensive path.
        v = validate_tracking_chain(None)  # type: ignore[arg-type]
        self.assertEqual(v.status, STATUS_STANDALONE_NO_TARGET)
        self.assertFalse(v.blocked)


# ---------------------------------------------------------------------------
# Mode requirement
# ---------------------------------------------------------------------------


class ModeRequirementTests(unittest.TestCase):
    def test_no_work_mode_blocked(self) -> None:
        extra = {
            "github_target": {
                "kind": "pull_request",
                "owner": "foo",
                "repo": "bar",
                "number": 5,
            }
        }
        v = validate_tracking_chain(extra)
        self.assertEqual(v.status, STATUS_NEEDS_MODE)
        self.assertTrue(v.blocked)
        self.assertIn("work_mode", v.missing_links)
        self.assertEqual(v.next_action, "ask_user")


# ---------------------------------------------------------------------------
# Handoff packet requirement (approval_required)
# ---------------------------------------------------------------------------


class HandoffPacketRequirementTests(unittest.TestCase):
    def test_approval_required_without_packet_blocked(self) -> None:
        extra = {
            "work_mode": "approval_required",
            "github_target": {
                "kind": "pull_request",
                "owner": "foo",
                "repo": "bar",
                "number": 5,
            },
        }
        v = validate_tracking_chain(extra)
        self.assertEqual(v.status, STATUS_NEEDS_HANDOFF_PACKET)
        self.assertTrue(v.blocked)
        self.assertIn("coding_handoff_packet", v.missing_links)

    def test_autonomous_merge_without_packet_ok(self) -> None:
        # autonomous_merge mode doesn't require the packet for the
        # reviewer card (there's no reviewer); chain still completes.
        extra = {
            "work_mode": "autonomous_merge",
            "github_target": {
                "kind": "pull_request",
                "owner": "foo",
                "repo": "bar",
                "number": 5,
            },
        }
        v = validate_tracking_chain(extra)
        self.assertEqual(v.status, STATUS_OK)
        self.assertFalse(v.blocked)


# ---------------------------------------------------------------------------
# Repo-root → needs issue (unless contract exception)
# ---------------------------------------------------------------------------


class RepoRootTargetTests(unittest.TestCase):
    def test_repo_root_without_contract_exception_blocked(self) -> None:
        extra = _extra_with(
            github_target={
                "kind": "repo",
                "owner": "yule-studio",
                "repo": "yule-studio-agent",
            }
        )
        v = validate_tracking_chain(extra)
        self.assertEqual(v.status, STATUS_NEEDS_ISSUE)
        self.assertTrue(v.blocked)
        self.assertEqual(v.next_action, "open_issue")
        self.assertFalse(v.allowed_via_contract_exception)

    def test_repo_root_with_trunk_based_contract_allowed(self) -> None:
        extra = _extra_with(
            github_target={
                "kind": "repo",
                "owner": "foo",
                "repo": "bar",
            },
            repo_contract={
                "owner": "foo",
                "repo": "bar",
                "branch_strategy": "trunk-based",
                "fallback": False,
            },
        )
        v = validate_tracking_chain(extra)
        self.assertEqual(v.status, STATUS_OK)
        self.assertFalse(v.blocked)
        self.assertTrue(v.allowed_via_contract_exception)
        self.assertEqual(v.next_action, "open_pr_branch")

    def test_repo_root_with_github_flow_contract_allowed(self) -> None:
        extra = _extra_with(
            github_target={
                "kind": "repo",
                "owner": "foo",
                "repo": "bar",
            },
            repo_contract={
                "owner": "foo",
                "repo": "bar",
                "branch_strategy": "github-flow",
                "fallback": False,
            },
        )
        v = validate_tracking_chain(extra)
        self.assertTrue(v.allowed_via_contract_exception)

    def test_repo_root_with_git_flow_contract_still_blocked(self) -> None:
        # git-flow conventionally requires an issue link before PR.
        extra = _extra_with(
            github_target={
                "kind": "repo",
                "owner": "foo",
                "repo": "bar",
            },
            repo_contract={
                "owner": "foo",
                "repo": "bar",
                "branch_strategy": "git-flow",
                "fallback": False,
            },
        )
        v = validate_tracking_chain(extra)
        self.assertEqual(v.status, STATUS_NEEDS_ISSUE)
        self.assertTrue(v.blocked)

    def test_fallback_contract_does_not_grant_exception(self) -> None:
        extra = _extra_with(
            github_target={
                "kind": "repo",
                "owner": "foo",
                "repo": "bar",
            },
            repo_contract={"owner": "foo", "repo": "bar", "fallback": True},
        )
        v = validate_tracking_chain(extra)
        self.assertFalse(v.allowed_via_contract_exception)
        self.assertEqual(v.status, STATUS_NEEDS_ISSUE)


# ---------------------------------------------------------------------------
# Issue → needs branch
# ---------------------------------------------------------------------------


class IssueTargetTests(unittest.TestCase):
    def test_issue_without_branch_blocked(self) -> None:
        extra = _extra_with(
            github_target={
                "kind": "issue",
                "owner": "foo",
                "repo": "bar",
                "number": 1,
            }
        )
        v = validate_tracking_chain(extra)
        self.assertEqual(v.status, STATUS_NEEDS_BRANCH)
        self.assertTrue(v.blocked)
        self.assertEqual(v.next_action, "open_pr_branch")

    def test_issue_with_branch_ok(self) -> None:
        extra = _extra_with(
            github_target={
                "kind": "issue",
                "owner": "foo",
                "repo": "bar",
                "number": 1,
            },
            branch_name="feature/p0i",
        )
        v = validate_tracking_chain(extra)
        self.assertEqual(v.status, STATUS_OK)
        self.assertFalse(v.blocked)
        self.assertTrue(v.has_branch)


# ---------------------------------------------------------------------------
# PR target → ok
# ---------------------------------------------------------------------------


class PullRequestTargetTests(unittest.TestCase):
    def test_pr_target_ok(self) -> None:
        extra = _extra_with(
            github_target={
                "kind": "pull_request",
                "owner": "foo",
                "repo": "bar",
                "number": 5,
            }
        )
        v = validate_tracking_chain(extra)
        self.assertEqual(v.status, STATUS_OK)
        self.assertFalse(v.blocked)
        self.assertTrue(v.has_pull_request)
        # PR implies a branch (head ref).
        self.assertTrue(v.has_branch)


# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------


class SummaryLineTests(unittest.TestCase):
    def test_ok_line(self) -> None:
        v = TrackingValidation(status=STATUS_OK, blocked=False)
        self.assertIn("✅", v.status_summary_line())

    def test_standalone_line(self) -> None:
        v = TrackingValidation(status=STATUS_STANDALONE_NO_TARGET, blocked=False)
        line = v.status_summary_line()
        self.assertIn("ℹ️", line)
        self.assertIn("GitHub target 없음", line)

    def test_blocked_line_lists_missing(self) -> None:
        v = TrackingValidation(
            status=STATUS_NEEDS_ISSUE,
            blocked=True,
            missing_links=("issue",),
        )
        line = v.status_summary_line()
        self.assertIn("⚠️", line)
        self.assertIn("issue", line)

    def test_exception_line_marks_repo_contract(self) -> None:
        v = TrackingValidation(
            status=STATUS_OK,
            blocked=False,
            allowed_via_contract_exception=True,
        )
        self.assertIn("✅", v.status_summary_line())


class RoundTripTests(unittest.TestCase):
    def test_to_dict_includes_all_flags(self) -> None:
        v = TrackingValidation(
            status=STATUS_OK,
            blocked=False,
            has_github_target=True,
            has_issue=True,
            has_branch=True,
        )
        payload = v.to_dict()
        self.assertEqual(payload["status"], STATUS_OK)
        self.assertTrue(payload["has_github_target"])
        self.assertTrue(payload["has_issue"])
        self.assertTrue(payload["has_branch"])
        self.assertFalse(payload["has_pull_request"])


if __name__ == "__main__":
    unittest.main()
