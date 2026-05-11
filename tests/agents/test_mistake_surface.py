"""mistake_surface — issue #81 round 1.

Pin the operator-readable surface that joins:

  * per-role mistake summaries
  * preflight advisory (optional)
  * hook candidates

The surface is the seam runtime/status renderers and completion
metadata splice into; this module verifies the contract is small,
deterministic, and "does not write when there is nothing to say".
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.lifecycle.mistake_ledger import (
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    record_mistake,
)
from yule_orchestrator.agents.lifecycle.mistake_surface import (
    build_operator_surface,
    render_operator_surface_block,
)
from yule_orchestrator.agents.lifecycle.preflight_judgement import (
    PREFLIGHT_BLOCK,
    PREFLIGHT_PASS,
    evaluate_preflight,
)


def _populate(extra=None, *, role, key, times, severity=SEVERITY_LOW):
    base = extra
    for i in range(times):
        base, _ = record_mistake(
            base,
            role_id=role,
            mistake_key=key,
            summary="x",
            prevention_hint="y",
            severity=severity,
            when=f"2026-05-10T00:{i:02d}:00+00:00",
        )
    return base


class BuildSurfaceTests(unittest.TestCase):
    def test_empty_ledger_is_empty_surface(self) -> None:
        surface = build_operator_surface({})
        self.assertTrue(surface.is_empty())
        self.assertEqual(surface.summaries, ())
        self.assertEqual(surface.hook_candidates, ())

    def test_passing_preflight_does_not_make_surface_signal(self) -> None:
        # No mistakes recorded → preflight passes → surface stays empty.
        preflight = evaluate_preflight(
            {}, role_id="devops", action="coding_execute"
        )
        self.assertEqual(preflight.verdict, PREFLIGHT_PASS)
        surface = build_operator_surface({}, preflight=preflight)
        self.assertTrue(surface.is_empty())

    def test_summaries_grouped_per_role(self) -> None:
        extra = _populate(role="devops", key="a", times=2)
        extra = _populate(role="qa-engineer", key="b", times=1, extra=extra)
        surface = build_operator_surface(extra)
        self.assertEqual(
            sorted(s.role_id for s in surface.summaries),
            ["devops", "qa-engineer"],
        )

    def test_hook_candidates_promoted_when_recurring(self) -> None:
        extra = _populate(
            role="devops",
            key="ci:protected_branch",
            times=3,
            severity=SEVERITY_HIGH,
        )
        extra = _populate(
            role="qa-engineer", key="single_mistake", times=1, extra=extra
        )
        surface = build_operator_surface(extra)
        ids = [c.future_hook_id for c in surface.hook_candidates]
        # Recurring devops mistake promoted, single qa mistake not.
        self.assertEqual(len(ids), 1)
        self.assertIn("devops", ids[0])
        self.assertIn("ci-protected-branch", ids[0])

    def test_preflight_block_carried_through(self) -> None:
        extra = _populate(
            role="devops", key="ci:lint", times=5,
        )
        preflight = evaluate_preflight(
            extra, role_id="devops", action="coding_execute"
        )
        self.assertEqual(preflight.verdict, PREFLIGHT_BLOCK)
        surface = build_operator_surface(extra, preflight=preflight)
        self.assertIsNotNone(surface.preflight)
        assert surface.preflight is not None
        self.assertTrue(surface.preflight.is_block())


class RenderTests(unittest.TestCase):
    def test_empty_surface_renders_empty_string(self) -> None:
        surface = build_operator_surface({})
        self.assertEqual(render_operator_surface_block(surface), "")

    def test_render_includes_role_summary_and_top_recurring(self) -> None:
        extra = _populate(role="devops", key="ci:lint", times=3)
        surface = build_operator_surface(extra)
        block = render_operator_surface_block(surface)
        self.assertIn("devops", block)
        self.assertIn("ci:lint", block)
        self.assertIn("3", block)  # occurrence count surfaces somewhere

    def test_render_includes_preflight_when_signal(self) -> None:
        extra = _populate(role="devops", key="ci:lint", times=3)
        preflight = evaluate_preflight(
            extra, role_id="devops", action="coding_execute"
        )
        surface = build_operator_surface(extra, preflight=preflight)
        block = render_operator_surface_block(surface)
        self.assertIn("preflight", block)
        self.assertIn("coding_execute", block)

    def test_render_includes_hook_candidate_section(self) -> None:
        extra = _populate(
            role="devops",
            key="ci:protected_branch",
            times=3,
            severity=SEVERITY_HIGH,
        )
        surface = build_operator_surface(extra)
        block = render_operator_surface_block(surface)
        self.assertIn("hook 후보", block)
        self.assertIn("preflight-devops-ci-protected-branch", block)


class PayloadTests(unittest.TestCase):
    def test_to_payload_round_trip_shape(self) -> None:
        extra = _populate(
            role="devops",
            key="ci:lint",
            times=3,
            severity=SEVERITY_MEDIUM,
        )
        preflight = evaluate_preflight(
            extra, role_id="devops", action="coding_execute"
        )
        surface = build_operator_surface(extra, preflight=preflight)
        payload = surface.to_payload()
        self.assertIn("summaries", payload)
        self.assertIn("preflight", payload)
        self.assertIn("hook_candidates", payload)
        # Preflight payload nests the verdict.
        self.assertEqual(
            payload["preflight"]["verdict"], preflight.verdict
        )
        # Each hook candidate has its deterministic id.
        for candidate in payload["hook_candidates"]:
            self.assertTrue(candidate["future_hook_id"].startswith("preflight-"))


class SummaryLineTests(unittest.TestCase):
    def test_empty_surface_is_empty_string(self) -> None:
        surface = build_operator_surface({})
        self.assertEqual(surface.summary_line(), "")

    def test_passing_preflight_does_not_show_clause(self) -> None:
        # Empty ledger + passing preflight → summary line is empty.
        preflight = evaluate_preflight(
            {}, role_id="devops", action="coding_execute"
        )
        surface = build_operator_surface({}, preflight=preflight)
        self.assertEqual(surface.summary_line(), "")

    def test_summary_includes_role_count_and_occurrences(self) -> None:
        extra = _populate(role="devops", key="ci:lint", times=3)
        extra = _populate(role="qa-engineer", key="b", times=2, extra=extra)
        surface = build_operator_surface(extra)
        line = surface.summary_line()
        self.assertIn("역할 2건", line)
        self.assertIn("총 5회", line)

    def test_summary_includes_preflight_verdict_when_signal(self) -> None:
        extra = _populate(role="devops", key="ci:lint", times=5)
        preflight = evaluate_preflight(
            extra, role_id="devops", action="coding_execute"
        )
        surface = build_operator_surface(extra, preflight=preflight)
        line = surface.summary_line()
        self.assertIn("preflight=block", line)

    def test_summary_includes_hook_candidate_count(self) -> None:
        extra = _populate(
            role="devops",
            key="ci:protected_branch",
            times=3,
            severity=SEVERITY_HIGH,
        )
        surface = build_operator_surface(extra)
        line = surface.summary_line()
        self.assertIn("hook 후보 1건", line)

    def test_payload_carries_summary_line(self) -> None:
        extra = _populate(role="devops", key="ci:lint", times=3)
        surface = build_operator_surface(extra)
        payload = surface.to_payload()
        self.assertEqual(payload["summary_line"], surface.summary_line())


if __name__ == "__main__":
    unittest.main()
