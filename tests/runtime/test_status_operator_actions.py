"""Operator action surface + compact view — Round 4 마무리 of #73.

Pin the contract that:

  * :func:`summarize_operator_actions` projects a
    :class:`RuntimeStatusReport` into a sorted tuple of
    :class:`OperatorAction` rows the operator should act on (high
    severity first), distinguishing stalled discussion / failed CI /
    waiting approval / blocked / lock contention / circuit_open /
    stale service categories.
  * :func:`render_runtime_status_compact` produces a deterministic
    short digest (≤6 lines) that is safe to log every supervisor
    tick.
  * The text + JSON renderers expose the new operator-action
    section + compact payload alongside the existing fields.
  * The autonomy markdown renderer surfaces an Operator actions
    section above the producer / funnel sections so the most
    urgent next-step is visible above the fold.
"""

from __future__ import annotations

import json
import unittest
from typing import Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.runtime.status import (
    ACTION_KIND_AUTONOMY_ERROR,
    ACTION_KIND_BLOCKED,
    ACTION_KIND_CIRCUIT_OPEN,
    ACTION_KIND_FAILED_TERMINAL,
    ACTION_KIND_LOCK_CONTENTION,
    ACTION_KIND_NEEDS_APPROVAL,
    ACTION_KIND_RETRY_READY_BACKLOG,
    ACTION_KIND_STALE_SERVICE,
    ACTION_KIND_UNKNOWN_SERVICE,
    AUTONOMY_OUTCOME_DISPATCHED,
    AUTONOMY_OUTCOME_LOCKED,
    AutonomyDispatchSummary,
    AutonomyTickSummary,
    CompactStatusSummary,
    CompletionFunnelSummary,
    FailedJobSummary,
    HEALTH_ALIVE,
    HEALTH_CIRCUIT_OPEN,
    HEALTH_STALE,
    HEALTH_UNKNOWN,
    OPERATOR_ACTION_HIGH,
    OPERATOR_ACTION_LOW,
    OPERATOR_ACTION_MEDIUM,
    OperatorAction,
    RuntimeStatusReport,
    ServiceStatus,
    build_compact_status_summary,
    render_autonomy_summary_markdown,
    render_runtime_status_compact,
    render_runtime_status_json,
    render_runtime_status_text,
    summarize_operator_actions,
)


def _service(
    service_id: str,
    *,
    health: str = HEALTH_ALIVE,
    implemented: bool = True,
) -> ServiceStatus:
    return ServiceStatus(
        service_id=service_id,
        kind="research_worker",
        role=None,
        description=service_id,
        implemented=implemented,
        health=health,
        heartbeat_age_seconds=0.5,
        heartbeat_last_beat=None,
        pid=None,
        metadata={},
        job_type=None,
    )


def _funnel(
    *,
    session_id: str,
    completion_status: str,
    job_id: str = "j",
    reason: str = "",
    job_type: str = "coding_execute",
) -> CompletionFunnelSummary:
    return CompletionFunnelSummary(
        session_id=session_id,
        job_id=job_id,
        job_type=job_type,
        completion_status=completion_status,
        ticked=False,
        reason=reason,
    )


def _failed_terminal_job(job_id: str = "job-x") -> FailedJobSummary:
    return FailedJobSummary(
        job_id=job_id,
        job_type="role_take",
        role="tech-lead",
        state="failed_terminal",
        attempt=2,
        age_seconds=120.0,
        error="terminal-explosion",
    )


def _report(
    *,
    services: Sequence[ServiceStatus] = (),
    autonomy_recent: Sequence[AutonomyTickSummary] = (),
    completion_funnel_recent: Sequence[CompletionFunnelSummary] = (),
    failed_recent: Sequence[FailedJobSummary] = (),
    locks_held: Sequence[str] = (),
) -> RuntimeStatusReport:
    return RuntimeStatusReport(
        profile="engineering",
        generated_at=1_731_000_000.0,
        deadline_seconds=90.0,
        services=tuple(services),
        job_types=(),
        failed_recent=tuple(failed_recent),
        warnings=(),
        autonomy_recent=tuple(autonomy_recent),
        completion_funnel_recent=tuple(completion_funnel_recent),
        autonomy_locks_held=tuple(locks_held),
    )


# ---------------------------------------------------------------------------
# summarize_operator_actions
# ---------------------------------------------------------------------------


class SummarizeOperatorActionsTests(unittest.TestCase):
    def test_clean_report_returns_no_actions(self) -> None:
        report = _report(services=(_service("eng-research"),))
        self.assertEqual(summarize_operator_actions(report), ())

    def test_needs_approval_is_high_priority_with_reply_step(self) -> None:
        report = _report(
            services=(_service("eng-research"),),
            completion_funnel_recent=(
                _funnel(session_id="sess-A", completion_status="needs_approval"),
            ),
        )
        actions = summarize_operator_actions(report)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.kind, ACTION_KIND_NEEDS_APPROVAL)
        self.assertEqual(action.severity, OPERATOR_ACTION_HIGH)
        self.assertIn("sess-A", action.affected)
        self.assertIn("이대로 저장", action.next_step)
        self.assertIn("승인-대기", action.headline + action.next_step)

    def test_blocked_surfaces_reasons_and_review_step(self) -> None:
        report = _report(
            services=(_service("eng-research"),),
            completion_funnel_recent=(
                _funnel(
                    session_id="sess-blk",
                    completion_status="blocked",
                    reason="protected_branch_blocked",
                ),
                _funnel(
                    session_id="sess-blk-2",
                    completion_status="blocked",
                    reason="ci_failed_terminal",
                ),
            ),
        )
        actions = summarize_operator_actions(report)
        kinds = [a.kind for a in actions]
        self.assertIn(ACTION_KIND_BLOCKED, kinds)
        blocked = next(a for a in actions if a.kind == ACTION_KIND_BLOCKED)
        self.assertEqual(blocked.severity, OPERATOR_ACTION_HIGH)
        self.assertIn("protected_branch_blocked", blocked.headline)
        self.assertIn("ci_failed_terminal", blocked.headline)
        self.assertIn("session.extra", blocked.next_step)

    def test_circuit_open_high_priority_with_reset_command(self) -> None:
        report = _report(
            services=(_service("eng-supervisor", health=HEALTH_CIRCUIT_OPEN),),
        )
        actions = summarize_operator_actions(report)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.kind, ACTION_KIND_CIRCUIT_OPEN)
        self.assertEqual(action.severity, OPERATOR_ACTION_HIGH)
        self.assertIn("yule runtime circuit reset eng-supervisor", action.next_step)

    def test_stale_service_lists_restart_options(self) -> None:
        report = _report(
            services=(_service("eng-role-tech-lead", health=HEALTH_STALE),),
        )
        actions = summarize_operator_actions(report)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.kind, ACTION_KIND_STALE_SERVICE)
        self.assertEqual(action.severity, OPERATOR_ACTION_HIGH)
        self.assertIn("yule run-service eng-role-tech-lead", action.next_step)
        self.assertIn("systemctl restart", action.next_step)

    def test_unknown_service_only_when_implemented(self) -> None:
        # Reserved (unimplemented) services should NOT generate an action.
        report = _report(
            services=(
                _service("eng-research", health=HEALTH_UNKNOWN, implemented=True),
                _service(
                    "eng-future-thing", health=HEALTH_UNKNOWN, implemented=False
                ),
            ),
        )
        actions = summarize_operator_actions(report)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.kind, ACTION_KIND_UNKNOWN_SERVICE)
        self.assertIn("eng-research", action.affected)
        self.assertNotIn("eng-future-thing", action.affected)

    def test_failed_terminal_jobs_surfaces_inspect_step(self) -> None:
        report = _report(
            services=(_service("eng-research"),),
            failed_recent=(_failed_terminal_job("job-7"),),
        )
        actions = summarize_operator_actions(report)
        kinds = [a.kind for a in actions]
        self.assertIn(ACTION_KIND_FAILED_TERMINAL, kinds)
        failed = next(a for a in actions if a.kind == ACTION_KIND_FAILED_TERMINAL)
        self.assertEqual(failed.severity, OPERATOR_ACTION_HIGH)
        self.assertIn("job-7", failed.affected)
        self.assertIn("yule runtime status --json", failed.next_step)

    def test_autonomy_error_is_medium_priority(self) -> None:
        report = _report(
            services=(_service("eng-research"),),
            autonomy_recent=(
                AutonomyTickSummary(
                    tick_id="tick-bad",
                    started_at="t",
                    finished_at="t",
                    next_task_source=None,
                    summary_line="",
                    error="selector_failed",
                ),
            ),
        )
        actions = summarize_operator_actions(report)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.kind, ACTION_KIND_AUTONOMY_ERROR)
        self.assertEqual(action.severity, OPERATOR_ACTION_MEDIUM)
        self.assertIn("tick-bad", action.affected)
        self.assertIn("journalctl", action.next_step)

    def test_lock_contention_medium_priority_lists_unique_scopes(self) -> None:
        report = _report(
            services=(_service("eng-research"),),
            autonomy_recent=(
                AutonomyTickSummary(
                    tick_id="tick-1",
                    started_at="t",
                    finished_at="t",
                    next_task_source=None,
                    summary_line="",
                    dispatches=(
                        AutonomyDispatchSummary(
                            source="approved_coding_job",
                            outcome=AUTONOMY_OUTCOME_LOCKED,
                            session_id="sess-lock",
                        ),
                        AutonomyDispatchSummary(
                            source="approved_coding_job",
                            outcome=AUTONOMY_OUTCOME_LOCKED,
                            session_id="sess-lock",  # same scope twice
                        ),
                    ),
                ),
            ),
        )
        actions = summarize_operator_actions(report)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.kind, ACTION_KIND_LOCK_CONTENTION)
        self.assertEqual(action.severity, OPERATOR_ACTION_MEDIUM)
        # Two dispatches but only one unique scope.
        self.assertIn("2 dispatch(es)", action.headline)
        self.assertEqual(action.affected, ("sess-lock",))

    def test_retry_ready_backlog_low_priority_when_three_or_more(self) -> None:
        # Two retry_ready rows should NOT trigger an action (transient
        # CI flap is fine). Three or more does.
        report_two = _report(
            services=(_service("eng-research"),),
            completion_funnel_recent=tuple(
                _funnel(session_id=f"s-{i}", completion_status="retry_ready")
                for i in range(2)
            ),
        )
        self.assertNotIn(
            ACTION_KIND_RETRY_READY_BACKLOG,
            [a.kind for a in summarize_operator_actions(report_two)],
        )
        report_three = _report(
            services=(_service("eng-research"),),
            completion_funnel_recent=tuple(
                _funnel(session_id=f"s-{i}", completion_status="retry_ready")
                for i in range(3)
            ),
        )
        actions = summarize_operator_actions(report_three)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.kind, ACTION_KIND_RETRY_READY_BACKLOG)
        self.assertEqual(action.severity, OPERATOR_ACTION_LOW)

    def test_distinguishes_failed_ci_vs_waiting_approval_vs_blocked(self) -> None:
        # All three signals coexist — must surface as three distinct
        # action rows, not a generic "stuck" bucket.
        report = _report(
            services=(_service("eng-research"),),
            completion_funnel_recent=(
                _funnel(
                    session_id="s-app",
                    completion_status="needs_approval",
                ),
                _funnel(
                    session_id="s-blk",
                    completion_status="blocked",
                    reason="protected_branch_blocked",
                ),
                _funnel(session_id="s-r1", completion_status="retry_ready"),
                _funnel(session_id="s-r2", completion_status="retry_ready"),
                _funnel(session_id="s-r3", completion_status="retry_ready"),
            ),
        )
        actions = summarize_operator_actions(report)
        kinds = [a.kind for a in actions]
        self.assertIn(ACTION_KIND_NEEDS_APPROVAL, kinds)
        self.assertIn(ACTION_KIND_BLOCKED, kinds)
        self.assertIn(ACTION_KIND_RETRY_READY_BACKLOG, kinds)
        # high severity items must come before the low severity backlog.
        backlog_idx = kinds.index(ACTION_KIND_RETRY_READY_BACKLOG)
        approval_idx = kinds.index(ACTION_KIND_NEEDS_APPROVAL)
        blocked_idx = kinds.index(ACTION_KIND_BLOCKED)
        self.assertLess(approval_idx, backlog_idx)
        self.assertLess(blocked_idx, backlog_idx)

    def test_action_to_payload_round_trip(self) -> None:
        action = OperatorAction(
            kind=ACTION_KIND_NEEDS_APPROVAL,
            severity=OPERATOR_ACTION_HIGH,
            headline="x",
            next_step="reply foo",
            affected=("a", "b"),
            icon="🙋",
        )
        payload = action.to_payload()
        self.assertEqual(payload["kind"], ACTION_KIND_NEEDS_APPROVAL)
        self.assertEqual(payload["severity"], OPERATOR_ACTION_HIGH)
        self.assertEqual(payload["affected"], ["a", "b"])
        self.assertEqual(payload["icon"], "🙋")


# ---------------------------------------------------------------------------
# render_runtime_status_compact + build_compact_status_summary
# ---------------------------------------------------------------------------


class CompactSummaryTests(unittest.TestCase):
    def test_clean_report_marks_summary_clean(self) -> None:
        report = _report(services=(_service("eng-research"),))
        compact = build_compact_status_summary(report)
        self.assertIsInstance(compact, CompactStatusSummary)
        self.assertEqual(compact.services_alive, 1)
        self.assertEqual(compact.services_stale, 0)
        self.assertTrue(compact.is_clean())
        self.assertIsNone(compact.top_action)

    def test_compact_text_under_seven_lines(self) -> None:
        report = _report(
            services=(
                _service("eng-research"),
                _service("eng-stale", health=HEALTH_STALE),
            ),
            completion_funnel_recent=(
                _funnel(session_id="sess-A", completion_status="needs_approval"),
                _funnel(session_id="sess-B", completion_status="blocked"),
                _funnel(session_id="sess-C", completion_status="done"),
            ),
            autonomy_recent=(
                AutonomyTickSummary(
                    tick_id="tick-bad",
                    started_at="t",
                    finished_at="t",
                    next_task_source=None,
                    summary_line="",
                    error="boom",
                ),
            ),
        )
        text = render_runtime_status_compact(report)
        lines = text.split("\n")
        self.assertLessEqual(len(lines), 6)
        # First line: profile + timestamp.
        self.assertTrue(lines[0].startswith("🛰 runtime[engineering]"))
        # Counters surface alive / stale / unknown / circuit.
        self.assertIn("alive", lines[1])
        self.assertIn("1 stale", lines[1])
        # Funnel counter line distinguishes the three categories.
        self.assertIn("1 needs_approval", lines[4])
        self.assertIn("1 blocked", lines[4])
        # Top action is the highest-severity row + "+N more" suffix.
        self.assertIn("[high]", lines[5])
        self.assertIn("more)", lines[5])

    def test_compact_text_clean_state_announces_no_action(self) -> None:
        report = _report(services=(_service("eng-research"),))
        text = render_runtime_status_compact(report)
        self.assertIn("no operator action required", text)

    def test_compact_summary_counters_match_funnel_distribution(self) -> None:
        report = _report(
            services=(_service("eng-research"),),
            completion_funnel_recent=(
                _funnel(session_id="s1", completion_status="done"),
                _funnel(session_id="s2", completion_status="done"),
                _funnel(session_id="s3", completion_status="retry_ready"),
                _funnel(session_id="s4", completion_status="needs_approval"),
                _funnel(session_id="s5", completion_status="blocked"),
            ),
        )
        compact = build_compact_status_summary(report)
        self.assertEqual(compact.funnel_done, 2)
        self.assertEqual(compact.funnel_retry_ready, 1)
        self.assertEqual(compact.funnel_needs_approval, 1)
        self.assertEqual(compact.funnel_blocked, 1)


# ---------------------------------------------------------------------------
# Renderer integration
# ---------------------------------------------------------------------------


class RendererIntegrationTests(unittest.TestCase):
    def test_text_render_includes_operator_action_section(self) -> None:
        report = _report(
            services=(_service("eng-research"),),
            completion_funnel_recent=(
                _funnel(session_id="sess-A", completion_status="needs_approval"),
            ),
        )
        text = render_runtime_status_text(report)
        self.assertIn("operator actions:", text)
        # action lines carry both severity + the next_step on a sub-line
        self.assertIn("[high]", text)
        self.assertIn("이대로 저장", text)

    def test_text_render_announces_no_action_when_clean(self) -> None:
        report = _report(services=(_service("eng-research"),))
        text = render_runtime_status_text(report)
        self.assertIn("operator actions:", text)
        self.assertIn("no operator action required", text)

    def test_json_render_includes_actions_and_compact(self) -> None:
        report = _report(
            services=(_service("eng-research", health=HEALTH_STALE),),
            completion_funnel_recent=(
                _funnel(session_id="sess-A", completion_status="blocked"),
            ),
        )
        blob = render_runtime_status_json(report)
        payload = json.loads(blob)
        self.assertIn("operator_actions", payload)
        self.assertIn("compact", payload)
        kinds = {a["kind"] for a in payload["operator_actions"]}
        self.assertIn(ACTION_KIND_STALE_SERVICE, kinds)
        self.assertIn(ACTION_KIND_BLOCKED, kinds)
        compact = payload["compact"]
        self.assertEqual(compact["services_stale"], 1)
        self.assertEqual(compact["funnel_blocked"], 1)
        self.assertFalse(compact["is_clean"])
        self.assertIsNotNone(compact["top_action"])
        self.assertIn(compact["top_action"]["kind"], kinds)

    def test_autonomy_markdown_surfaces_operator_actions_first(self) -> None:
        report = _report(
            services=(_service("eng-research"),),
            completion_funnel_recent=(
                _funnel(session_id="sess-A", completion_status="needs_approval"),
                _funnel(
                    session_id="sess-B",
                    completion_status="blocked",
                    reason="ci_failed",
                ),
            ),
        )
        md = render_autonomy_summary_markdown(report)
        self.assertIn("### Operator actions", md)
        # Operator actions section must come before the funnel section.
        self.assertLess(
            md.index("### Operator actions"),
            md.index("### Completion funnel"),
        )
        # next_step must appear in the markdown so the operator can copy it.
        self.assertIn("이대로 저장", md)
        self.assertIn("session.extra", md)

    def test_autonomy_markdown_remains_empty_for_fully_healthy_runtime(self) -> None:
        report = _report(services=(_service("eng-research"),))
        md = render_autonomy_summary_markdown(report)
        self.assertEqual(md, "")


if __name__ == "__main__":
    unittest.main()
