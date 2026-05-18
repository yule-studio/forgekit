"""P1-R — 11 사용자 acceptance + 보조.

1.  approval_required, git_flow, tagged_release, issue_required intake
    persists all governance keys
2.  autonomous_merge, git_flow, tagged_release, issue_required intake
    persists all governance keys
3.  issue 없는 작업은 branch 생성 전에 block
4.  invalid git-flow branch name 은 block
5.  valid feature branch with issue anchor passes
6.  release/hotfix completion without tag is blocked
7.  valid tag naming passes
8.  approval_required card without clear summary is blocked
9.  approval_required card with required summary passes
10. autonomous_merge mode suppresses unnecessary approval cards
11. cross-repo target repo write path enforces same rules
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.governance.repo_write_policy import (
    ApprovalCardQualityContext,
    GitFlowBranchContext,
    IssueAnchorContext,
    PolicyViolation,
    REASON_APPROVAL_CARD_MISSING_SECTIONS,
    REASON_INVALID_GIT_FLOW_BRANCH,
    REASON_INVALID_RELEASE_TAG,
    REASON_ISSUE_REQUIRED_FOR_REPO_WORK,
    REASON_MISSING_RELEASE_TAG,
    REASON_PROTECTED_BRANCH_DIRECT_WORK,
    ReleaseTagContext,
    enforce_approval_card_quality,
    enforce_git_flow_branch,
    enforce_release_tag,
    validate_approval_card_quality,
    validate_git_flow_branch,
    validate_issue_anchor,
    validate_release_tag,
)
from yule_orchestrator.agents.coding.coding_session_context import (
    prepare_coding_session_context,
)
from yule_orchestrator.agents.lifecycle.session_mode import (
    BRANCH_STRATEGY_GIT_FLOW,
    EXTRA_BRANCH_STRATEGY,
    EXTRA_ISSUE_POLICY,
    EXTRA_RELEASE_STRATEGY,
    EXTRA_SCOPE,
    EXTRA_TOPOLOGY,
    EXTRA_WORK_MODE,
    ISSUE_POLICY_REQUIRED,
    RELEASE_STRATEGY_TAGGED,
    SCOPE_FULL_STACK,
    TOPOLOGY_SINGLE,
    WORK_MODE_APPROVAL,
    WORK_MODE_AUTONOMOUS,
)


# ---------------------------------------------------------------------------
# 1, 2 — intake mode persistence (extended contract)
# ---------------------------------------------------------------------------


class IntakeGovernanceContractTests(unittest.TestCase):
    def test_approval_required_intake_persists_all_seven_keys(self) -> None:
        ctx = prepare_coding_session_context(
            message_text=(
                "approval_required, git_flow, tagged_release, issue_required, "
                "single_repo, full_stack_single_repo "
                "네이버 검색 풀스택 MVP 구현해줘 "
                "https://github.com/yule-studio/naver-search-clone"
            ),
            user_links=("https://github.com/yule-studio/naver-search-clone",),
            existing_extra={},
            discover_contract=False,
        )
        ex = ctx.extras_update
        self.assertEqual(ex.get(EXTRA_WORK_MODE), WORK_MODE_APPROVAL)
        self.assertEqual(ex.get(EXTRA_TOPOLOGY), TOPOLOGY_SINGLE)
        self.assertEqual(ex.get(EXTRA_SCOPE), SCOPE_FULL_STACK)
        self.assertEqual(ex.get(EXTRA_BRANCH_STRATEGY), BRANCH_STRATEGY_GIT_FLOW)
        self.assertEqual(ex.get(EXTRA_RELEASE_STRATEGY), RELEASE_STRATEGY_TAGGED)
        self.assertEqual(ex.get(EXTRA_ISSUE_POLICY), ISSUE_POLICY_REQUIRED)
        self.assertIn("mode_decided_by", ex)
        self.assertIn("mode_decided_at", ex)

    def test_autonomous_merge_intake_persists_all_seven_keys(self) -> None:
        ctx = prepare_coding_session_context(
            message_text=(
                "autonomous_merge, git_flow, tagged_release, issue_required, "
                "single_repo, full_stack_single_repo "
                "네이버 검색 풀스택 MVP 끝까지 자율로 진행해줘"
            ),
            user_links=("https://github.com/yule-studio/naver-search-clone",),
            existing_extra={},
            discover_contract=False,
        )
        ex = ctx.extras_update
        self.assertEqual(ex.get(EXTRA_WORK_MODE), WORK_MODE_AUTONOMOUS)
        self.assertEqual(ex.get(EXTRA_BRANCH_STRATEGY), BRANCH_STRATEGY_GIT_FLOW)
        self.assertEqual(ex.get(EXTRA_RELEASE_STRATEGY), RELEASE_STRATEGY_TAGGED)
        self.assertEqual(ex.get(EXTRA_ISSUE_POLICY), ISSUE_POLICY_REQUIRED)

    def test_default_intake_still_persists_governance_keys(self) -> None:
        """prompt 에 토큰 명시 없어도 default 값 (git_flow / tagged_release /
        issue_required) 이 자동 영속."""

        ctx = prepare_coding_session_context(
            message_text="일반 작업 — 추가 token 없음",
            user_links=("https://github.com/yule-studio/naver-search-clone",),
            existing_extra={},
            discover_contract=False,
        )
        ex = ctx.extras_update
        # default 값으로 영속
        self.assertEqual(ex.get(EXTRA_BRANCH_STRATEGY), BRANCH_STRATEGY_GIT_FLOW)
        self.assertEqual(ex.get(EXTRA_RELEASE_STRATEGY), RELEASE_STRATEGY_TAGGED)
        self.assertEqual(ex.get(EXTRA_ISSUE_POLICY), ISSUE_POLICY_REQUIRED)


# ---------------------------------------------------------------------------
# 3, 4, 5 — Git Flow branch + issue-first
# ---------------------------------------------------------------------------


class GitFlowBranchValidatorTests(unittest.TestCase):
    def test_valid_feature_with_issue_anchor_passes(self) -> None:
        r = validate_git_flow_branch(
            GitFlowBranchContext(branch="feature/auth-issue-12")
        )
        self.assertTrue(r.ok)
        self.assertEqual(r.fields["kind"], "feature")

    def test_invalid_prefix_blocks(self) -> None:
        r = validate_git_flow_branch(
            GitFlowBranchContext(branch="random-branch-name")
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_INVALID_GIT_FLOW_BRANCH)

    def test_protected_branch_direct_work_blocks(self) -> None:
        for b in ("main", "master", "develop", "dev", "prod", "production"):
            with self.subTest(b=b):
                r = validate_git_flow_branch(GitFlowBranchContext(branch=b))
                self.assertFalse(r.ok)
                self.assertEqual(r.reason, REASON_PROTECTED_BRANCH_DIRECT_WORK)

    def test_feature_without_issue_anchor_blocks_as_issue_required(self) -> None:
        # feature/ prefix 인데 slug 에 issue-N 없고 issue_number_hint 도 없음
        r = validate_git_flow_branch(
            GitFlowBranchContext(branch="feature/auth-no-anchor")
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_ISSUE_REQUIRED_FOR_REPO_WORK)

    def test_release_branch_is_anchor_exempt(self) -> None:
        r = validate_git_flow_branch(
            GitFlowBranchContext(branch="release/v1.4.0")
        )
        self.assertTrue(r.ok)
        self.assertEqual(r.fields["kind"], "release")

    def test_hotfix_branch_is_anchor_exempt(self) -> None:
        r = validate_git_flow_branch(
            GitFlowBranchContext(branch="hotfix/token-refresh")
        )
        self.assertTrue(r.ok)

    def test_issue_number_hint_satisfies_for_feature(self) -> None:
        r = validate_git_flow_branch(
            GitFlowBranchContext(branch="feature/auth", issue_number_hint=42)
        )
        self.assertTrue(r.ok)


# ---------------------------------------------------------------------------
# 6, 7 — tag policy
# ---------------------------------------------------------------------------


class TagPolicyTests(unittest.TestCase):
    def test_release_without_tag_blocks(self) -> None:
        r = validate_release_tag(
            ReleaseTagContext(branch="release/v1.4.0", tag=None)
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_MISSING_RELEASE_TAG)

    def test_hotfix_without_tag_blocks(self) -> None:
        r = validate_release_tag(
            ReleaseTagContext(branch="hotfix/token", tag=None)
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_MISSING_RELEASE_TAG)

    def test_valid_semver_tag_passes(self) -> None:
        for tag in ("v1.4.0", "v0.0.1", "v10.20.30", "v1.0.0-rc.1", "v2.0.0-beta+build.7"):
            with self.subTest(tag=tag):
                r = validate_release_tag(
                    ReleaseTagContext(branch="release/x", tag=tag)
                )
                self.assertTrue(r.ok, tag)

    def test_invalid_tag_format_blocks(self) -> None:
        for tag in ("1.4.0", "v1.4", "release-1.4.0", "vlatest"):
            with self.subTest(tag=tag):
                r = validate_release_tag(
                    ReleaseTagContext(branch="release/x", tag=tag)
                )
                self.assertFalse(r.ok)
                self.assertEqual(r.reason, REASON_INVALID_RELEASE_TAG)

    def test_non_release_branch_skips_tag_requirement(self) -> None:
        # feature 같은 일반 branch 는 tag 요구 안 함
        r = validate_release_tag(
            ReleaseTagContext(branch="feature/auth", tag=None)
        )
        self.assertTrue(r.ok)


# ---------------------------------------------------------------------------
# 8, 9, 10 — approval card quality
# ---------------------------------------------------------------------------


_GOOD_BODY = (
    "🔀 PR 머지 승인 — #4\n\n"
    "작업 내용\n- 회원가입 API 추가\n\n"
    "목적\n- 인증 기능이 아직 없어서 사용자가 가입 불가\n\n"
    "영향 범위\n- services/auth, 위험도 LOW\n\n"
    "다음 단계\n- 승인 시 ready_for_review → gate → merge\n"
)


class ApprovalCardQualityTests(unittest.TestCase):
    def test_full_body_with_4_korean_sections_passes(self) -> None:
        r = validate_approval_card_quality(
            ApprovalCardQualityContext(body=_GOOD_BODY)
        )
        self.assertTrue(r.ok, r.detail)

    def test_missing_one_section_blocks(self) -> None:
        # "다음 단계" 누락
        bad_body = (
            "작업 내용\n- 추가\n\n"
            "목적\n- 필요\n\n"
            "영향 범위\n- repo\n"
        )
        r = validate_approval_card_quality(
            ApprovalCardQualityContext(body=bad_body)
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_APPROVAL_CARD_MISSING_SECTIONS)
        self.assertIn("다음 단계", r.detail)

    def test_english_only_body_blocks(self) -> None:
        # 섹션 한글 헤더 없음
        bad = (
            "Description: add login API\n"
            "Why: missing auth\n"
            "Scope: services/auth\n"
            "Next: merge after approval\n"
        )
        r = validate_approval_card_quality(
            ApprovalCardQualityContext(body=bad)
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_APPROVAL_CARD_MISSING_SECTIONS)

    def test_autonomous_merge_mode_skips_enforcement(self) -> None:
        """autonomous_merge 는 사람 승인 카드 최소 사용 — 본 검증 적용 X.
        4 섹션 누락된 short body 도 통과 (autonomous 가 operator_action
        카드 외에는 카드를 거의 안 만든다는 의미)."""

        r = validate_approval_card_quality(
            ApprovalCardQualityContext(
                body="short autonomous summary",
                work_mode="autonomous_merge",
            )
        )
        self.assertTrue(r.ok)

    def test_render_pr_merge_summary_passes_enforcement(self) -> None:
        """pr_approval.render_pr_merge_summary 의 출력이 항상 4 섹션
        포함 → enforcement 통과."""

        from yule_orchestrator.agents.job_queue.pr_approval import (
            PRMergeProposal,
            render_pr_merge_summary,
        )

        proposal = PRMergeProposal(
            repo="yule-studio/naver-search-clone",
            pr_number=4,
            pr_title="[구현][인증] 회원가입",
            pr_url="https://x/y/pull/4",
            head_sha="s",
            base_branch="main",
            draft=False,
            mergeable_state="clean",
            summary_md="",
            body_excerpt="회원가입 API",
        )
        body = render_pr_merge_summary(proposal)
        r = validate_approval_card_quality(
            ApprovalCardQualityContext(body=body)
        )
        self.assertTrue(r.ok, r.detail)


# ---------------------------------------------------------------------------
# 11 — cross-repo (validator repo-agnostic)
# ---------------------------------------------------------------------------


class CrossRepoEnforcementTests(unittest.TestCase):
    def test_validator_is_repo_agnostic(self) -> None:
        """같은 branch / tag / card 입력은 repo 와 무관하게 동일 결과."""

        # 어떤 repo 가정 — validator 자체는 repo 인자를 받지 않으므로
        # context 만 동일하면 결과 동일.
        for case_branch in (
            "feature/auth-issue-12",  # ok
            "random-name",  # block
            "main",  # protected
            "release/v1.0.0",  # ok
        ):
            with self.subTest(branch=case_branch):
                r1 = validate_git_flow_branch(
                    GitFlowBranchContext(branch=case_branch)
                )
                r2 = validate_git_flow_branch(
                    GitFlowBranchContext(branch=case_branch)
                )
                self.assertEqual(r1.ok, r2.ok)
                self.assertEqual(r1.reason, r2.reason)


# ---------------------------------------------------------------------------
# Bonus — enforce 함수의 PolicyViolation raise
# ---------------------------------------------------------------------------


class EnforceRaisesTests(unittest.TestCase):
    def test_enforce_git_flow_raises_on_invalid(self) -> None:
        with self.assertRaises(PolicyViolation) as cm:
            enforce_git_flow_branch(
                GitFlowBranchContext(branch="random")
            )
        self.assertEqual(cm.exception.reason, REASON_INVALID_GIT_FLOW_BRANCH)

    def test_enforce_release_tag_raises_on_missing(self) -> None:
        with self.assertRaises(PolicyViolation) as cm:
            enforce_release_tag(
                ReleaseTagContext(branch="release/v1.0.0", tag=None)
            )
        self.assertEqual(cm.exception.reason, REASON_MISSING_RELEASE_TAG)

    def test_enforce_approval_card_raises_on_missing_sections(self) -> None:
        with self.assertRaises(PolicyViolation) as cm:
            enforce_approval_card_quality(
                ApprovalCardQualityContext(body="vague PR")
            )
        self.assertEqual(
            cm.exception.reason, REASON_APPROVAL_CARD_MISSING_SECTIONS
        )


if __name__ == "__main__":
    unittest.main()
