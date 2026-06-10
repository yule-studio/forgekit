"""preflight_judgement — issue #81 round 1.

Pin the preflight seam contract:

  * No mistakes → ``PREFLIGHT_PASS`` (no signal).
  * 2x recurring → ``PREFLIGHT_ADVISORY``.
  * 3x recurring → ``PREFLIGHT_WARNING``.
  * 5x recurring → ``PREFLIGHT_BLOCK`` (any severity).
  * High severity + 3x → ``PREFLIGHT_BLOCK`` (escalates earlier).
  * ``only_keys`` filter limits which mistakes are evaluated.
  * The advisory's checklist surfaces every prevention hint, sorted
    by recurrence count descending.
"""

from __future__ import annotations

import unittest
from typing import Any, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.lifecycle.mistake_ledger import (
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    record_mistake,
)
from yule_engineering.agents.lifecycle.preflight_judgement import (
    PREFLIGHT_ADVISORY,
    PREFLIGHT_BLOCK,
    PREFLIGHT_PASS,
    PREFLIGHT_WARNING,
    PreflightThresholds,
    evaluate_preflight,
    render_preflight_advisory_block,
)


def _populate(
    *,
    role: str,
    key: str,
    times: int,
    severity: str = SEVERITY_LOW,
    extra: Optional[Mapping[str, Any]] = None,
    summary: str = "x",
    prevention_hint: str = "다음에는 점검 후 진입",
) -> Mapping[str, Any]:
    base = extra
    for i in range(times):
        base, _ = record_mistake(
            base,
            role_id=role,
            mistake_key=key,
            summary=summary,
            prevention_hint=prevention_hint,
            severity=severity,
            when=f"2026-05-10T00:{i:02d}:00+00:00",
        )
    return base  # type: ignore[return-value]


class VerdictThresholdTests(unittest.TestCase):
    def test_no_mistakes_passes(self) -> None:
        advisory = evaluate_preflight(
            None, role_id="devops", action="coding_execute"
        )
        self.assertEqual(advisory.verdict, PREFLIGHT_PASS)
        self.assertFalse(advisory.has_signal())
        self.assertEqual(advisory.checklist, ())

    def test_two_occurrences_is_advisory(self) -> None:
        extra = _populate(role="devops", key="missing_lint", times=2)
        advisory = evaluate_preflight(
            extra, role_id="devops", action="coding_execute"
        )
        self.assertEqual(advisory.verdict, PREFLIGHT_ADVISORY)
        self.assertEqual(len(advisory.triggered_mistakes), 1)
        self.assertEqual(len(advisory.checklist), 1)

    def test_three_occurrences_is_warning(self) -> None:
        extra = _populate(role="qa-engineer", key="regression_skip", times=3)
        advisory = evaluate_preflight(
            extra, role_id="qa-engineer", action="discussion_handoff"
        )
        self.assertEqual(advisory.verdict, PREFLIGHT_WARNING)

    def test_five_occurrences_blocks(self) -> None:
        extra = _populate(role="devops", key="ci:lint", times=5)
        advisory = evaluate_preflight(
            extra, role_id="devops", action="coding_execute"
        )
        self.assertEqual(advisory.verdict, PREFLIGHT_BLOCK)
        self.assertTrue(advisory.is_block())

    def test_high_severity_blocks_at_three(self) -> None:
        extra = _populate(
            role="devops",
            key="ci:protected_branch_blocked",
            times=3,
            severity=SEVERITY_HIGH,
        )
        advisory = evaluate_preflight(
            extra, role_id="devops", action="coding_execute"
        )
        self.assertEqual(advisory.verdict, PREFLIGHT_BLOCK)

    def test_high_severity_below_threshold_is_advisory(self) -> None:
        # 2x high severity falls into advisory bucket, not block.
        extra = _populate(
            role="devops",
            key="ci:secret_lookup_failed",
            times=2,
            severity=SEVERITY_HIGH,
        )
        advisory = evaluate_preflight(
            extra, role_id="devops", action="coding_execute"
        )
        self.assertEqual(advisory.verdict, PREFLIGHT_ADVISORY)

    def test_only_keys_scopes_evaluation(self) -> None:
        extra = _populate(
            role="devops", key="ci:protected_branch", times=5
        )
        extra = _populate(
            role="devops",
            key="missing_regression_check",
            times=5,
            extra=extra,
        )
        advisory = evaluate_preflight(
            extra,
            role_id="devops",
            action="coding_execute",
            only_keys=("ci:protected_branch",),
        )
        self.assertEqual(advisory.verdict, PREFLIGHT_BLOCK)
        self.assertEqual(
            tuple(m.mistake_key for m in advisory.triggered_mistakes),
            ("ci:protected_branch",),
        )

    def test_other_role_is_ignored(self) -> None:
        extra = _populate(role="devops", key="ci:lint", times=10)
        advisory = evaluate_preflight(
            extra, role_id="qa-engineer", action="discussion_handoff"
        )
        self.assertEqual(advisory.verdict, PREFLIGHT_PASS)

    def test_blank_role_is_safe_pass(self) -> None:
        advisory = evaluate_preflight(
            {}, role_id="", action="coding_execute"
        )
        self.assertEqual(advisory.verdict, PREFLIGHT_PASS)


class CustomThresholdsTests(unittest.TestCase):
    def test_thresholds_can_be_customised(self) -> None:
        extra = _populate(role="devops", key="x", times=2)
        thresholds = PreflightThresholds(
            advisory_at=2, warning_at=2, block_at=2,
        )
        advisory = evaluate_preflight(
            extra,
            role_id="devops",
            action="coding_execute",
            thresholds=thresholds,
        )
        self.assertEqual(advisory.verdict, PREFLIGHT_BLOCK)


class ChecklistShapeTests(unittest.TestCase):
    def test_checklist_sorted_by_recurrence_desc(self) -> None:
        extra = _populate(role="devops", key="b", times=2)
        extra = _populate(role="devops", key="a", times=4, extra=extra)
        advisory = evaluate_preflight(
            extra, role_id="devops", action="coding_execute"
        )
        keys = [r.mistake_key for r in advisory.triggered_mistakes]
        self.assertEqual(keys, ["a", "b"])

    def test_render_block_empty_when_pass(self) -> None:
        advisory = evaluate_preflight(
            None, role_id="devops", action="coding_execute"
        )
        self.assertEqual(render_preflight_advisory_block(advisory), "")

    def test_render_block_includes_keys_and_hint(self) -> None:
        extra = _populate(
            role="devops",
            key="ci:protected_branch",
            times=3,
            severity=SEVERITY_HIGH,
            prevention_hint="PR 흐름으로 push",
        )
        advisory = evaluate_preflight(
            extra, role_id="devops", action="coding_execute"
        )
        block = render_preflight_advisory_block(advisory)
        self.assertIn("ci:protected_branch", block)
        self.assertIn("PR 흐름", block)
        self.assertIn("preflight", block)


class PayloadShapeTests(unittest.TestCase):
    def test_payload_carries_keys_and_verdict(self) -> None:
        extra = _populate(role="devops", key="ci:lint", times=3)
        advisory = evaluate_preflight(
            extra, role_id="devops", action="coding_execute"
        )
        payload = advisory.to_payload()
        self.assertEqual(payload["verdict"], PREFLIGHT_WARNING)
        self.assertEqual(payload["role_id"], "devops")
        self.assertEqual(payload["action"], "coding_execute")
        self.assertEqual(payload["triggered_mistake_keys"], ["ci:lint"])
        self.assertEqual(len(payload["checklist"]), 1)


if __name__ == "__main__":
    unittest.main()
