"""mistake_ledger — issue #81 round 1.

Pin the role-specific mistake ledger contract:

  * ``record_mistake`` accumulates the same ``(role, key)`` instead
    of appending a duplicate row, and the original *extra* dict is
    not mutated.
  * Severity escalates one-way (low → medium → high); a milder later
    occurrence does not relax the recorded severity.
  * The ledger is bounded so a long-lived session row stays small.
  * Derivation helpers (``derive_mistake_from_completion`` and
    ``derive_mistake_from_ci_exhaustion``) emit kwargs the caller
    can hand straight to ``record_mistake``, and they no-op on
    surface-irrelevant inputs (success completion, missing role).
  * The summary projection groups by role and ranks the most
    recurring mistakes first.
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.completion_hook import (
    JobCompletionEvent,
)
from yule_engineering.agents.lifecycle.mistake_ledger import (
    SESSION_EXTRA_KEY,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SOURCE_BLOCKED_COMPLETION,
    SOURCE_CI_FAILURE,
    SOURCE_POSTMORTEM,
    derive_mistake_from_ci_exhaustion,
    derive_mistake_from_completion,
    mistakes_for_role,
    read_mistake_ledger,
    record_mistake,
    summarize_role_mistakes,
)


class RecordMistakeAccumulationTests(unittest.TestCase):
    def test_first_record_is_count_one(self) -> None:
        new_extra, record = record_mistake(
            None,
            role_id="devops",
            mistake_key="protected_branch_push",
            summary="main 브랜치 push 가 차단됨",
            prevention_hint="PR 흐름으로 push 금지 — 리베이스 후 PR 생성",
            source_kind=SOURCE_BLOCKED_COMPLETION,
            severity=SEVERITY_MEDIUM,
            when="2026-05-10T10:00:00+00:00",
        )
        self.assertEqual(record.occurrence_count, 1)
        self.assertEqual(record.first_seen_at, record.last_seen_at)
        ledger = read_mistake_ledger(new_extra)
        self.assertEqual(len(ledger), 1)

    def test_repeat_increments_count_and_advances_last_seen(self) -> None:
        extra, _ = record_mistake(
            None,
            role_id="qa-engineer",
            mistake_key="missing_regression_check",
            summary="회귀 체크 누락",
            prevention_hint="회귀 시나리오 포함 여부 점검",
            severity=SEVERITY_LOW,
            when="2026-05-09T10:00:00+00:00",
        )
        extra, second = record_mistake(
            extra,
            role_id="qa-engineer",
            mistake_key="missing_regression_check",
            summary="회귀 체크 누락 (두 번째 발생)",
            prevention_hint="회귀 시나리오 점검 강화",
            severity=SEVERITY_LOW,
            when="2026-05-10T10:00:00+00:00",
        )
        self.assertEqual(second.occurrence_count, 2)
        self.assertEqual(second.first_seen_at, "2026-05-09T10:00:00+00:00")
        self.assertEqual(second.last_seen_at, "2026-05-10T10:00:00+00:00")
        ledger = read_mistake_ledger(extra)
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0].occurrence_count, 2)

    def test_different_keys_create_distinct_rows(self) -> None:
        extra, _ = record_mistake(
            None,
            role_id="qa-engineer",
            mistake_key="missing_regression_check",
            summary="x",
            prevention_hint="y",
            when="2026-05-08T00:00:00+00:00",
        )
        extra, _ = record_mistake(
            extra,
            role_id="qa-engineer",
            mistake_key="flaky_test_ignored",
            summary="x",
            prevention_hint="y",
            when="2026-05-09T00:00:00+00:00",
        )
        ledger = read_mistake_ledger(extra)
        self.assertEqual(
            sorted(r.mistake_key for r in ledger),
            ["flaky_test_ignored", "missing_regression_check"],
        )

    def test_different_roles_have_independent_keys(self) -> None:
        extra, _ = record_mistake(
            None,
            role_id="devops",
            mistake_key="ci:lint_failure",
            summary="lint 실패",
            prevention_hint="lint 실행 후 push",
            when="2026-05-10T00:00:00+00:00",
        )
        extra, _ = record_mistake(
            extra,
            role_id="backend-engineer",
            mistake_key="ci:lint_failure",
            summary="lint 실패",
            prevention_hint="lint 실행 후 push",
            when="2026-05-10T00:01:00+00:00",
        )
        ledger = read_mistake_ledger(extra)
        self.assertEqual(len(ledger), 2)
        self.assertEqual(
            sorted(r.role_id for r in ledger),
            ["backend-engineer", "devops"],
        )

    def test_severity_only_escalates(self) -> None:
        extra, _ = record_mistake(
            None,
            role_id="devops",
            mistake_key="ci:protected_branch_blocked",
            summary="protected branch push 시도",
            prevention_hint="PR 흐름으로",
            severity=SEVERITY_HIGH,
            when="2026-05-09T00:00:00+00:00",
        )
        extra, second = record_mistake(
            extra,
            role_id="devops",
            mistake_key="ci:protected_branch_blocked",
            summary="protected branch 차단 (재발)",
            prevention_hint="PR 흐름으로",
            severity=SEVERITY_LOW,
            when="2026-05-10T00:00:00+00:00",
        )
        self.assertEqual(second.severity, SEVERITY_HIGH)

    def test_extra_input_is_not_mutated(self) -> None:
        original = {"foo": "bar"}
        new_extra, _ = record_mistake(
            original,
            role_id="devops",
            mistake_key="x",
            summary="y",
            prevention_hint="z",
            when="2026-05-10T00:00:00+00:00",
        )
        self.assertEqual(original, {"foo": "bar"})
        self.assertEqual(new_extra["foo"], "bar")
        self.assertIn(SESSION_EXTRA_KEY, new_extra)

    def test_ledger_capped_to_max_entries(self) -> None:
        extra: Optional[Mapping[str, Any]] = None
        for i in range(10):
            extra, _ = record_mistake(
                extra,
                role_id="devops",
                mistake_key=f"key_{i}",
                summary="s",
                prevention_hint="p",
                when=f"2026-05-10T00:{i:02d}:00+00:00",
                max_entries=3,
            )
        ledger = read_mistake_ledger(extra)
        self.assertEqual(len(ledger), 3)
        # Oldest entries dropped first.
        self.assertEqual(
            sorted(r.mistake_key for r in ledger),
            ["key_7", "key_8", "key_9"],
        )

    def test_blank_role_or_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            record_mistake(
                None,
                role_id="",
                mistake_key="x",
                summary="s",
                prevention_hint="p",
            )
        with self.assertRaises(ValueError):
            record_mistake(
                None,
                role_id="devops",
                mistake_key="",
                summary="s",
                prevention_hint="p",
            )


class MistakesForRoleTests(unittest.TestCase):
    def test_filters_by_role(self) -> None:
        extra, _ = record_mistake(
            None,
            role_id="devops",
            mistake_key="a",
            summary="x",
            prevention_hint="y",
            when="2026-05-10T00:00:00+00:00",
        )
        extra, _ = record_mistake(
            extra,
            role_id="qa-engineer",
            mistake_key="b",
            summary="x",
            prevention_hint="y",
            when="2026-05-10T00:01:00+00:00",
        )
        out = mistakes_for_role(extra, "devops")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].role_id, "devops")

    def test_unknown_role_returns_empty(self) -> None:
        self.assertEqual(mistakes_for_role({}, "ghost"), ())


class DerivationTests(unittest.TestCase):
    def test_completion_done_is_skipped(self) -> None:
        event = JobCompletionEvent(
            job_id="j", job_type="role_take", session_id="s",
            status="done", role="devops",
        )
        self.assertIsNone(derive_mistake_from_completion(event=event))

    def test_completion_blocked_with_role_emits_kwargs(self) -> None:
        event = JobCompletionEvent(
            job_id="j",
            job_type="coding_execute",
            session_id="s",
            status="blocked",
            reason="protected branch push refused",
            role="devops",
        )
        kwargs = derive_mistake_from_completion(event=event)
        assert kwargs is not None
        self.assertEqual(kwargs["role_id"], "devops")
        self.assertEqual(kwargs["source_kind"], SOURCE_BLOCKED_COMPLETION)
        self.assertIn("protected", kwargs["mistake_key"])

    def test_completion_blocked_without_role_returns_none(self) -> None:
        event = JobCompletionEvent(
            job_id="j", job_type="coding_execute", session_id="s",
            status="blocked", reason="x", role=None,
        )
        self.assertIsNone(derive_mistake_from_completion(event=event))

    def test_ci_exhaustion_marks_high_severity_after_three(self) -> None:
        kwargs = derive_mistake_from_ci_exhaustion(
            role_id="devops",
            failing_runs=("test", "lint"),
            pr_number=42,
            attempts=3,
        )
        assert kwargs is not None
        self.assertEqual(kwargs["severity"], SEVERITY_HIGH)
        self.assertEqual(kwargs["source_kind"], SOURCE_CI_FAILURE)
        self.assertTrue(kwargs["mistake_key"].startswith("ci:"))

    def test_ci_exhaustion_below_three_is_medium(self) -> None:
        kwargs = derive_mistake_from_ci_exhaustion(
            role_id="devops", failing_runs=("test",), attempts=1,
        )
        assert kwargs is not None
        self.assertEqual(kwargs["severity"], SEVERITY_MEDIUM)

    def test_ci_exhaustion_blank_role_returns_none(self) -> None:
        self.assertIsNone(
            derive_mistake_from_ci_exhaustion(
                role_id="", failing_runs=("test",), attempts=3,
            )
        )


class SummariseTests(unittest.TestCase):
    def _populate(self) -> Mapping[str, Any]:
        extra: Optional[Mapping[str, Any]] = None
        # devops: two distinct mistakes, one is high severity + recurring.
        for i in range(3):
            extra, _ = record_mistake(
                extra,
                role_id="devops",
                mistake_key="ci:protected_branch_blocked",
                summary="protected branch push refused",
                prevention_hint="PR 흐름",
                severity=SEVERITY_HIGH,
                source_kind=SOURCE_CI_FAILURE,
                when=f"2026-05-09T00:0{i}:00+00:00",
            )
        extra, _ = record_mistake(
            extra,
            role_id="devops",
            mistake_key="missing_secret_lookup",
            summary="env 누락",
            prevention_hint="env 점검",
            severity=SEVERITY_LOW,
            when="2026-05-10T00:00:00+00:00",
        )
        # qa-engineer: one mistake.
        extra, _ = record_mistake(
            extra,
            role_id="qa-engineer",
            mistake_key="missing_regression_check",
            summary="회귀 체크 누락",
            prevention_hint="회귀 시나리오 점검",
            severity=SEVERITY_MEDIUM,
            when="2026-05-08T00:00:00+00:00",
        )
        return extra  # type: ignore[return-value]

    def test_groups_per_role_and_ranks_top_recurring(self) -> None:
        summaries = summarize_role_mistakes(self._populate())
        by_role = {s.role_id: s for s in summaries}
        self.assertEqual(set(by_role), {"devops", "qa-engineer"})

        devops = by_role["devops"]
        self.assertEqual(devops.total_mistakes, 2)
        self.assertEqual(devops.high_severity_count, 1)
        self.assertEqual(devops.low_severity_count, 1)
        self.assertEqual(devops.total_occurrences, 4)
        # Top recurring leads with the high-severity x3 row.
        self.assertEqual(
            devops.top_recurring[0].mistake_key,
            "ci:protected_branch_blocked",
        )
        self.assertEqual(devops.top_recurring[0].occurrence_count, 3)

        qa = by_role["qa-engineer"]
        self.assertEqual(qa.total_mistakes, 1)
        self.assertEqual(qa.medium_severity_count, 1)


class PayloadRoundTripTests(unittest.TestCase):
    def test_record_can_be_payloaded_and_back(self) -> None:
        extra, original = record_mistake(
            None,
            role_id="devops",
            mistake_key="ci:lint",
            summary="lint",
            prevention_hint="lint 먼저",
            severity=SEVERITY_MEDIUM,
            source_kind=SOURCE_CI_FAILURE,
            when="2026-05-10T00:00:00+00:00",
        )
        payload = original.to_payload()
        # Mimic the SQLite round trip: dump → load → re-read.
        reloaded = read_mistake_ledger(
            {SESSION_EXTRA_KEY: [dict(payload)]}
        )
        self.assertEqual(len(reloaded), 1)
        self.assertEqual(reloaded[0], original)


if __name__ == "__main__":
    unittest.main()
