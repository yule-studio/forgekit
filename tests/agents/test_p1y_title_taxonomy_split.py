"""P1-Y — issue/PR prefix taxonomy 분리 + builder/template 정렬 회귀.

배경
----
이전 (P1-N) 에서 모든 title prefix 가 한국어로 통일됐다가, GitHub 목록
가독성 / 외부 dashboards 와의 정합성을 위해 issue prefix 만 영문
taxonomy 로 다시 정렬했다 — PR prefix 는 한국어 정책 유지.

핵심 contract:
  * issue prefix → ``[Feature] / [Bug] / [Docs] / [Refactor] / [Chore]
    / [Test]`` 만 허용
  * PR prefix → ``[구현] / [수정] / [문서] / [설정] / [테스트] /
    [리팩토링]`` 만 허용
  * 두 정책은 ``validate_issue_title`` / ``validate_pr_title`` 분리
  * ``[기능]`` (옛 한국어 issue prefix) / ``[Feat]`` (옛 축약) 둘 다 거부

본 모듈은 위 contract 가 다음 refactor 때 다시 한 allowlist 로 합쳐지지
않도록 명시 회귀 라인을 박는다.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.coding.human_titles import build_issue_title
from yule_orchestrator.agents.github_workos.issue_quality import (
    INTENT_BUGFIX,
    INTENT_CHORE,
    INTENT_DOCS,
    INTENT_FEATURE,
    INTENT_FULL_STACK_MVP,
    INTENT_REFACTOR,
    INTENT_TEST,
    synthesize_korean_title,
)
from yule_orchestrator.agents.governance.repo_write_policy import (
    REASON_INVALID_ISSUE_TITLE,
    REASON_INVALID_PR_TITLE,
    validate_issue_title,
    validate_pr_title,
)


# ---------------------------------------------------------------------------
# Issue allowlist — English only
# ---------------------------------------------------------------------------


class IssueTitleAllowlistTests(unittest.TestCase):
    def test_accepts_all_six_english_prefixes(self) -> None:
        cases = [
            "[Feature] 네이버 검색 MVP — 인증 흐름 1차",
            "[Bug] 검색 페이지가 빈 결과 처리 실패",
            "[Docs] 운영 매뉴얼 5섹션 구조 정리",
            "[Refactor] 검색 라우터 책임 분리",
            "[Chore] 의존성 업데이트 및 lock 정리",
            "[Test] 검색 API 응답 회귀 추가",
        ]
        for title in cases:
            with self.subTest(title=title):
                result = validate_issue_title(title)
                self.assertTrue(result.ok, msg=f"{title} → {result.reason}/{result.detail}")

    def test_rejects_legacy_korean_prefix(self) -> None:
        # 옛 [기능] prefix — PR 쪽으로 잘못 쓸 일 없게 issue 도 reject.
        result = validate_issue_title("[기능] 네이버 검색 MVP 인증 흐름")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_INVALID_ISSUE_TITLE)

    def test_rejects_legacy_short_feat_prefix(self) -> None:
        # ``[Feat]`` 축약은 명시 거부 — taxonomy 일관성.
        result = validate_issue_title("[Feat] 네이버 검색 MVP 인증")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_INVALID_ISSUE_TITLE)

    def test_rejects_pr_korean_prefix_in_issue(self) -> None:
        # PR 전용 prefix 를 issue 에 잘못 쓰면 reject.
        result = validate_issue_title("[구현] 인증 시스템 보강 1차")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_INVALID_ISSUE_TITLE)

    def test_issue_title_still_requires_korean_body(self) -> None:
        # English prefix 만 있고 본문이 영문이면 4 한국어 chars 미만 → reject.
        result = validate_issue_title("[Feature] add login API endpoint")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, REASON_INVALID_ISSUE_TITLE)


# ---------------------------------------------------------------------------
# PR allowlist — Korean only
# ---------------------------------------------------------------------------


class PRTitleAllowlistTests(unittest.TestCase):
    def test_accepts_all_six_korean_prefixes(self) -> None:
        cases = [
            "[구현][인증] 회원가입 API 1차 (#7)",
            "[수정] 검색 결과 페이지 빈 응답 회귀 (#9)",
            "[문서] 운영 매뉴얼 5섹션 보강 (#11)",
            "[설정] CI 환경 변수 정리 (#13)",
            "[테스트] 회원가입 회귀 케이스 추가 (#15)",
            "[리팩토링] 검색 라우터 책임 분리 (#17)",
        ]
        for title in cases:
            with self.subTest(title=title):
                result = validate_pr_title(title)
                self.assertTrue(result.ok, msg=f"{title} → {result.reason}/{result.detail}")

    def test_rejects_english_issue_prefix_in_pr(self) -> None:
        # 사용자가 강조: PR 은 issue 의 영문 prefix 절대 받지 않음.
        for prefix in ("[Feature]", "[Bug]", "[Docs]", "[Refactor]", "[Chore]", "[Test]"):
            with self.subTest(prefix=prefix):
                result = validate_pr_title(f"{prefix} 인증 API 추가 (#7)")
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, REASON_INVALID_PR_TITLE)


# ---------------------------------------------------------------------------
# Builder + intent mapping alignment
# ---------------------------------------------------------------------------


class IssueTitleBuilderTests(unittest.TestCase):
    def test_build_issue_title_emits_feature_prefix(self) -> None:
        title = build_issue_title(
            session_prompt="네이버 검색형 풀스택 MVP 구축",
        )
        self.assertTrue(title.startswith("[Feature]"), title)

    def test_build_issue_title_with_slice_emits_feature_area(self) -> None:
        title = build_issue_title(
            session_prompt="구현 작업",
            slice_spec={"title": "회원가입 API 1차", "area": "auth"},
        )
        self.assertTrue(title.startswith("[Feature]"), title)
        # 영역 라벨 (한국어) 도 포함되어야 함.
        self.assertIn("인증", title)


class SynthesizeKoreanTitleTests(unittest.TestCase):
    def test_full_stack_emits_feature(self) -> None:
        title, _strategy = synthesize_korean_title(
            request_text="네이버 검색형 풀스택 MVP",
            intent=INTENT_FULL_STACK_MVP,
        )
        self.assertTrue(title.startswith("[Feature]"), title)

    def test_bugfix_emits_bug(self) -> None:
        title, _strategy = synthesize_korean_title(
            request_text="검색 결과 빈 응답 회귀 수정",
            intent=INTENT_BUGFIX,
        )
        self.assertTrue(title.startswith("[Bug]"), title)

    def test_docs_emits_docs(self) -> None:
        title, _ = synthesize_korean_title(request_text="운영 매뉴얼 보강", intent=INTENT_DOCS)
        self.assertTrue(title.startswith("[Docs]"), title)

    def test_refactor_emits_refactor(self) -> None:
        title, _ = synthesize_korean_title(
            request_text="검색 라우터 책임 분리", intent=INTENT_REFACTOR
        )
        self.assertTrue(title.startswith("[Refactor]"), title)

    def test_chore_emits_chore(self) -> None:
        title, _ = synthesize_korean_title(
            request_text="config 정리 작업", intent=INTENT_CHORE
        )
        self.assertTrue(title.startswith("[Chore]"), title)

    def test_test_emits_test(self) -> None:
        title, _ = synthesize_korean_title(
            request_text="회귀 테스트 추가", intent=INTENT_TEST
        )
        self.assertTrue(title.startswith("[Test]"), title)

    def test_feature_emits_feature(self) -> None:
        title, _ = synthesize_korean_title(
            request_text="신규 기능 추가", intent=INTENT_FEATURE
        )
        self.assertTrue(title.startswith("[Feature]"), title)


# ---------------------------------------------------------------------------
# Template alignment
# ---------------------------------------------------------------------------


class FeatureIssueTemplateTests(unittest.TestCase):
    def test_feature_template_title_prefix_is_feature(self) -> None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / ".github" / "ISSUE_TEMPLATE" / "-feature--issue-template.md"
        text = path.read_text(encoding="utf-8")
        # ``title:`` 줄이 [Feature] 로 정렬.
        self.assertIn('title: "[Feature]"', text)
        # 옛 표기 흔적이 없어야.
        self.assertNotIn('title: "[Feat]"', text)
        self.assertNotIn('title: "[기능]"', text)


# ---------------------------------------------------------------------------
# Cross-allowlist isolation guard
# ---------------------------------------------------------------------------


class AllowlistIsolationGuardTests(unittest.TestCase):
    def test_issue_and_pr_allowlists_are_disjoint(self) -> None:
        from yule_orchestrator.agents.governance.repo_write_policy import (
            _ALLOWED_ISSUE_TITLE_PREFIXES,
            _ALLOWED_PR_TITLE_PREFIXES,
        )

        issue_set = set(_ALLOWED_ISSUE_TITLE_PREFIXES)
        pr_set = set(_ALLOWED_PR_TITLE_PREFIXES)
        intersection = issue_set & pr_set
        self.assertEqual(intersection, set(), f"shared prefixes: {intersection}")
        # 그리고 각자 6개 이상 (정책 contract).
        self.assertGreaterEqual(len(issue_set), 6)
        self.assertGreaterEqual(len(pr_set), 6)

    def test_validator_module_exports_split_allowlists(self) -> None:
        from yule_orchestrator.agents.governance import repo_write_policy as mod

        self.assertTrue(hasattr(mod, "_ALLOWED_ISSUE_TITLE_PREFIXES"))
        self.assertTrue(hasattr(mod, "_ALLOWED_PR_TITLE_PREFIXES"))
        # 옛 통합 이름 흔적이 정의돼있지 않아야 — refactor 때 collision 방지.
        self.assertFalse(hasattr(mod, "_ALLOWED_TITLE_PREFIXES"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
