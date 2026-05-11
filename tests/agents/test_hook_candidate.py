"""hook_candidate — issue #81 round 1.

Pin the postmortem → future-hook contract:

  * ``slugify_hook_id`` is deterministic + idempotent across calls.
  * ``promote_record_to_hook_candidate`` returns ``None`` when the
    record's evidence count is below ``min_evidence``.
  * ``promote_postmortem_to_hook_candidate`` always returns a
    candidate (the postmortem author has decided promotion is
    warranted) and stamps the same id as the record-based path for
    matching ``(role, key)``.
  * ``collect_hook_candidates`` filters and ranks the ledger.
  * ``render_hook_candidate_block`` produces the empty string when
    nothing matches so the operator surface stays compact.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.hook_candidate import (
    HOOK_CANDIDATE_ID_PREFIX,
    collect_hook_candidates,
    promote_postmortem_to_hook_candidate,
    promote_record_to_hook_candidate,
    render_hook_candidate_block,
    slugify_hook_id,
)
from yule_orchestrator.agents.lifecycle.mistake_ledger import (
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SOURCE_BLOCKED_COMPLETION,
    SOURCE_CI_FAILURE,
    SOURCE_POSTMORTEM,
    record_mistake,
    read_mistake_ledger,
)


class SlugifyTests(unittest.TestCase):
    def test_basic_slug(self) -> None:
        self.assertEqual(
            slugify_hook_id("devops", "ci:protected_branch_blocked"),
            f"{HOOK_CANDIDATE_ID_PREFIX}-devops-ci-protected-branch-blocked",
        )

    def test_idempotent(self) -> None:
        a = slugify_hook_id("qa-engineer", "missing_regression_check")
        b = slugify_hook_id("qa-engineer", "missing_regression_check")
        self.assertEqual(a, b)

    def test_collapses_special_characters(self) -> None:
        self.assertEqual(
            slugify_hook_id("Backend Engineer", "FAIL!!  test ::case"),
            f"{HOOK_CANDIDATE_ID_PREFIX}-backend-engineer-fail-test-case",
        )

    def test_falls_back_when_blank(self) -> None:
        self.assertEqual(
            slugify_hook_id("", ""),
            f"{HOOK_CANDIDATE_ID_PREFIX}-role-mistake",
        )


class PromoteFromRecordTests(unittest.TestCase):
    def test_below_min_evidence_returns_none(self) -> None:
        extra, record = record_mistake(
            None,
            role_id="devops",
            mistake_key="ci:lint",
            summary="lint",
            prevention_hint="lint 먼저",
            severity=SEVERITY_LOW,
            when="2026-05-10T00:00:00+00:00",
        )
        self.assertIsNone(
            promote_record_to_hook_candidate(record, min_evidence=2)
        )

    def test_at_min_evidence_promotes(self) -> None:
        extra = None
        for i in range(2):
            extra, record = record_mistake(
                extra,
                role_id="devops",
                mistake_key="ci:lint",
                summary="lint 실패",
                prevention_hint="lint 먼저",
                severity=SEVERITY_MEDIUM,
                source_kind=SOURCE_CI_FAILURE,
                when=f"2026-05-10T00:0{i}:00+00:00",
            )
        candidate = promote_record_to_hook_candidate(record, min_evidence=2)
        assert candidate is not None
        self.assertEqual(candidate.role_id, "devops")
        self.assertEqual(candidate.mistake_key, "ci:lint")
        self.assertEqual(candidate.evidence_count, 2)
        self.assertEqual(candidate.source_kind, SOURCE_CI_FAILURE)
        self.assertEqual(
            candidate.future_hook_id,
            slugify_hook_id("devops", "ci:lint"),
        )


class PromotePostmortemTests(unittest.TestCase):
    def test_postmortem_always_promotes(self) -> None:
        candidate = promote_postmortem_to_hook_candidate(
            role_id="qa-engineer",
            mistake_key="regression_check_skipped",
            summary="회귀 시나리오 누락 — 사용자가 직접 발견",
            prevention_hint="QA 체크리스트에 회귀 시나리오 항목 추가",
            severity=SEVERITY_HIGH,
            evidence_count=1,
            source_kind=SOURCE_POSTMORTEM,
        )
        self.assertEqual(candidate.evidence_count, 1)
        self.assertEqual(candidate.source_kind, SOURCE_POSTMORTEM)
        self.assertEqual(
            candidate.future_hook_id,
            slugify_hook_id("qa-engineer", "regression_check_skipped"),
        )

    def test_postmortem_id_matches_record_id(self) -> None:
        # Verify deterministic alignment between the postmortem-driven
        # path and the ledger-driven path so the live wiring later can
        # dedupe candidates without consulting both paths.
        postmortem = promote_postmortem_to_hook_candidate(
            role_id="devops",
            mistake_key="ci:protected_branch_blocked",
            summary="x",
            prevention_hint="y",
        )
        extra = None
        for i in range(3):
            extra, record = record_mistake(
                extra,
                role_id="devops",
                mistake_key="ci:protected_branch_blocked",
                summary="x",
                prevention_hint="y",
                severity=SEVERITY_HIGH,
                source_kind=SOURCE_BLOCKED_COMPLETION,
                when=f"2026-05-10T00:0{i}:00+00:00",
            )
        from_record = promote_record_to_hook_candidate(record, min_evidence=2)
        assert from_record is not None
        self.assertEqual(postmortem.future_hook_id, from_record.future_hook_id)

    def test_blank_role_or_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            promote_postmortem_to_hook_candidate(
                role_id="", mistake_key="x", summary="s",
                prevention_hint="p",
            )


class CollectAndRenderTests(unittest.TestCase):
    def _populate(self):
        extra = None
        for i in range(4):
            extra, _ = record_mistake(
                extra,
                role_id="devops",
                mistake_key="ci:protected_branch_blocked",
                summary="x",
                prevention_hint="y",
                severity=SEVERITY_HIGH,
                when=f"2026-05-09T00:0{i}:00+00:00",
            )
        for i in range(2):
            extra, _ = record_mistake(
                extra,
                role_id="qa-engineer",
                mistake_key="regression_skip",
                summary="x",
                prevention_hint="y",
                severity=SEVERITY_MEDIUM,
                when=f"2026-05-10T00:0{i}:00+00:00",
            )
        # Single occurrence — should NOT promote.
        extra, _ = record_mistake(
            extra,
            role_id="backend-engineer",
            mistake_key="forgot_to_run_tests",
            summary="x",
            prevention_hint="y",
            when="2026-05-10T01:00:00+00:00",
        )
        return read_mistake_ledger(extra)

    def test_collect_filters_and_ranks(self) -> None:
        records = self._populate()
        candidates = collect_hook_candidates(records, min_evidence=2)
        ids = [c.future_hook_id for c in candidates]
        self.assertEqual(len(candidates), 2)
        # Ranked by evidence_count desc — devops 4x first.
        self.assertEqual(
            ids[0],
            slugify_hook_id("devops", "ci:protected_branch_blocked"),
        )
        self.assertEqual(
            ids[1], slugify_hook_id("qa-engineer", "regression_skip"),
        )

    def test_render_block_empty_for_no_candidates(self) -> None:
        self.assertEqual(render_hook_candidate_block(()), "")

    def test_render_block_lists_each_candidate(self) -> None:
        candidates = collect_hook_candidates(self._populate(), min_evidence=2)
        block = render_hook_candidate_block(candidates)
        for candidate in candidates:
            self.assertIn(candidate.future_hook_id, block)


if __name__ == "__main__":
    unittest.main()
