"""P1-N — 15 사용자 명시 acceptance for hard guard layer.

1.  허용되지 않은 gitmoji commit message reject
2.  변경 이유 / 주요 변경 사항 / 비고 누락 commit reject
3.  `## 변경 이유` (markdown header) reject (SSoT 통일 강제)
4.  issue anchor 없는 PR 생성 시 block
5.  issue title humanizer Korean readable
6.  PR title humanizer Korean readable
7.  machine-like PR title block
8.  valid 조합 통과
9.  coding executor / PR creator live path 에서 guard 호출 (단위 테스트로 guard 호출 가능성 확인)
10. blocker reason status/audit/operator surface 명확
11. cross-repo write path 가드 (validator 는 repo 와 무관하게 동일 동작)
12. initial commit `:tada: initial commit` 정확 매칭
13. non-initial 에서 `:tada: initial commit` reject
14. initial 인데 다른 제목 reject
15. ambiguous detection 시 honest blocker
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_engineering.agents.governance.repo_write_policy import (
    ALLOWED_GITMOJI,
    INITIAL_COMMIT_TITLE_EXACT,
    InitialCommitDecision,
    IssueAnchorContext,
    PolicyViolation,
    REASON_INITIAL_COMMIT_DETECTION_AMBIGUOUS,
    REASON_INVALID_COMMIT_BODY_SECTIONS,
    REASON_INVALID_COMMIT_GITMOJI,
    REASON_INVALID_INITIAL_COMMIT_TITLE,
    REASON_INVALID_ISSUE_TITLE,
    REASON_INVALID_PR_TITLE,
    REASON_ISSUE_REQUIRED_FOR_REPO_WORK,
    REASON_TADA_OUTSIDE_INITIAL_COMMIT,
    REQUIRED_SECTIONS,
    enforce_commit_message,
    enforce_issue_anchor,
    enforce_issue_title,
    enforce_pr_title,
    is_initial_commit_context,
    validate_commit_message,
    validate_initial_commit_decision,
    validate_issue_anchor,
    validate_issue_title,
    validate_pr_title,
)


_VALID_BODY = (
    "변경 이유\n- 회원가입 부재\n\n"
    "주요 변경 사항\n- 로그인 API\n\n"
    "비고\n- 없음"
)


# ---------------------------------------------------------------------------
# 1. gitmoji whitelist
# ---------------------------------------------------------------------------


class GitmojiWhitelistTests(unittest.TestCase):
    def test_rejects_unlisted_gitmoji(self) -> None:
        # 🎚 (recent 위반 케이스), 🔗, 🛡️, 🧪 모두 reject
        for emoji in ("🎚", "🔗", "🛡️", "🧪", "🚀"):
            with self.subTest(emoji=emoji):
                r = validate_commit_message(f"{emoji} 제목\n\n{_VALID_BODY}")
                self.assertFalse(r.ok, emoji)
                self.assertEqual(r.reason, REASON_INVALID_COMMIT_GITMOJI)

    def test_accepts_base_whitelist(self) -> None:
        for emoji in ("✨", "🐛", "♻️", "📝", "✅", "🔧"):
            with self.subTest(emoji=emoji):
                r = validate_commit_message(f"{emoji} 제목\n\n{_VALID_BODY}")
                self.assertTrue(r.ok, emoji)


# ---------------------------------------------------------------------------
# 2. body section enforcement
# ---------------------------------------------------------------------------


class BodySectionEnforcementTests(unittest.TestCase):
    def test_rejects_missing_section(self) -> None:
        for missing in REQUIRED_SECTIONS:
            with self.subTest(missing=missing):
                # rebuild body without the chosen section
                body = "\n\n".join(
                    f"{sec}\n- 내용" for sec in REQUIRED_SECTIONS if sec != missing
                )
                r = validate_commit_message(f"✨ 테스트 제목\n\n{body}")
                self.assertFalse(r.ok)
                self.assertEqual(r.reason, REASON_INVALID_COMMIT_BODY_SECTIONS)
                self.assertIn(missing, r.detail)

    def test_rejects_empty_section_body(self) -> None:
        # section 헤더는 있지만 bullet 없는 경우
        body = "변경 이유\n\n주요 변경 사항\n- 추가\n\n비고\n- 없음"
        r = validate_commit_message(f"✨ 테스트\n\n{body}")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_INVALID_COMMIT_BODY_SECTIONS)


# ---------------------------------------------------------------------------
# 3. markdown header reject (SSoT 통일)
# ---------------------------------------------------------------------------


class MarkdownHeaderRejectTests(unittest.TestCase):
    def test_rejects_md_header_variant(self) -> None:
        body = (
            "## 변경 이유\n- a\n\n"
            "## 주요 변경 사항\n- b\n\n"
            "## 비고\n- 없음"
        )
        r = validate_commit_message(f"✨ 테스트\n\n{body}")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_INVALID_COMMIT_BODY_SECTIONS)
        self.assertIn("plain text", r.detail)


# ---------------------------------------------------------------------------
# 4. issue anchor required
# ---------------------------------------------------------------------------


class IssueAnchorRequiredTests(unittest.TestCase):
    def test_missing_anchor_blocks(self) -> None:
        ctx = IssueAnchorContext(
            branch="feature/some-branch-no-issue",
            pr_body="## 요약\n- 코드 추가",
        )
        r = validate_issue_anchor(ctx)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_ISSUE_REQUIRED_FOR_REPO_WORK)

    def test_branch_issue_anchor_satisfies(self) -> None:
        ctx = IssueAnchorContext(branch="feature/auth-issue-12")
        r = validate_issue_anchor(ctx)
        self.assertTrue(r.ok)
        self.assertEqual(r.fields["issue_number"], 12)

    def test_body_close_anchor_satisfies(self) -> None:
        ctx = IssueAnchorContext(
            branch="feature/no-anchor", pr_body="close #42"
        )
        r = validate_issue_anchor(ctx)
        self.assertTrue(r.ok)
        self.assertEqual(r.fields["issue_number"], 42)

    def test_docs_only_exempt(self) -> None:
        ctx = IssueAnchorContext(branch="docs/runbook", is_docs_only=True)
        r = validate_issue_anchor(ctx)
        self.assertTrue(r.ok)

    def test_issue_number_hint_satisfies(self) -> None:
        ctx = IssueAnchorContext(issue_number_hint=7)
        r = validate_issue_anchor(ctx)
        self.assertTrue(r.ok)
        self.assertEqual(r.fields["issue_number"], 7)


# ---------------------------------------------------------------------------
# 5, 6 — issue/PR title 한국어 humanizer
# ---------------------------------------------------------------------------


class HumanReadableTitleTests(unittest.TestCase):
    def test_issue_title_valid_korean(self) -> None:
        r = validate_issue_title("[기능] 네이버 검색 MVP - 인증/검색 홈 1차")
        self.assertTrue(r.ok)

    def test_pr_title_valid_korean(self) -> None:
        r = validate_pr_title("[구현][검색] 검색 홈 화면 및 결과 탭 UI 1차 (#4)")
        self.assertTrue(r.ok)

    def test_pr_title_english_only_rejected(self) -> None:
        r = validate_pr_title("[구현] fix authentication bug")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_INVALID_PR_TITLE)

    def test_issue_title_missing_prefix_rejected(self) -> None:
        r = validate_issue_title("회원가입 API 추가")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_INVALID_ISSUE_TITLE)


# ---------------------------------------------------------------------------
# 7. machine-like title block
# ---------------------------------------------------------------------------


class MachinelikeTitleBlockTests(unittest.TestCase):
    def test_coding_executor_draft_pattern_blocked(self) -> None:
        r = validate_pr_title("coding-executor draft #4")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_INVALID_PR_TITLE)
        self.assertIn("machine-like", r.detail)

    def test_mode_token_in_title_blocked(self) -> None:
        r = validate_pr_title("[구현] autonomous_merge 인증 추가")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_INVALID_PR_TITLE)


# ---------------------------------------------------------------------------
# 8. valid 조합 통과
# ---------------------------------------------------------------------------


class HappyPathTests(unittest.TestCase):
    def test_valid_commit_issue_pr_anchor_all_pass(self) -> None:
        # commit
        commit = (
            "✨ 회원가입 API 추가\n\n"
            "변경 이유\n- 인증 부재\n\n"
            "주요 변경 사항\n- POST /auth/signup\n- 비밀번호 해시\n\n"
            "비고\n- 없음"
        )
        # All four validators pass
        self.assertTrue(validate_commit_message(commit).ok)
        self.assertTrue(validate_issue_title("[기능][인증] 회원가입 API 추가").ok)
        self.assertTrue(
            validate_pr_title("[구현][인증] 회원가입 API 1차 (#7)").ok
        )
        self.assertTrue(
            validate_issue_anchor(
                IssueAnchorContext(branch="feature/auth-issue-7")
            ).ok
        )

    def test_enforce_helpers_dont_raise_on_valid(self) -> None:
        enforce_commit_message(
            "✨ 추가\n\n변경 이유\n- a\n\n주요 변경 사항\n- b\n\n비고\n- 없음"
        )
        enforce_issue_title("[기능] 인증 추가")
        enforce_pr_title("[구현] 인증 API 추가")
        enforce_issue_anchor(IssueAnchorContext(issue_number_hint=1))


# ---------------------------------------------------------------------------
# 9. live path 에서 guard 호출 (import-time wiring 가드)
# ---------------------------------------------------------------------------


class LiveWiringExistsTests(unittest.TestCase):
    def test_committer_source_references_validator(self) -> None:
        """coding_executor_live.GithubAppCommitter.commit 가 enforce_commit_message
        를 실제 호출하는지 source-grep — 단위 테스트로 wiring 회귀 차단."""

        from pathlib import Path

        src = Path(
            "apps/engineering-agent/src/yule_engineering/agents/job_queue/coding_executor_live.py"
        ).read_text(encoding="utf-8")
        self.assertIn("enforce_commit_message", src)
        self.assertIn("is_initial_commit_context", src)

    def test_pr_creator_source_references_validators(self) -> None:
        from pathlib import Path

        # P0-185: the draft-PR creator (``GithubAppDraftPRCreator``) was
        # split out of ``coding_executor_live`` into the sibling push
        # module during the live-runner/formatting responsibility split.
        # The governance wiring guard follows the code to its new home.
        src = Path(
            "apps/engineering-agent/src/yule_engineering/agents/job_queue/coding_executor_live_push.py"
        ).read_text(encoding="utf-8")
        self.assertIn("enforce_pr_title", src)
        self.assertIn("enforce_issue_anchor", src)

    def test_issue_creator_source_references_validator(self) -> None:
        from pathlib import Path

        src = Path(
            "apps/engineering-agent/src/yule_engineering/agents/github_workos/github_writer.py"
        ).read_text(encoding="utf-8")
        self.assertIn("enforce_issue_title", src)


# ---------------------------------------------------------------------------
# 10. blocker reason 노출
# ---------------------------------------------------------------------------


class PolicyViolationSurfaceTests(unittest.TestCase):
    def test_violation_has_reason_and_fields(self) -> None:
        try:
            enforce_pr_title("[구현] english only title")
        except PolicyViolation as exc:
            self.assertEqual(exc.reason, REASON_INVALID_PR_TITLE)
            self.assertIn("title", exc.fields)
            return
        self.fail("expected PolicyViolation")

    def test_initial_ambiguous_violation_explicit(self) -> None:
        decision = InitialCommitDecision(
            is_initial=False, ambiguous=True, reason="no_signal"
        )
        check = validate_initial_commit_decision(decision)
        self.assertFalse(check.ok)
        self.assertEqual(check.reason, REASON_INITIAL_COMMIT_DETECTION_AMBIGUOUS)


# ---------------------------------------------------------------------------
# 11. cross-repo (validator 자체가 repo-agnostic)
# ---------------------------------------------------------------------------


class CrossRepoApplicationTests(unittest.TestCase):
    def test_validator_does_not_depend_on_repo_full_name(self) -> None:
        # 같은 commit message 는 어떤 repo 든 동일 결과.
        msg = (
            "✨ 인증 추가\n\n변경 이유\n- a\n\n주요 변경 사항\n- b\n\n비고\n- 없음"
        )
        for repo in (
            "yule-studio/yule-studio-agent",
            "yule-studio/naver-search-clone",
            "external/foo",
        ):
            with self.subTest(repo=repo):
                self.assertTrue(validate_commit_message(msg).ok)
        # 동일 PR title 도 어떤 repo 든 동일.
        bad = validate_pr_title("coding-executor draft #4")
        good = validate_pr_title("[구현][검색] 검색 홈 (#1)")
        self.assertFalse(bad.ok)
        self.assertTrue(good.ok)


# ---------------------------------------------------------------------------
# 12-14 — initial commit special case
# ---------------------------------------------------------------------------


class InitialCommitSpecialCaseTests(unittest.TestCase):
    def test_exact_initial_title_accepted(self) -> None:
        msg = (
            ":tada: initial commit\n\n"
            "변경 이유\n- 새 repo 시작\n\n"
            "주요 변경 사항\n- 기본 scaffold\n\n"
            "비고\n- 없음"
        )
        r = validate_commit_message(msg, is_initial=True)
        self.assertTrue(r.ok, r.detail)

    def test_tada_outside_initial_rejected(self) -> None:
        msg = (
            ":tada: 무언가 다른 제목\n\n"
            "변경 이유\n- a\n\n주요 변경 사항\n- b\n\n비고\n- 없음"
        )
        r = validate_commit_message(msg, is_initial=False)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_TADA_OUTSIDE_INITIAL_COMMIT)

    def test_initial_with_wrong_title_rejected(self) -> None:
        msg = (
            "✨ 첫 commit\n\n"
            "변경 이유\n- a\n\n주요 변경 사항\n- b\n\n비고\n- 없음"
        )
        r = validate_commit_message(msg, is_initial=True)
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, REASON_INVALID_INITIAL_COMMIT_TITLE)
        self.assertIn(INITIAL_COMMIT_TITLE_EXACT, r.detail)

    def test_emoji_form_of_tada_also_rejected_outside_initial(self) -> None:
        # 🎉 (unicode emoji form, not shortcode) 도 차단
        msg = (
            "🎉 축하 메시지\n\n변경 이유\n- a\n\n주요 변경 사항\n- b\n\n비고\n- 없음"
        )
        r = validate_commit_message(msg, is_initial=False)
        self.assertFalse(r.ok)
        # 🎉 는 whitelist 에도 없으므로 invalid_commit_gitmoji 또는
        # tada_used_outside_initial_commit 중 하나로 reject — 둘 다 honest.
        self.assertIn(r.reason, (
            REASON_TADA_OUTSIDE_INITIAL_COMMIT,
            REASON_INVALID_COMMIT_GITMOJI,
        ))


# ---------------------------------------------------------------------------
# 15. ambiguous detection
# ---------------------------------------------------------------------------


class AmbiguousInitialDetectionTests(unittest.TestCase):
    def test_no_signal_returns_ambiguous(self) -> None:
        decision = is_initial_commit_context(
            repo_root=None, explicit_hint=None
        )
        self.assertTrue(decision.ambiguous)
        self.assertFalse(decision.is_initial)
        check = validate_initial_commit_decision(decision)
        self.assertFalse(check.ok)
        self.assertEqual(check.reason, REASON_INITIAL_COMMIT_DETECTION_AMBIGUOUS)

    def test_explicit_hint_wins(self) -> None:
        d_true = is_initial_commit_context(explicit_hint=True)
        d_false = is_initial_commit_context(explicit_hint=False)
        self.assertTrue(d_true.is_initial)
        self.assertFalse(d_true.ambiguous)
        self.assertFalse(d_false.is_initial)
        self.assertFalse(d_false.ambiguous)

    def test_zero_commits_returns_initial(self) -> None:
        import tempfile, subprocess

        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["git", "init", "-q", tmp], check=True, capture_output=True
            )
            decision = is_initial_commit_context(repo_root=tmp)
            self.assertTrue(decision.is_initial)
            self.assertFalse(decision.ambiguous)


if __name__ == "__main__":
    unittest.main()
