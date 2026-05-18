"""P1-S — PR title production path 가 절대 machine-like 출력 X.

1. coding executor PR creation path uses Korean human title builder
2. no production path emits machine-like `coding-executor draft` title
3. no production path emits branch-name-derived PR title fallback
4. 166c416a1ed0-like context (repo + issue #5 + backend slice) yields
   valid Korean human-readable PR title
5. missing optional slice metadata still yields human-readable Korean
   fallback title
6. title policy validator and title builder are aligned
7. retry path no longer fails with invalid_pr_title_not_human_readable_korean
8. English-only / mode-token-only prompt 도 한국어 fallback 으로 self-correct
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.coding.human_titles import (
    _korean_fallback_title,
    build_pr_title,
)
from yule_orchestrator.agents.governance.repo_write_policy import (
    PolicyViolation,
    enforce_pr_title,
    validate_pr_title,
)


# ---------------------------------------------------------------------------
# 1, 6 — builder 와 validator 의 정합성
# ---------------------------------------------------------------------------


class BuilderValidatorAlignmentTests(unittest.TestCase):
    """모든 build_pr_title 출력이 enforce_pr_title 통과해야 한다."""

    def test_korean_prompt_passes(self) -> None:
        title = build_pr_title(
            session_prompt="네이버 검색 풀스택 MVP 구축",
            issue_number=5,
        )
        enforce_pr_title(title)  # raises if invalid
        self.assertIn("네이버", title)
        self.assertIn("(#5)", title)

    def test_empty_prompt_self_corrects_to_korean_fallback(self) -> None:
        title = build_pr_title(
            session_prompt="",
            branch_hint="agent/backend-engineer/issue-5-coding-execute",
            issue_number=5,
        )
        enforce_pr_title(title)
        self.assertIn("[구현]", title)
        self.assertIn("(#5)", title)
        # 옛 회귀 — branch_hint 의 segment 가 절대 title 에 안 들어감
        self.assertNotIn("agent/", title)
        self.assertNotIn("backend-engineer", title)
        self.assertNotIn("coding-execute", title)

    def test_english_only_prompt_self_corrects(self) -> None:
        # 영어 prompt 면 한국어 4자 미만 → fallback 으로 self-correct
        title = build_pr_title(
            session_prompt="add login api for naver clone",
            issue_number=5,
        )
        enforce_pr_title(title)
        self.assertIn("[구현]", title)
        # 영어 본문은 절대 통과해서는 안 됨
        self.assertNotIn("add login api", title)

    def test_mode_tokens_only_prompt_self_corrects(self) -> None:
        title = build_pr_title(
            session_prompt="autonomous_merge, git_flow, tagged_release, issue_required",
            issue_number=5,
        )
        enforce_pr_title(title)
        # 모드 토큰은 모두 strip → 한국어 fallback
        self.assertNotIn("autonomous_merge", title)
        self.assertNotIn("git_flow", title)
        self.assertIn("[구현]", title)


# ---------------------------------------------------------------------------
# 2, 3 — 옛 회귀 (machine-like / branch-name) 차단
# ---------------------------------------------------------------------------


class NoMachineLikeOutputTests(unittest.TestCase):
    def test_coding_executor_draft_literal_is_rejected_by_validator(self) -> None:
        """validator 가 옛 fallback 텍스트를 reject 하는지 가드 — 본 PR 이
        validator 와 builder 둘 다 강화한 것을 보장."""

        legacy = "📝 #5 coding-executor draft"
        r = validate_pr_title(legacy)
        self.assertFalse(r.ok)

    def test_builder_never_emits_branch_name_token(self) -> None:
        """canonical session 166c416a1ed0 의 branch
        ``agent/backend-engineer/issue-5-coding-execute`` 의 마지막 segment
        가 절대 title 에 들어가지 않음.  옛 wiring 의 회귀 직접 가드."""

        # 한국어 prompt 정상 + branch_hint 전달 → branch slug 영원히 미사용
        title = build_pr_title(
            session_prompt="네이버 검색 풀스택 MVP 구축",
            branch_hint="agent/backend-engineer/issue-5-coding-execute",
            issue_number=5,
        )
        self.assertNotIn("issue-5-coding-execute", title)
        # 한국어 prompt 빈 case 도
        title2 = build_pr_title(
            session_prompt="",
            branch_hint="agent/backend-engineer/issue-5-coding-execute",
            issue_number=5,
        )
        self.assertNotIn("issue-5-coding-execute", title2)


# ---------------------------------------------------------------------------
# 4, 5 — canonical 166c416a1ed0 context
# ---------------------------------------------------------------------------


class CanonicalSessionContextTests(unittest.TestCase):
    def test_canonical_166c_with_slice_yields_human_title(self) -> None:
        slice_spec = {
            "title": "회원가입 / 로그인 API 1차",
            "area": "auth",
            "executor_role": "backend-engineer",
        }
        title = build_pr_title(
            session_prompt="네이버 검색형 풀스택 MVP 구축",
            slice_spec=slice_spec,
            branch_hint="agent/backend-engineer/issue-5-coding-execute",
            issue_number=5,
        )
        enforce_pr_title(title)
        self.assertIn("[구현][인증]", title)
        self.assertIn("회원가입", title)
        self.assertIn("(#5)", title)

    def test_canonical_166c_without_slice_uses_prompt_summary(self) -> None:
        """slice_spec 없음 — single_scope intake 의 경우. prompt 의
        Korean summary 가 title 로 들어감."""

        title = build_pr_title(
            session_prompt=(
                "네이버 검색형 풀스택 MVP 구축 (인증/검색/UI) "
                "https://github.com/yule-studio/naver-search-clone"
            ),
            slice_spec=None,
            branch_hint="agent/backend-engineer/issue-5-coding-execute",
            issue_number=5,
        )
        enforce_pr_title(title)
        self.assertIn("[구현]", title)
        self.assertIn("네이버", title)
        self.assertIn("(#5)", title)

    def test_canonical_166c_no_slice_no_prompt_uses_korean_default(self) -> None:
        """최악 case — slice / prompt 둘 다 빈약 → Korean default fallback."""

        title = build_pr_title(
            session_prompt="",
            slice_spec=None,
            branch_hint="agent/backend-engineer/issue-5-coding-execute",
            issue_number=5,
        )
        enforce_pr_title(title)
        # default fallback
        self.assertIn("[구현]", title)
        self.assertIn("코딩 작업", title)
        self.assertIn("(#5)", title)


# ---------------------------------------------------------------------------
# 7 — retry path: 다시 호출해도 같은 결과 + 항상 통과
# ---------------------------------------------------------------------------


class RetryPathTests(unittest.TestCase):
    def test_repeated_build_with_same_input_is_deterministic_and_valid(
        self,
    ) -> None:
        inputs = dict(
            session_prompt="네이버 검색 풀스택 MVP",
            slice_spec=None,
            branch_hint="agent/backend-engineer/issue-5-coding-execute",
            issue_number=5,
        )
        first = build_pr_title(**inputs)
        second = build_pr_title(**inputs)
        third = build_pr_title(**inputs)
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        for t in (first, second, third):
            enforce_pr_title(t)


# ---------------------------------------------------------------------------
# 8 — live PR creator 의 dangerous fallback 회귀 가드
# ---------------------------------------------------------------------------


class LivePRCreatorFallbackGuardTests(unittest.TestCase):
    """coding_executor_live.GithubAppDraftPRCreator.open 의 except fallback
    이 더 이상 ``coding-executor draft`` 리터럴을 emit 하지 않음을 source-
    grep + import 호출로 가드."""

    def test_live_pr_creator_uses_korean_fallback(self) -> None:
        from pathlib import Path

        src = Path(
            "src/yule_orchestrator/agents/job_queue/coding_executor_live.py"
        ).read_text(encoding="utf-8")
        # 옛 회귀 fallback 텍스트가 더 이상 코드에 없음
        self.assertNotIn("coding-executor draft", src)
        # 새 fallback 호출 site 존재
        self.assertIn("_korean_fallback_title", src)
        # post-validate 분기 존재
        self.assertIn("validate_pr_title", src)

    def test_live_creator_imports_human_titles_unconditionally(self) -> None:
        """build_pr_title / _korean_fallback_title 가 import 가능한 상태로
        wiring 됨."""

        from yule_orchestrator.agents.coding.human_titles import (
            _korean_fallback_title,
            build_pr_title,
        )

        self.assertTrue(callable(build_pr_title))
        self.assertTrue(callable(_korean_fallback_title))


# ---------------------------------------------------------------------------
# 보조 — _korean_fallback_title 직접 검증
# ---------------------------------------------------------------------------


class KoreanFallbackTitleTests(unittest.TestCase):
    def test_with_issue_and_area(self) -> None:
        title = _korean_fallback_title(issue_number=5, area="인증")
        enforce_pr_title(title)
        self.assertIn("[구현][인증]", title)
        self.assertIn("(#5)", title)

    def test_with_issue_no_area(self) -> None:
        title = _korean_fallback_title(issue_number=5)
        enforce_pr_title(title)
        self.assertIn("[구현]", title)
        self.assertIn("(#5)", title)

    def test_no_issue_no_area(self) -> None:
        title = _korean_fallback_title(issue_number=None)
        enforce_pr_title(title)
        self.assertIn("[구현]", title)
        # (#N) suffix 없음
        self.assertNotIn("(#", title)


if __name__ == "__main__":
    unittest.main()
