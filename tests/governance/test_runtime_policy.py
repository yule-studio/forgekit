"""Runtime governance policy gates 회귀 핀 — P0-T.

본 test 가 통과한다 = `agents/governance/runtime_policy.py` 가 정의한
hard rail 이 살아있다는 뜻. 정책이 silently 약해지면 가장 먼저 잡힌다.

검사 영역:
  1. Branch policy — protected branch 거부 / 표준 prefix 권장 / issue
     anchor warning / derive_standard_branch_name 결정성
  2. PR body — 5 섹션 + audit block / 누락 시 fail
  3. Curated note — inbox 직접 승격 거부 / frontmatter 필수 / 본문
     필수 섹션 / hub linkage warning
  4. Orphan / broken link — hub 없음 + related 없음 = orphan,
     wikilink 가 available_paths 에 없음 = broken
  5. Retrieval eval — entry 스키마 / fixture 최소 count / top-5
  6. Post-test hardening — opening criteria 8 종 매칭 / 빈 obs 거부
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.governance import (
    BRANCH_PREFIXES,
    CURATED_REQUIRED_FRONTMATTER,
    CURATED_REQUIRED_SECTIONS,
    HARDENING_OPENING_CRITERIA,
    INBOX_PATH_PREFIX,
    MIN_RETRIEVAL_EVAL_QUESTIONS,
    PR_REQUIRED_SECTIONS,
    RETRIEVAL_EVAL_REQUIRED_KEYS,
    RETRIEVAL_EVAL_TOP_K,
    TARGET_RETRIEVAL_EVAL_QUESTIONS,
    decide_hardening_opening,
    derive_standard_branch_name,
    detect_broken_links,
    detect_orphan_note,
    is_inbox_path,
    validate_branch_name,
    validate_curated_note,
    validate_pr_body,
    validate_retrieval_eval_entry,
    validate_retrieval_eval_fixture,
)


# ---------------------------------------------------------------------------
# Branch
# ---------------------------------------------------------------------------


class BranchPolicyTests(unittest.TestCase):
    def test_protected_branches_rejected(self) -> None:
        for name in ("main", "master", "develop", "release", "production"):
            with self.subTest(name=name):
                r = validate_branch_name(name)
                self.assertFalse(r.allowed)
                self.assertTrue(r.reason.startswith("protected_branch"))

    def test_qualified_protected_ref_rejected(self) -> None:
        for name in (
            "refs/heads/main",
            "origin/main",
            "feature/main",  # 마지막 segment 가 main → 거부
        ):
            with self.subTest(name=name):
                self.assertFalse(validate_branch_name(name).allowed)

    def test_empty_branch_rejected(self) -> None:
        self.assertFalse(validate_branch_name(None).allowed)
        self.assertFalse(validate_branch_name("").allowed)

    def test_invalid_chars_rejected(self) -> None:
        self.assertFalse(validate_branch_name("Feat/CAPS").allowed)
        self.assertFalse(validate_branch_name("feat with spaces").allowed)

    def test_standard_prefix_ok_and_no_warnings(self) -> None:
        r = validate_branch_name("feat/auth-issue-77", issue_number=77)
        self.assertTrue(r.allowed)
        self.assertEqual(r.warnings, ())

    def test_issue_anchor_missing_warns(self) -> None:
        r = validate_branch_name("feat/auth-only", issue_number=77)
        self.assertTrue(r.allowed)
        self.assertTrue(
            any("missing_issue_anchor" in w for w in r.warnings)
        )

    def test_non_standard_prefix_warning_only_by_default(self) -> None:
        # 'agent/...' 은 기존 derive_branch_name 패턴 — 회귀 없도록 허용
        r = validate_branch_name("agent/backend-engineer/issue-77-foo")
        self.assertTrue(r.allowed)
        # custom prefix 는 warning
        r2 = validate_branch_name("custom-prefix/foo")
        self.assertTrue(r2.allowed)
        self.assertTrue(any("non_standard_prefix" in w for w in r2.warnings))

    def test_non_standard_prefix_can_be_strict(self) -> None:
        r = validate_branch_name(
            "custom-prefix/foo", require_standard_prefix=True
        )
        self.assertFalse(r.allowed)
        self.assertTrue(r.reason.startswith("non_standard_prefix"))

    def test_derive_standard_branch_name_includes_issue(self) -> None:
        name = derive_standard_branch_name(
            kind="feat", short_purpose="auth flow", issue_number=77
        )
        self.assertEqual(name, "feat/auth-flow-issue-77")

    def test_derive_standard_branch_name_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            derive_standard_branch_name(
                kind="experimental", short_purpose="x"
            )


# ---------------------------------------------------------------------------
# PR body
# ---------------------------------------------------------------------------


_VALID_PR_BODY = """
## 📌 관련 이슈
closes #77

## ✨ 과제 내용

목적: ...

## 범위
in_scope / out_of_scope

## 리스크
- foo

## 테스트
- bar

🤖 engineering-agent audit
"""


class PRBodyPolicyTests(unittest.TestCase):
    def test_required_sections_table_5_items(self) -> None:
        self.assertEqual(
            set(PR_REQUIRED_SECTIONS.keys()),
            {"purpose", "scope", "risks", "tests", "issue_linkage"},
        )

    def test_valid_pr_body_passes(self) -> None:
        r = validate_pr_body(_VALID_PR_BODY)
        self.assertTrue(r.ok)
        self.assertEqual(r.missing_sections, ())
        self.assertTrue(r.audit_block_present)

    def test_missing_sections_collected(self) -> None:
        r = validate_pr_body("## 목적\nonly this")
        self.assertFalse(r.ok)
        self.assertIn("scope", r.missing_sections)
        self.assertIn("risks", r.missing_sections)
        self.assertIn("tests", r.missing_sections)
        self.assertIn("issue_linkage", r.missing_sections)

    def test_missing_audit_block_warns(self) -> None:
        body = """
## 목적
x
## 범위
x
## 리스크
x
## 테스트
x
## 관련 이슈
closes #1
"""
        r = validate_pr_body(body)
        self.assertFalse(r.ok)  # audit 누락 시 fail
        self.assertFalse(r.audit_block_present)
        self.assertIn("missing_audit_block", r.warnings)

    def test_empty_body_fails(self) -> None:
        r = validate_pr_body("")
        self.assertFalse(r.ok)
        self.assertEqual(
            set(r.missing_sections), set(PR_REQUIRED_SECTIONS.keys())
        )


# ---------------------------------------------------------------------------
# Curated note
# ---------------------------------------------------------------------------


def _full_frontmatter() -> dict:
    return {
        "title": "네이버 검색 클론 — 인증 흐름",
        "kind": "curated",
        "status": "draft",
        "created_at": "2026-05-15",
        "tags": ["auth", "backend"],
        "related": ["[[hub-app-arch]]"],
        "home_hub": "_moc/30-areas-engineering",
    }


_VALID_CURATED_BODY = """
## 핵심 요약
회원가입은 JWT.

## 내 해석
session 보다 stateless

## 적용 맥락
naver-search-clone

## 관련 노트
[[hub-app-arch]]

## 참고
[[00-inbox/links/foo]]
"""


class CuratedNoteTests(unittest.TestCase):
    def test_required_frontmatter_table(self) -> None:
        self.assertIn("title", CURATED_REQUIRED_FRONTMATTER)
        self.assertIn("home_hub", CURATED_REQUIRED_FRONTMATTER)
        self.assertIn("related", CURATED_REQUIRED_FRONTMATTER)

    def test_required_sections_table(self) -> None:
        self.assertEqual(
            set(CURATED_REQUIRED_SECTIONS.keys()),
            {"summary", "interpretation", "context", "related", "references"},
        )

    def test_inbox_path_rejected(self) -> None:
        r = validate_curated_note(
            path="00-inbox/sample.md", frontmatter=_full_frontmatter()
        )
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "inbox_direct_promotion_forbidden")

    def test_is_inbox_path_helper(self) -> None:
        self.assertTrue(is_inbox_path("00-inbox/foo.md"))
        self.assertTrue(is_inbox_path(f"./{INBOX_PATH_PREFIX}/x.md"))
        self.assertFalse(is_inbox_path("20-areas/foo.md"))
        self.assertFalse(is_inbox_path(None))

    def test_full_curated_note_passes(self) -> None:
        r = validate_curated_note(
            path="20-areas/auth-flow.md",
            frontmatter=_full_frontmatter(),
            body=_VALID_CURATED_BODY,
        )
        self.assertTrue(r.ok, r)
        self.assertEqual(r.missing_frontmatter, ())
        self.assertEqual(r.missing_sections, ())

    def test_missing_frontmatter_keys_collected(self) -> None:
        partial = dict(_full_frontmatter())
        partial.pop("home_hub")
        partial.pop("related")
        r = validate_curated_note(
            path="20-areas/x.md",
            frontmatter=partial,
            body=_VALID_CURATED_BODY,
        )
        self.assertFalse(r.ok)
        self.assertIn("home_hub", r.missing_frontmatter)
        self.assertIn("related", r.missing_frontmatter)

    def test_missing_body_sections_collected(self) -> None:
        r = validate_curated_note(
            path="20-areas/x.md",
            frontmatter=_full_frontmatter(),
            body="## 핵심 요약\nonly summary",
        )
        self.assertFalse(r.ok)
        self.assertIn("interpretation", r.missing_sections)
        self.assertIn("context", r.missing_sections)
        self.assertIn("related", r.missing_sections)
        self.assertIn("references", r.missing_sections)


# ---------------------------------------------------------------------------
# Orphan / broken link
# ---------------------------------------------------------------------------


class OrphanAndBrokenLinkTests(unittest.TestCase):
    def test_orphan_no_hub_no_related(self) -> None:
        self.assertTrue(
            detect_orphan_note(note_path="x", home_hub=None, related=())
        )

    def test_not_orphan_with_hub(self) -> None:
        self.assertFalse(
            detect_orphan_note(
                note_path="x", home_hub="_moc/h", related=()
            )
        )

    def test_not_orphan_with_related_only(self) -> None:
        self.assertFalse(
            detect_orphan_note(
                note_path="x", home_hub=None, related=["[[other]]"]
            )
        )

    def test_orphan_when_hub_not_in_known_set(self) -> None:
        # hub_paths 가 주어졌는데 home_hub 가 그 안에 없고 related 도 없음
        self.assertTrue(
            detect_orphan_note(
                note_path="x",
                home_hub="_moc/unknown",
                related=(),
                hub_paths=("_moc/known-1", "_moc/known-2"),
            )
        )

    def test_broken_link_detects_unknown_target(self) -> None:
        broken = detect_broken_links(
            body="[[known]] [[unknown-target]] [[hubs/path]]",
            available_paths=("known.md", "hubs/path.md"),
        )
        self.assertEqual(broken, ("unknown-target",))

    def test_broken_link_basename_match_passes(self) -> None:
        broken = detect_broken_links(
            body="[[tail]]",
            available_paths=("some/long/path/tail.md",),
        )
        self.assertEqual(broken, ())


# ---------------------------------------------------------------------------
# Retrieval eval
# ---------------------------------------------------------------------------


class RetrievalEvalTests(unittest.TestCase):
    def _entry(self, **overrides):
        base = {
            "question": "JWT 와 session 차이",
            "expected_notes": ["20-areas/auth-flow.md"],
            "allowed_alternatives": [],
            "failure_reason": "",
        }
        base.update(overrides)
        return base

    def test_required_keys_present(self) -> None:
        for key in ("question", "expected_notes", "allowed_alternatives", "failure_reason"):
            self.assertIn(key, RETRIEVAL_EVAL_REQUIRED_KEYS)

    def test_top_k_is_5(self) -> None:
        self.assertEqual(RETRIEVAL_EVAL_TOP_K, 5)

    def test_min_and_target_counts(self) -> None:
        self.assertEqual(MIN_RETRIEVAL_EVAL_QUESTIONS, 50)
        self.assertEqual(TARGET_RETRIEVAL_EVAL_QUESTIONS, 100)

    def test_valid_entry(self) -> None:
        r = validate_retrieval_eval_entry(self._entry())
        self.assertTrue(r.ok)

    def test_missing_key(self) -> None:
        bad = self._entry()
        bad.pop("allowed_alternatives")
        r = validate_retrieval_eval_entry(bad)
        self.assertFalse(r.ok)
        self.assertIn("allowed_alternatives", r.missing_keys)

    def test_empty_question_rejected(self) -> None:
        r = validate_retrieval_eval_entry(self._entry(question=""))
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "empty_question")

    def test_empty_expected_notes_rejected(self) -> None:
        r = validate_retrieval_eval_entry(self._entry(expected_notes=[]))
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "empty_expected_notes")

    def test_fixture_below_min_fails(self) -> None:
        entries = [self._entry()] * 10
        r = validate_retrieval_eval_fixture(entries)
        self.assertFalse(r.ok)
        self.assertTrue(any(w.startswith("below_min") for w in r.warnings))

    def test_fixture_at_min_passes_with_target_warning(self) -> None:
        entries = [self._entry()] * MIN_RETRIEVAL_EVAL_QUESTIONS
        r = validate_retrieval_eval_fixture(entries)
        self.assertTrue(r.ok)
        self.assertTrue(any(w.startswith("below_target") for w in r.warnings))

    def test_fixture_above_target_no_warnings(self) -> None:
        entries = [self._entry()] * (TARGET_RETRIEVAL_EVAL_QUESTIONS + 5)
        r = validate_retrieval_eval_fixture(entries)
        self.assertTrue(r.ok)
        self.assertEqual(r.warnings, ())


# ---------------------------------------------------------------------------
# Post-test hardening
# ---------------------------------------------------------------------------


class HardeningOpeningTests(unittest.TestCase):
    def test_eight_criteria_enumerated(self) -> None:
        self.assertEqual(
            set(HARDENING_OPENING_CRITERIA),
            {
                "queue_backlog",
                "runtime_status_latency",
                "retrieval_eval_regression",
                "prompt_size_ceiling",
                "large_file_rule",
                "duplicate_work",
                "critical_path_bottleneck",
                "flaky_or_slow_test",
            },
        )

    def test_no_observations_denies(self) -> None:
        d = decide_hardening_opening({})
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "no_opening_criteria_met")
        # required artifacts 는 deny 시에도 caller 가 다음 PR 에서 충족
        # 해야 할 항목 정보
        self.assertIn("baseline_measurement", d.required_artifacts)

    def test_queue_backlog_triggers(self) -> None:
        d = decide_hardening_opening({"queue_backlog_jobs": 3})
        self.assertTrue(d.allowed)
        self.assertIn("queue_backlog", d.matched_criteria)

    def test_runtime_status_latency_triggers(self) -> None:
        d = decide_hardening_opening({"status_latency_seconds": 45})
        self.assertTrue(d.allowed)
        self.assertIn("runtime_status_latency", d.matched_criteria)

    def test_retrieval_regression_triggers(self) -> None:
        d = decide_hardening_opening({"retrieval_eval_regression": True})
        self.assertTrue(d.allowed)
        self.assertIn("retrieval_eval_regression", d.matched_criteria)

    def test_prompt_size_ceiling_triggers(self) -> None:
        # ceiling 90% 초과
        d = decide_hardening_opening(
            {"prompt_size_bytes": 300 * 1024, "prompt_size_ceiling": 320 * 1024}
        )
        self.assertTrue(d.allowed)

    def test_large_files_triggers(self) -> None:
        d = decide_hardening_opening({"large_files": ["foo.py"]})
        self.assertTrue(d.allowed)
        self.assertIn("large_file_rule", d.matched_criteria)

    def test_flaky_or_slow_triggers(self) -> None:
        d = decide_hardening_opening({"flaky_tests": ["t1"]})
        self.assertTrue(d.allowed)
        self.assertIn("flaky_or_slow_test", d.matched_criteria)

    def test_multiple_criteria_listed(self) -> None:
        d = decide_hardening_opening(
            {"queue_backlog_jobs": 1, "large_files": ["x.py"]}
        )
        self.assertTrue(d.allowed)
        self.assertEqual(
            set(d.matched_criteria), {"queue_backlog", "large_file_rule"}
        )


# ---------------------------------------------------------------------------
# Constants contract — docs 동기화 보호
# ---------------------------------------------------------------------------


class ConstantsContractTests(unittest.TestCase):
    """docs 가 명시한 정책 상수가 코드에서 silently 약해지지 않게 핀."""

    def test_branch_prefixes_includes_core_set(self) -> None:
        for kind in ("feat", "fix", "chore", "refactor"):
            self.assertIn(kind, BRANCH_PREFIXES)

    def test_inbox_path_prefix_is_00_inbox(self) -> None:
        self.assertEqual(INBOX_PATH_PREFIX, "00-inbox")


if __name__ == "__main__":
    unittest.main()
