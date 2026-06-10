"""P0-H stage 2 commit 5 — CodingHandoffPacket builder unit tests."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.coding.handoff_packet import (
    CodingHandoffPacket,
    NEXT_ANALYZE_COMMIT,
    NEXT_ANALYZE_COMPARE,
    NEXT_ANALYZE_PR,
    NEXT_ASK_USER,
    NEXT_CONTINUE_EXISTING,
    NEXT_OPEN_ISSUE,
    NEXT_OPEN_PR_BRANCH,
    TRACKING_BRANCH,
    TRACKING_COMMIT,
    TRACKING_COMPARE,
    TRACKING_ISSUE,
    TRACKING_PR,
    TRACKING_REPO_ROOT,
    TRACKING_STANDALONE,
    build_coding_handoff_packet,
)
from yule_vcs.github_url import parse_github_target
from yule_vcs.repo_contract import RepoContract


class TrackingModeDerivationTests(unittest.TestCase):
    def test_issue_url_yields_tracking_issue(self) -> None:
        target = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/issues/140"
        )
        packet = build_coding_handoff_packet(
            canonical_request="issue 140 작업해줘", github_target=target
        )
        self.assertEqual(packet.tracking_mode, TRACKING_ISSUE)
        self.assertEqual(packet.next_action, NEXT_OPEN_PR_BRANCH)

    def test_pr_url_yields_analyze_pr(self) -> None:
        target = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/pull/142"
        )
        packet = build_coding_handoff_packet(
            canonical_request="이 PR 검토", github_target=target
        )
        self.assertEqual(packet.tracking_mode, TRACKING_PR)
        self.assertEqual(packet.next_action, NEXT_ANALYZE_PR)

    def test_commit_url_yields_analyze_commit(self) -> None:
        target = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/commit/a4e8507"
        )
        packet = build_coding_handoff_packet(
            canonical_request="이 커밋 봐", github_target=target
        )
        self.assertEqual(packet.tracking_mode, TRACKING_COMMIT)
        self.assertEqual(packet.next_action, NEXT_ANALYZE_COMMIT)

    def test_compare_url_yields_analyze_compare(self) -> None:
        target = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/compare/main...feature/x"
        )
        packet = build_coding_handoff_packet(
            canonical_request="diff 검토", github_target=target
        )
        self.assertEqual(packet.tracking_mode, TRACKING_COMPARE)
        self.assertEqual(packet.next_action, NEXT_ANALYZE_COMPARE)

    def test_tree_url_yields_branch_open_issue(self) -> None:
        target = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/tree/feature/x"
        )
        packet = build_coding_handoff_packet(
            canonical_request="이 브랜치에서 이슈 만들고 시작", github_target=target
        )
        self.assertEqual(packet.tracking_mode, TRACKING_BRANCH)
        self.assertEqual(packet.next_action, NEXT_OPEN_ISSUE)

    def test_repo_root_yields_open_issue(self) -> None:
        target = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent"
        )
        packet = build_coding_handoff_packet(
            canonical_request="이 repo 에 새 작업", github_target=target
        )
        self.assertEqual(packet.tracking_mode, TRACKING_REPO_ROOT)
        self.assertEqual(packet.next_action, NEXT_OPEN_ISSUE)

    def test_no_target_yields_standalone_ask_user(self) -> None:
        packet = build_coding_handoff_packet(canonical_request="그냥 작업해줘")
        self.assertEqual(packet.tracking_mode, TRACKING_STANDALONE)
        self.assertEqual(packet.next_action, NEXT_ASK_USER)


class ExistingSessionMatchTests(unittest.TestCase):
    def test_existing_session_short_circuits_to_continue_existing(self) -> None:
        target = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/issues/140"
        )
        packet = build_coding_handoff_packet(
            canonical_request="이어서 작업",
            github_target=target,
            existing_session_id="sess-abc",
        )
        # Even though target is an issue, the existing session wins.
        self.assertEqual(packet.next_action, NEXT_CONTINUE_EXISTING)
        self.assertEqual(packet.existing_session_match, "sess-abc")

    def test_next_action_override_wins(self) -> None:
        target = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/issues/140"
        )
        packet = build_coding_handoff_packet(
            canonical_request="이거 issue 만 분석",
            github_target=target,
            next_action_override=NEXT_ASK_USER,
        )
        self.assertEqual(packet.next_action, NEXT_ASK_USER)


class RepoContractSummaryTests(unittest.TestCase):
    def test_full_contract_yields_check_summary(self) -> None:
        contract = RepoContract(
            owner="foo",
            repo="bar",
            pr_templates=(".github/PULL_REQUEST_TEMPLATE.md",),
            contributing="CONTRIBUTING.md",
            codeowners="CODEOWNERS",
            workflows=(".github/workflows/ci.yml",),
            backend="local_clone",
        )
        packet = build_coding_handoff_packet(
            canonical_request="작업",
            repo_contract=contract,
        )
        assert packet.repo_contract_summary is not None
        self.assertIn("✅ foo/bar", packet.repo_contract_summary)
        self.assertIn("pr_templates=1", packet.repo_contract_summary)
        self.assertIn("workflows=1", packet.repo_contract_summary)

    def test_fallback_contract_yields_warning(self) -> None:
        contract = RepoContract(
            owner="foo",
            repo="bar",
            fallback=True,
            failure_mode="no_backend",
        )
        packet = build_coding_handoff_packet(
            canonical_request="작업",
            repo_contract=contract,
        )
        assert packet.repo_contract_summary is not None
        self.assertIn("⚠️", packet.repo_contract_summary)
        self.assertIn("no_backend", packet.repo_contract_summary)

    def test_accepts_dict_form_for_contract(self) -> None:
        # When the caller already serialized RepoContract to dict.
        contract_dict = {
            "owner": "foo",
            "repo": "bar",
            "pr_templates": [".github/PULL_REQUEST_TEMPLATE.md"],
            "backend": "gh_cli",
        }
        packet = build_coding_handoff_packet(
            canonical_request="작업",
            repo_contract=contract_dict,
        )
        assert packet.repo_contract_summary is not None
        self.assertIn("✅ foo/bar", packet.repo_contract_summary)


class ModeFieldsTests(unittest.TestCase):
    def test_mode_topology_scope_round_through(self) -> None:
        packet = build_coding_handoff_packet(
            canonical_request="작업",
            work_mode="autonomous_merge",
            topology="multi_repo",
            scope="cross_repo_program",
        )
        self.assertEqual(packet.mode, "autonomous_merge")
        self.assertEqual(packet.topology, "multi_repo")
        self.assertEqual(packet.scope_mode, "cross_repo_program")


class RoundTripTests(unittest.TestCase):
    def test_to_dict_from_dict_full_round_trip(self) -> None:
        target = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/issues/140"
        )
        contract = RepoContract(
            owner="yule-studio",
            repo="yule-studio-agent",
            pr_templates=(".github/PULL_REQUEST_TEMPLATE.md",),
            backend="local_clone",
        )
        packet = build_coding_handoff_packet(
            canonical_request="P0-H stage 2 작업해줘",
            github_target=target,
            repo_contract=contract,
            work_mode="autonomous_merge",
            topology="single_repo",
            scope="single_scope",
        )
        payload = packet.to_dict()
        restored = CodingHandoffPacket.from_dict(payload)
        self.assertEqual(restored.canonical_request, packet.canonical_request)
        self.assertEqual(restored.tracking_mode, packet.tracking_mode)
        self.assertEqual(restored.next_action, packet.next_action)
        self.assertEqual(restored.mode, packet.mode)
        assert restored.github_target is not None
        self.assertEqual(restored.github_target["owner"], "yule-studio")

    def test_summary_line_includes_key_fields(self) -> None:
        target = parse_github_target(
            "https://github.com/yule-studio/yule-studio-agent/pull/142"
        )
        packet = build_coding_handoff_packet(
            canonical_request="PR 검토",
            github_target=target,
            work_mode="approval_required",
            topology="single_repo",
            scope="single_scope",
        )
        line = packet.summary_line()
        self.assertIn("yule-studio/yule-studio-agent#142", line)
        self.assertIn("mode=approval_required", line)
        self.assertIn(NEXT_ANALYZE_PR, line)


if __name__ == "__main__":
    unittest.main()
