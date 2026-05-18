"""issue auto-create quality + repair 회귀 — 사용자 필수 7 종.

사용자 라이브 스모크 evidence:
- created issue #1: title=raw prompt, labels=빈, body=`no_repo_template`
- 운영 규칙: 한국어 명확 title, deterministic label, Obsidian/Yule fallback
  template, audit visibility, 이미 잘못 만든 issue repair 가능

필수 7 종:
1. no repo template fallback no longer produces raw prompt title
2. fallback title is clear Korean summary for the current full-stack request
3. labels are populated in fallback path
4. repo template labels still win when template exists
5. Obsidian/Yule fallback template path works and is auditable
6. existing created issue repair path works
7. regression: issue-less auto-create still succeeds
"""

from __future__ import annotations

import unittest
from typing import Any, List, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


from yule_orchestrator.agents.git.repo_contract import RepoContract
from yule_orchestrator.agents.github_workos.issue_auto_create import (
    AUDIT_TEMPLATE_FALLBACK,
    build_default_issue_body,
    build_issue_auto_create_plan,
)
from yule_orchestrator.agents.github_workos.issue_quality import (
    INTENT_FEATURE,
    INTENT_FULL_STACK_MVP,
    LABEL_AUTO_CREATED,
    LABEL_DOCS,
    LABEL_FEATURE,
    LABEL_FULL_STACK,
    LABEL_REFACTOR,
    LABEL_SOURCE_OPERATOR_EXTRA,
    LABEL_SOURCE_TEMPLATE,
    LABEL_SOURCE_YULE_FALLBACK,
    TEMPLATE_SOURCE_OBSIDIAN,
    TEMPLATE_SOURCE_YULE_DEFAULT,
    derive_default_labels,
    detect_intent,
    detect_scopes,
    resolve_template_source,
    synthesize_korean_title,
)
from yule_orchestrator.agents.github_workos.issue_repair import (
    IssueRepairOutcome,
    repair_existing_issue,
    repair_outcome_to_audit,
)


_NAVER_PROMPT = (
    "repo: https://github.com/yule-studio/naver-search-clone.git\n"
    "목표: 네이버의 기본 검색 경험을 참고한 풀스택 MVP를 구현해줘. "
    "범위는 검색, 블로그, 메일 기능 정도까지만 한다.\n"
    "구현 범위: 회원가입 / 로그인 / 로그아웃 / 검색 홈 / 검색 결과 / "
    "블로그 / 메일 / docker compose"
)


# ---------------------------------------------------------------------------
# 1 + 2. 한국어 명확 title — raw prompt 가 아니어야 함
# ---------------------------------------------------------------------------


class FallbackTitleQualityTests(unittest.TestCase):
    def test_no_raw_prompt_title(self) -> None:
        """사용자 §1번 — raw intake prompt 가 title 에 그대로 들어가면 안 됨."""

        plan = build_default_issue_body(
            request_summary=_NAVER_PROMPT,
            session_id="sess-x",
        )
        # raw prompt 의 'repo: https://github.com/' 같은 텍스트가 title 에
        # 절대 들어가면 안 됨
        self.assertNotIn("repo:", plan.title)
        self.assertNotIn("https://", plan.title)

    def test_korean_clear_title_for_fullstack(self) -> None:
        """사용자 §2번 — 한국어 명확 제목."""

        plan = build_default_issue_body(
            request_summary=_NAVER_PROMPT,
            session_id="sess-x",
        )
        # 사용자가 명시한 기대 형태와 같은 골격: `[Feature] ... 풀스택 MVP 구축 (...)`
        self.assertTrue(plan.title.startswith("[Feature]"))
        self.assertIn("풀스택 MVP", plan.title)
        self.assertIn("(인증/검색/블로그/메일)", plan.title)
        # 도메인 토큰 (네이버 검색형) 도 들어감
        self.assertIn("네이버 검색형", plan.title)
        # title 길이는 110 자 이내
        self.assertLessEqual(len(plan.title), 110)

    def test_title_synthesizer_falls_back_to_summary_when_no_scope(self) -> None:
        title, strategy = synthesize_korean_title(
            request_text="아무 텍스트 — 명확한 scope 토큰 없음",
            intent=INTENT_FEATURE,
            scopes=(),
            fallback_summary="단일 helper 함수 추가",
        )
        # intent 만 잡히고 scope 가 없으면 summary 의 첫 줄을 prefix 와 결합
        self.assertEqual(strategy, "intent_fallback_summary")
        self.assertIn("신규 기능 추가", title)


# ---------------------------------------------------------------------------
# 3. fallback 경로에 labels 가 채워짐
# ---------------------------------------------------------------------------


class FallbackLabelsTests(unittest.TestCase):
    def test_fullstack_fallback_has_labels(self) -> None:
        """사용자 §3번 — issue #1 처럼 빈 labels 가 다시 나오면 안 됨."""

        plan = build_default_issue_body(
            request_summary=_NAVER_PROMPT,
            session_id="sess-x",
        )
        # 빈 tuple 아님
        self.assertGreater(len(plan.labels), 0)
        # full-stack 매칭 + auto-created marker 모두 포함
        self.assertIn(LABEL_FULL_STACK, plan.labels)
        self.assertIn(LABEL_FEATURE, plan.labels)
        self.assertIn(LABEL_AUTO_CREATED, plan.labels)

    def test_label_source_audit_in_body(self) -> None:
        plan = build_default_issue_body(
            request_summary=_NAVER_PROMPT,
            session_id="sess-x",
        )
        # body 끝의 engineering-agent audit 섹션에 label_source 가 stamp
        self.assertIn("label_source: `yule_fallback`", plan.body)
        self.assertIn("title_strategy: `intent_template`", plan.body)
        self.assertIn("template_source: `yule_default`", plan.body)


class LabelDerivationTests(unittest.TestCase):
    def test_template_labels_win(self) -> None:
        """사용자 §4번 — repo template labels 가 우선."""

        resolution = derive_default_labels(
            request_text=_NAVER_PROMPT,
            template_labels=("custom:from-template",),
            intent=INTENT_FULL_STACK_MVP,
        )
        # template 라벨이 있으면 primary_source 가 template
        self.assertEqual(resolution.primary_source, LABEL_SOURCE_TEMPLATE)
        # template label 이 포함되고, Yule fallback (full-stack/feature) 은
        # 추가되지 않음 — primary 만 winner
        self.assertIn("custom:from-template", resolution.labels)
        self.assertNotIn(LABEL_FULL_STACK, resolution.labels)
        # auto-created marker 는 항상 추가
        self.assertIn(LABEL_AUTO_CREATED, resolution.labels)
        self.assertEqual(
            resolution.sources_per_label["custom:from-template"],
            LABEL_SOURCE_TEMPLATE,
        )

    def test_extra_labels_audit_source(self) -> None:
        resolution = derive_default_labels(
            request_text=_NAVER_PROMPT,
            extra_labels=("operator:priority-high",),
            intent=INTENT_FEATURE,
        )
        self.assertIn("operator:priority-high", resolution.labels)
        self.assertEqual(
            resolution.sources_per_label["operator:priority-high"],
            LABEL_SOURCE_OPERATOR_EXTRA,
        )

    def test_docs_intent_picks_docs_label(self) -> None:
        resolution = derive_default_labels(
            request_text="readme 문서 보강",
            intent="docs",
        )
        self.assertIn(LABEL_DOCS, resolution.labels)


# ---------------------------------------------------------------------------
# 5. Obsidian fallback template loader path
# ---------------------------------------------------------------------------


class ObsidianFallbackTests(unittest.TestCase):
    def test_obsidian_loader_supplies_text(self) -> None:
        """사용자 §5번 — Obsidian fallback template path 동작 + audit."""

        obsidian_text = (
            "## 사용자 정의 Obsidian template\n\n"
            "이 본문은 vault `80-templates/github-issue/feature.md` 에서 왔다.\n"
        )
        plan = build_default_issue_body(
            request_summary=_NAVER_PROMPT,
            session_id="sess-x",
            obsidian_template_loader=lambda: obsidian_text,
        )
        # Obsidian 텍스트가 body 의 시작에 들어감
        self.assertIn("사용자 정의 Obsidian template", plan.body)
        # audit 섹션이 자동으로 끝에 stamp
        self.assertIn(
            "template_source: `obsidian_fallback`", plan.body
        )
        # title 은 여전히 Korean synthesizer 적용
        self.assertTrue(plan.title.startswith("[Feature]"))

    def test_obsidian_loader_none_falls_through(self) -> None:
        """loader 가 빈 텍스트 / None 반환 → Yule default 로 fall through."""

        plan = build_default_issue_body(
            request_summary=_NAVER_PROMPT,
            obsidian_template_loader=lambda: None,
        )
        self.assertIn("template_source: `yule_default`", plan.body)

    def test_resolve_source_obsidian_when_text(self) -> None:
        decision = resolve_template_source(
            repo_contract_templates=(),
            obsidian_template_loader=lambda: "non-empty text",
        )
        self.assertEqual(decision.source, TEMPLATE_SOURCE_OBSIDIAN)

    def test_resolve_source_yule_default_when_no_inputs(self) -> None:
        decision = resolve_template_source(
            repo_contract_templates=(),
            obsidian_template_loader=None,
        )
        self.assertEqual(decision.source, TEMPLATE_SOURCE_YULE_DEFAULT)


# ---------------------------------------------------------------------------
# 6. existing issue repair path
# ---------------------------------------------------------------------------


class IssueRepairTests(unittest.TestCase):
    def test_dry_run_returns_plan_without_calling_client(self) -> None:
        outcome = repair_existing_issue(
            repo="yule-studio/naver-search-clone",
            issue_number=1,
            request_summary=_NAVER_PROMPT,
            session_id="sess-x",
            dry_run=True,
        )
        self.assertFalse(outcome.updated)
        self.assertTrue(outcome.dry_run)
        self.assertEqual(outcome.skipped_reason, "no_client_wired")
        # plan 은 quality fallback 으로 생성
        self.assertTrue(outcome.plan.title.startswith("[Feature]"))
        self.assertIn(LABEL_FULL_STACK, outcome.plan.labels)

    def test_live_update_calls_client(self) -> None:
        """사용자 §6번 — repair path 가 실제로 client 호출."""

        recorded: List[Mapping[str, Any]] = []

        class _Recorder:
            def update_issue(self, *, repo, issue_number, title=None, body=None, labels=None):
                recorded.append(
                    dict(
                        repo=repo,
                        issue_number=issue_number,
                        title=title,
                        body=body,
                        labels=list(labels or ()),
                    )
                )
                return {"number": issue_number, "html_url": f"https://x/{issue_number}"}

        outcome = repair_existing_issue(
            repo="yule-studio/naver-search-clone",
            issue_number=1,
            request_summary=_NAVER_PROMPT,
            session_id="sess-x",
            client=_Recorder(),
            dry_run=False,
        )
        self.assertTrue(outcome.updated)
        self.assertFalse(outcome.dry_run)
        self.assertEqual(len(recorded), 1)
        sent = recorded[0]
        self.assertEqual(sent["repo"], "yule-studio/naver-search-clone")
        self.assertEqual(sent["issue_number"], 1)
        # 새 title 이 한국어 명확
        self.assertTrue(sent["title"].startswith("[Feature]"))
        self.assertIn("풀스택 MVP", sent["title"])
        # labels 가 비어있지 않음
        self.assertGreater(len(sent["labels"]), 0)
        # response 가 outcome 에 echo
        self.assertEqual(outcome.response["number"], 1)

    def test_client_error_falls_back_to_dry_run_audit(self) -> None:
        class _Boom:
            def update_issue(self, **_):
                raise RuntimeError("simulated 403")

        outcome = repair_existing_issue(
            repo="yule-studio/naver-search-clone",
            issue_number=1,
            request_summary=_NAVER_PROMPT,
            client=_Boom(),
            dry_run=False,
        )
        # raise 가 caller 까지 새지 않고 skipped_reason 으로 변환
        self.assertFalse(outcome.updated)
        self.assertTrue(outcome.skipped_reason.startswith("client_error:"))
        self.assertIn("simulated 403", outcome.response["error"])

    def test_invalid_inputs_return_audit(self) -> None:
        for repo, issue in (("", 1), ("foo", 1), ("foo/bar", 0), ("foo/bar", -1)):
            outcome = repair_existing_issue(
                repo=repo,
                issue_number=issue,
                request_summary="x",
            )
            self.assertFalse(outcome.updated)
            self.assertIn(outcome.skipped_reason, {"invalid_repo", "invalid_issue_number"})

    def test_repair_audit_payload_shape(self) -> None:
        outcome = repair_existing_issue(
            repo="yule-studio/naver-search-clone",
            issue_number=1,
            request_summary=_NAVER_PROMPT,
            dry_run=True,
        )
        audit = repair_outcome_to_audit(outcome)
        self.assertEqual(audit["repo"], "yule-studio/naver-search-clone")
        self.assertEqual(audit["issue_number"], 1)
        self.assertEqual(audit["audit_reason"], "issue_repair")
        self.assertFalse(audit["updated"])
        self.assertTrue(audit["dry_run"])
        self.assertTrue(audit["title"])
        self.assertGreater(len(audit["labels"]), 0)


# ---------------------------------------------------------------------------
# 7. regression — 기존 build_issue_auto_create_plan 도 여전히 OK
# ---------------------------------------------------------------------------


class IssueLessAutoCreateRegression(unittest.TestCase):
    def test_no_template_path_still_succeeds(self) -> None:
        contract = RepoContract(
            owner="yule-studio", repo="naver-search-clone"
        )
        outcome = build_issue_auto_create_plan(
            repo_contract=contract,
            request_summary=_NAVER_PROMPT,
            session_id="sess-x",
        )
        # plan 생성 성공 + 새 quality 적용됨
        self.assertIsNotNone(outcome.plan)
        plan = outcome.plan
        self.assertTrue(plan.title.startswith("[Feature]"))
        self.assertGreater(len(plan.labels), 0)
        self.assertEqual(outcome.audit_reason, AUDIT_TEMPLATE_FALLBACK)

    def test_intent_and_scope_detection_robust(self) -> None:
        # 빈 텍스트도 안전한 default 로
        self.assertEqual(detect_intent(""), INTENT_FEATURE)
        self.assertEqual(detect_scopes(""), ())
        # 풀스택 prompt → full-stack intent + 4 scope
        self.assertEqual(detect_intent(_NAVER_PROMPT), INTENT_FULL_STACK_MVP)
        self.assertGreaterEqual(len(detect_scopes(_NAVER_PROMPT)), 4)


if __name__ == "__main__":
    unittest.main()
