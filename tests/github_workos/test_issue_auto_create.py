"""Issue auto-create — P0-S end-to-end 모델 회귀 테스트.

다음 contract 를 핀:

  1. ``parse_issue_template`` 가 frontmatter (name/title/labels/assignees) 와
     body 를 정확히 분리.
  2. ``select_issue_template`` 가 single-template repo 에서 HIGH confidence,
     multi-template 에서 키워드 매칭 점수로 confidence 분류.
  3. ``fill_issue_template`` 이 placeholder 를 보존하면서 첫 ``## `` 헤더
     아래에 request_summary 를 quote 형태로 삽입 + audit 섹션 부착.
  4. ``build_default_issue_body`` (fallback) 가 audit_reason=
     ``no_repo_template`` 로 명시.
  5. ``build_issue_auto_create_plan``:
     - ``existing_issue_number`` 가 있으면 plan 은 None, 중복 생성 금지.
     - template 가 없으면 fallback plan.
     - confidence LOW 시 ``needs_operator_decision=True`` 로 DECISION_REQUIRED
       카드 트리거 가능.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.git.repo_contract import RepoContract
from yule_orchestrator.agents.github_workos.issue_auto_create import (
    AUDIT_EXISTING_ISSUE_REUSED,
    AUDIT_TEMPLATE_AMBIGUOUS,
    AUDIT_TEMPLATE_FALLBACK,
    AUDIT_TEMPLATE_USED,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    IssueAutoCreateOutcome,
    IssueAutoCreatePlan,
    IssueTemplate,
    build_default_issue_body,
    build_issue_auto_create_plan,
    fill_issue_template,
    parse_issue_template,
    score_template_against_request,
    select_issue_template,
)


_REAL_FEATURE_TEMPLATE = (
    "---\n"
    'name: "[Feature] Issue Template"\n'
    'about: 모든 라벨 관리 이슈 템플릿\n'
    'title: "[기능]"\n'
    'labels: "✨ Feature, 📃 Docs"\n'
    "assignees: ''\n"
    "---\n"
    "\n"
    "## 어떤 기능인가요?\n"
    "> 추가하려는 기능에 대해 간결하게 설명해주세요\n"
    "\n"
    "## 작업 상세 내용\n"
    "- [ ] \n"
    "\n"
    "## 참고할만한 자료(선택)\n"
)


_BUG_TEMPLATE = (
    "---\n"
    'name: "Bug Report"\n'
    'about: 운영 중 발견된 버그를 신고하세요'
    "\n"
    'title: "[Bug]"\n'
    'labels:\n'
    "  - 🐞 BugFix\n"
    "---\n"
    "\n"
    "## 버그 설명\n"
    "> 무엇이 잘못 동작했는지 설명해주세요\n"
    "\n"
    "## 재현 단계\n"
    "1. ...\n"
)


class TemplateParsingTests(unittest.TestCase):
    def test_parses_inline_labels_list(self) -> None:
        tpl = parse_issue_template(
            path=".github/ISSUE_TEMPLATE/feature.md",
            text=_REAL_FEATURE_TEMPLATE,
        )
        self.assertEqual(tpl.name, "[Feature] Issue Template")
        self.assertEqual(tpl.title_prefix, "[기능]")
        self.assertEqual(tpl.labels, ("✨ Feature", "📃 Docs"))
        self.assertEqual(tpl.assignees, ())
        self.assertIn("어떤 기능인가요?", tpl.body)
        self.assertIn("작업 상세 내용", tpl.body)

    def test_parses_indented_labels_list(self) -> None:
        tpl = parse_issue_template(
            path=".github/ISSUE_TEMPLATE/bug.md",
            text=_BUG_TEMPLATE,
        )
        self.assertEqual(tpl.name, "Bug Report")
        self.assertEqual(tpl.title_prefix, "[Bug]")
        self.assertEqual(tpl.labels, ("🐞 BugFix",))

    def test_no_frontmatter_falls_back_to_body(self) -> None:
        tpl = parse_issue_template(
            path=".github/ISSUE_TEMPLATE/raw.md",
            text="just a body without frontmatter\n",
        )
        self.assertEqual(tpl.title_prefix, "")
        self.assertEqual(tpl.labels, ())
        self.assertIn("just a body", tpl.body)


class TemplateSelectionTests(unittest.TestCase):
    def test_single_template_confidence_high(self) -> None:
        tpl = parse_issue_template(
            path=".github/ISSUE_TEMPLATE/feature.md", text=_REAL_FEATURE_TEMPLATE
        )
        selected, score, confidence = select_issue_template(
            templates=(tpl,), request_text="회원가입 구현"
        )
        self.assertIs(selected, tpl)
        self.assertEqual(confidence, CONFIDENCE_HIGH)

    def test_multi_template_with_strong_keyword_match(self) -> None:
        feature = parse_issue_template(
            path=".github/ISSUE_TEMPLATE/feature.md", text=_REAL_FEATURE_TEMPLATE
        )
        bug = parse_issue_template(
            path=".github/ISSUE_TEMPLATE/bug.md", text=_BUG_TEMPLATE
        )
        selected, score, confidence = select_issue_template(
            templates=(feature, bug),
            request_text="운영 중 버그 발견 — 재현 단계 정리",
        )
        self.assertIs(selected, bug)
        self.assertGreaterEqual(score, 2)
        self.assertEqual(confidence, CONFIDENCE_HIGH)

    def test_multi_template_with_no_keyword_match_returns_low(self) -> None:
        feature = parse_issue_template(
            path=".github/ISSUE_TEMPLATE/feature.md", text=_REAL_FEATURE_TEMPLATE
        )
        bug = parse_issue_template(
            path=".github/ISSUE_TEMPLATE/bug.md", text=_BUG_TEMPLATE
        )
        selected, score, confidence = select_issue_template(
            templates=(feature, bug), request_text="zzz qqq xxx"
        )
        self.assertIn(selected, (feature, bug))
        self.assertEqual(score, 0)
        self.assertEqual(confidence, CONFIDENCE_LOW)


class FillTemplateTests(unittest.TestCase):
    def test_fill_prepends_summary_quote_under_first_header(self) -> None:
        tpl = parse_issue_template(
            path=".github/ISSUE_TEMPLATE/feature.md", text=_REAL_FEATURE_TEMPLATE
        )
        plan = fill_issue_template(
            template=tpl,
            request_summary="회원가입/로그인 구현",
            session_id="sess-1",
        )
        self.assertTrue(plan.title.startswith("[기능]"))
        self.assertIn("회원가입/로그인 구현", plan.title)
        # quote 삽입 위치 검증
        self.assertIn("> 회원가입/로그인 구현", plan.body)
        # placeholder 보존
        self.assertIn("추가하려는 기능에 대해 간결하게", plan.body)
        # audit 섹션 포함
        self.assertIn("engineering-agent audit", plan.body)
        self.assertIn("audit_reason: `template_used`", plan.body)
        self.assertIn("sess-1", plan.body)
        # labels 그대로
        self.assertEqual(plan.labels, ("✨ Feature", "📃 Docs"))
        self.assertEqual(plan.template_path, ".github/ISSUE_TEMPLATE/feature.md")
        self.assertFalse(plan.needs_operator_decision)

    def test_fill_appends_section_when_no_header(self) -> None:
        tpl = IssueTemplate(
            path=".github/ISSUE_TEMPLATE/raw.md",
            name="raw",
            body="just text without any header",
        )
        plan = fill_issue_template(template=tpl, request_summary="X 작업")
        self.assertIn("## 작업 컨텍스트", plan.body)
        self.assertIn("> X 작업", plan.body)

    def test_extra_labels_are_merged(self) -> None:
        tpl = parse_issue_template(
            path=".github/ISSUE_TEMPLATE/feature.md", text=_REAL_FEATURE_TEMPLATE
        )
        plan = fill_issue_template(
            template=tpl,
            request_summary="A",
            extra_labels=("🤖 Agent-runtime", "✨ Feature"),  # dup 제거 검증
        )
        # dict-fromkeys 가 원본 라벨 + extra 합치되 중복 없음
        self.assertIn("🤖 Agent-runtime", plan.labels)
        self.assertEqual(plan.labels.count("✨ Feature"), 1)


class DefaultFallbackTests(unittest.TestCase):
    def test_fallback_marks_audit_reason(self) -> None:
        plan = build_default_issue_body(
            request_summary="설명 없는 코딩 요청",
            session_id="sess-fallback",
        )
        self.assertEqual(plan.audit_reason, AUDIT_TEMPLATE_FALLBACK)
        self.assertIn("audit_reason: `no_repo_template`", plan.body)
        self.assertEqual(plan.template_path, None)
        self.assertFalse(plan.needs_operator_decision)
        self.assertIn("설명 없는 코딩 요청", plan.body)


class BuildIssueAutoCreatePlanTests(unittest.TestCase):
    def test_existing_issue_short_circuits(self) -> None:
        rc = RepoContract(owner="o", repo="r")
        outcome = build_issue_auto_create_plan(
            repo_contract=rc,
            request_summary="ignored",
            existing_issue_number=42,
        )
        self.assertIsNone(outcome.plan)
        self.assertEqual(outcome.existing_issue_number, 42)
        self.assertEqual(outcome.audit_reason, AUDIT_EXISTING_ISSUE_REUSED)

    def test_no_templates_returns_fallback(self) -> None:
        rc = RepoContract(owner="o", repo="r")  # issue_templates 비어있음
        outcome = build_issue_auto_create_plan(
            repo_contract=rc,
            request_summary="회원가입 추가",
            session_id="sess-x",
        )
        self.assertIsNotNone(outcome.plan)
        assert outcome.plan is not None
        self.assertEqual(outcome.audit_reason, AUDIT_TEMPLATE_FALLBACK)
        self.assertIn("회원가입 추가", outcome.plan.body)
        self.assertEqual(outcome.candidate_templates, ())

    def test_single_template_with_loader(self) -> None:
        rc = RepoContract(
            owner="o",
            repo="r",
            issue_templates=(".github/ISSUE_TEMPLATE/feature.md",),
        )
        loader_calls: list[str] = []

        def _loader(path: str) -> str:
            loader_calls.append(path)
            return _REAL_FEATURE_TEMPLATE

        outcome = build_issue_auto_create_plan(
            repo_contract=rc,
            request_summary="회원가입/검색 기능 구현",
            template_loader=_loader,
            session_id="sess-single",
        )
        self.assertEqual(loader_calls, [".github/ISSUE_TEMPLATE/feature.md"])
        self.assertIsNotNone(outcome.plan)
        assert outcome.plan is not None
        self.assertEqual(outcome.plan.confidence, CONFIDENCE_HIGH)
        self.assertEqual(outcome.audit_reason, AUDIT_TEMPLATE_USED)
        self.assertIn("> 회원가입/검색 기능 구현", outcome.plan.body)

    def test_ambiguous_multi_template_marks_needs_decision(self) -> None:
        rc = RepoContract(
            owner="o",
            repo="r",
            issue_templates=(
                ".github/ISSUE_TEMPLATE/feature.md",
                ".github/ISSUE_TEMPLATE/bug.md",
            ),
        )
        texts = {
            ".github/ISSUE_TEMPLATE/feature.md": _REAL_FEATURE_TEMPLATE,
            ".github/ISSUE_TEMPLATE/bug.md": _BUG_TEMPLATE,
        }
        outcome = build_issue_auto_create_plan(
            repo_contract=rc,
            request_summary="aaa bbb ccc",  # 매칭 0
            template_loader=lambda p: texts.get(p),
        )
        self.assertIsNotNone(outcome.plan)
        assert outcome.plan is not None
        self.assertEqual(outcome.plan.confidence, CONFIDENCE_LOW)
        self.assertTrue(outcome.plan.needs_operator_decision)
        self.assertEqual(outcome.audit_reason, AUDIT_TEMPLATE_AMBIGUOUS)

    def test_loader_failure_falls_back(self) -> None:
        rc = RepoContract(
            owner="o",
            repo="r",
            issue_templates=(".github/ISSUE_TEMPLATE/missing.md",),
        )
        outcome = build_issue_auto_create_plan(
            repo_contract=rc,
            request_summary="x",
            template_loader=lambda _: None,  # loader 가 못 읽음
        )
        self.assertEqual(outcome.audit_reason, AUDIT_TEMPLATE_FALLBACK)


if __name__ == "__main__":
    unittest.main()
