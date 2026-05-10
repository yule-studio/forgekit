"""Autonomy / completion-funnel surface in runtime.status — Round 4 of #73.

Pin the contract that:

  * :class:`RuntimeAutonomyJournal` projects a producer-report-shaped
    object into a status-friendly :class:`AutonomyTickSummary` and
    keeps a bounded ring of recent ticks.
  * :func:`build_runtime_status` reads from the journal and from the
    caller-provided completion-funnel rows so both surfaces flow into
    the same :class:`RuntimeStatusReport`.
  * :func:`render_autonomy_summary_markdown` produces stable section
    headers a Discord post can rely on, distinguishes
    blocked / needs_approval / retry_ready / locked / dispatched
    states, and stays empty when nothing operator-actionable is
    going on.
  * Warnings include the operator-relevant nudges for each state.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.store import JobQueue
from yule_orchestrator.runtime.status import (
    AUTONOMY_OUTCOME_DISPATCHED,
    AUTONOMY_OUTCOME_ERROR,
    AUTONOMY_OUTCOME_LOCKED,
    AutonomyDispatchSummary,
    AutonomyTickSummary,
    CompletionFunnelSummary,
    RuntimeAutonomyJournal,
    RuntimeStatusReport,
    build_runtime_status,
    render_autonomy_summary_markdown,
    render_runtime_status_json,
    render_runtime_status_text,
)


# ---------------------------------------------------------------------------
# Producer-report-shaped fakes (we don't import AutonomyProducerReport
# here so the journal's "liberal in what it accepts" projection is
# exercised explicitly).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeCandidate:
    source: str


@dataclass
class _FakeDispatch:
    source: str
    outcome: str
    session_id: str = ""
    executor_role: str = ""
    job_id: Optional[str] = None
    branch_hint: str = ""
    reason: str = ""


@dataclass
class _FakeReport:
    tick_id: str
    started_at: str
    finished_at: str
    next_task_candidate: Optional[_FakeCandidate]
    dispatches: Tuple[_FakeDispatch, ...]
    locks_held: Tuple[str, ...] = ()
    error: Optional[str] = None
    summary: str = ""

    def summary_line(self) -> str:
        return self.summary


# ---------------------------------------------------------------------------
# RuntimeAutonomyJournal
# ---------------------------------------------------------------------------


class JournalProjectionTests(unittest.TestCase):
    def test_record_projects_full_report_into_summary(self) -> None:
        journal = RuntimeAutonomyJournal(max_entries=4)
        report = _FakeReport(
            tick_id="tick-1",
            started_at="2026-05-10T00:00:00+00:00",
            finished_at="2026-05-10T00:00:01+00:00",
            next_task_candidate=_FakeCandidate(source="approved_coding_job"),
            dispatches=(
                _FakeDispatch(
                    source="approved_coding_job",
                    outcome=AUTONOMY_OUTCOME_DISPATCHED,
                    session_id="sess-A",
                    executor_role="backend-engineer",
                    job_id="job-9",
                    branch_hint="agent/backend/issue-1",
                ),
                _FakeDispatch(
                    source="unresolved_discussion",
                    outcome=AUTONOMY_OUTCOME_LOCKED,
                    session_id="sess-B",
                    reason="scope held: session:sess-B",
                ),
            ),
            locks_held=("session:sess-A",),
            summary="autonomy producer tick tick-1 approved=dispatched/backend",
        )
        summary = journal.record_report(report)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.tick_id, "tick-1")
        self.assertEqual(summary.next_task_source, "approved_coding_job")
        self.assertEqual(len(summary.dispatches), 2)
        self.assertEqual(summary.dispatches[0].outcome, AUTONOMY_OUTCOME_DISPATCHED)
        self.assertEqual(summary.dispatches[1].outcome, AUTONOMY_OUTCOME_LOCKED)
        self.assertEqual(summary.locks_held, ("session:sess-A",))
        self.assertTrue(summary.has_actionable_signal())  # has a LOCKED row

    def test_recent_returns_newest_first_and_caps_at_limit(self) -> None:
        journal = RuntimeAutonomyJournal(max_entries=8)
        for i in range(5):
            journal.record_report(
                _FakeReport(
                    tick_id=f"tick-{i}",
                    started_at="t",
                    finished_at="t",
                    next_task_candidate=None,
                    dispatches=(),
                )
            )
        recent = journal.recent(limit=3)
        self.assertEqual([t.tick_id for t in recent], ["tick-4", "tick-3", "tick-2"])

    def test_max_entries_drops_oldest(self) -> None:
        journal = RuntimeAutonomyJournal(max_entries=3)
        for i in range(6):
            journal.record_report(
                _FakeReport(
                    tick_id=f"tick-{i}",
                    started_at="t",
                    finished_at="t",
                    next_task_candidate=None,
                    dispatches=(),
                )
            )
        recent = journal.recent()
        self.assertEqual(
            [t.tick_id for t in recent], ["tick-5", "tick-4", "tick-3"]
        )

    def test_record_swallows_bad_object(self) -> None:
        journal = RuntimeAutonomyJournal()
        # An object that raises on every getattr — the journal must
        # NOT propagate the exception (the supervisor's last-resort
        # hook can't crash on a malformed report).
        class _Boom:
            def __getattribute__(self, item):  # noqa: D401
                raise RuntimeError("boom")

        result = journal.record_report(_Boom())
        self.assertIsNone(result)
        self.assertEqual(journal.recent(), ())

    def test_locks_held_reflects_latest_tick(self) -> None:
        journal = RuntimeAutonomyJournal()
        journal.record_report(
            _FakeReport(
                tick_id="t1",
                started_at="t",
                finished_at="t",
                next_task_candidate=None,
                dispatches=(),
                locks_held=("session:A",),
            )
        )
        journal.record_report(
            _FakeReport(
                tick_id="t2",
                started_at="t",
                finished_at="t",
                next_task_candidate=None,
                dispatches=(),
                locks_held=("branch:repo:main",),
            )
        )
        self.assertEqual(journal.locks_held(), ("branch:repo:main",))


# ---------------------------------------------------------------------------
# build_runtime_status — autonomy + funnel propagation
# ---------------------------------------------------------------------------


class _StatusBuilderFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=db)
        self.heartbeats = HeartbeatStore(db_path=db)


class BuildRuntimeStatusTests(_StatusBuilderFixture):
    def test_autonomy_recent_propagates_from_journal(self) -> None:
        journal = RuntimeAutonomyJournal()
        journal.record_report(
            _FakeReport(
                tick_id="tick-A",
                started_at="t",
                finished_at="t",
                next_task_candidate=_FakeCandidate(source="idle"),
                dispatches=(),
                summary="autonomy producer tick tick-A idle",
            )
        )
        report = build_runtime_status(
            queue=self.queue,
            heartbeats=self.heartbeats,
            autonomy_journal=journal,
        )
        self.assertEqual(len(report.autonomy_recent), 1)
        self.assertEqual(report.autonomy_recent[0].tick_id, "tick-A")

    def test_completion_funnel_recent_propagates(self) -> None:
        funnel = (
            CompletionFunnelSummary(
                session_id="sess-X",
                job_id="job-1",
                job_type="coding_execute",
                completion_status="needs_approval",
                ticked=False,
                reason="awaiting human reply",
                at="2026-05-10T00:00:00+00:00",
            ),
        )
        report = build_runtime_status(
            queue=self.queue,
            heartbeats=self.heartbeats,
            autonomy_journal=RuntimeAutonomyJournal(),
            completion_funnel_recent=funnel,
        )
        self.assertEqual(len(report.completion_funnel_recent), 1)
        self.assertEqual(
            report.completion_funnel_recent[0].completion_status,
            "needs_approval",
        )

    def test_warnings_surface_blocked_and_needs_approval(self) -> None:
        funnel = (
            CompletionFunnelSummary(
                session_id="sess-blocked",
                job_id="job-1",
                job_type="coding_execute",
                completion_status="blocked",
                ticked=False,
                reason="protected_branch",
                at="2026-05-10T00:00:00+00:00",
            ),
            CompletionFunnelSummary(
                session_id="sess-approval",
                job_id="job-2",
                job_type="role_take",
                completion_status="needs_approval",
                ticked=False,
                at="2026-05-10T00:00:01+00:00",
            ),
        )
        report = build_runtime_status(
            queue=self.queue,
            heartbeats=self.heartbeats,
            autonomy_journal=RuntimeAutonomyJournal(),
            completion_funnel_recent=funnel,
        )
        joined = "\n".join(report.warnings)
        self.assertIn("completion=blocked", joined)
        self.assertIn("sess-blocked", joined)
        self.assertIn("waiting on human approval", joined)
        self.assertIn("sess-approval", joined)

    def test_warning_when_autonomy_tick_errored(self) -> None:
        journal = RuntimeAutonomyJournal()
        journal.record_report(
            _FakeReport(
                tick_id="tick-bad",
                started_at="t",
                finished_at="t",
                next_task_candidate=None,
                dispatches=(),
                error="selector_failed:RuntimeError",
            )
        )
        report = build_runtime_status(
            queue=self.queue,
            heartbeats=self.heartbeats,
            autonomy_journal=journal,
        )
        joined = "\n".join(report.warnings)
        self.assertIn("autonomy producer errored", joined)
        self.assertIn("tick-bad", joined)

    def test_warning_when_dispatch_locked_by_other(self) -> None:
        journal = RuntimeAutonomyJournal()
        journal.record_report(
            _FakeReport(
                tick_id="tick-1",
                started_at="t",
                finished_at="t",
                next_task_candidate=None,
                dispatches=(
                    _FakeDispatch(
                        source="approved_coding_job",
                        outcome=AUTONOMY_OUTCOME_LOCKED,
                        session_id="sess-locked",
                        reason="scope held",
                    ),
                ),
            )
        )
        report = build_runtime_status(
            queue=self.queue,
            heartbeats=self.heartbeats,
            autonomy_journal=journal,
        )
        joined = "\n".join(report.warnings)
        self.assertIn("held locks", joined)
        self.assertIn("sess-locked", joined)

    def test_renderers_include_autonomy_section(self) -> None:
        journal = RuntimeAutonomyJournal()
        journal.record_report(
            _FakeReport(
                tick_id="tick-X",
                started_at="t",
                finished_at="t",
                next_task_candidate=_FakeCandidate(source="idle"),
                dispatches=(),
                summary="autonomy producer idle",
            )
        )
        report = build_runtime_status(
            queue=self.queue,
            heartbeats=self.heartbeats,
            autonomy_journal=journal,
        )
        text = render_runtime_status_text(report)
        self.assertIn("autonomy producer", text)
        self.assertIn("tick-X", text)
        json_blob = render_runtime_status_json(report)
        self.assertIn("autonomy_recent", json_blob)
        self.assertIn("tick-X", json_blob)


# ---------------------------------------------------------------------------
# render_autonomy_summary_markdown
# ---------------------------------------------------------------------------


def _empty_status_report(
    *,
    autonomy_recent: Sequence[AutonomyTickSummary] = (),
    completion_funnel_recent: Sequence[CompletionFunnelSummary] = (),
    locks_held: Sequence[str] = (),
) -> RuntimeStatusReport:
    return RuntimeStatusReport(
        profile="engineering",
        generated_at=1_731_000_000.0,
        deadline_seconds=90.0,
        services=(),
        job_types=(),
        failed_recent=(),
        warnings=(),
        autonomy_recent=tuple(autonomy_recent),
        completion_funnel_recent=tuple(completion_funnel_recent),
        autonomy_locks_held=tuple(locks_held),
    )


class AutonomyMarkdownRendererTests(unittest.TestCase):
    def test_returns_empty_when_nothing_to_show(self) -> None:
        text = render_autonomy_summary_markdown(_empty_status_report())
        self.assertEqual(text, "")

    def test_dispatched_outcome_renders_with_role_and_session(self) -> None:
        tick = AutonomyTickSummary(
            tick_id="tick-1",
            started_at="t",
            finished_at="t",
            next_task_source="approved_coding_job",
            summary_line="autonomy producer tick tick-1 dispatched",
            dispatches=(
                AutonomyDispatchSummary(
                    source="approved_coding_job",
                    outcome=AUTONOMY_OUTCOME_DISPATCHED,
                    session_id="sess-1",
                    executor_role="backend-engineer",
                    job_id="job-7",
                    branch_hint="agent/backend/issue-1",
                ),
            ),
        )
        text = render_autonomy_summary_markdown(
            _empty_status_report(autonomy_recent=(tick,))
        )
        self.assertIn("### Autonomy producer", text)
        self.assertIn("tick-1", text)
        self.assertIn("approved_coding_job", text)
        self.assertIn("backend-engineer", text)
        self.assertIn("dispatched", text)

    def test_locked_outcome_renders_with_lock_icon(self) -> None:
        tick = AutonomyTickSummary(
            tick_id="tick-2",
            started_at="t",
            finished_at="t",
            next_task_source="approved_coding_job",
            summary_line="",
            dispatches=(
                AutonomyDispatchSummary(
                    source="approved_coding_job",
                    outcome=AUTONOMY_OUTCOME_LOCKED,
                    session_id="sess-2",
                    reason="scope held: coding_job:sess-2:role",
                ),
            ),
        )
        text = render_autonomy_summary_markdown(
            _empty_status_report(autonomy_recent=(tick,))
        )
        self.assertIn("locked_by_other", text)
        self.assertIn("🔒", text)
        self.assertIn("sess-2", text)

    def test_funnel_section_distinguishes_blocked_vs_needs_approval(self) -> None:
        funnel = (
            CompletionFunnelSummary(
                session_id="s-block",
                job_id="j-1",
                job_type="coding_execute",
                completion_status="blocked",
                ticked=False,
                reason="protected branch",
            ),
            CompletionFunnelSummary(
                session_id="s-app",
                job_id="j-2",
                job_type="role_take",
                completion_status="needs_approval",
                ticked=False,
            ),
            CompletionFunnelSummary(
                session_id="s-retry",
                job_id="j-3",
                job_type="coding_execute",
                completion_status="retry_ready",
                ticked=True,
                recommended_source="retry_same",
            ),
        )
        text = render_autonomy_summary_markdown(
            _empty_status_report(completion_funnel_recent=funnel)
        )
        self.assertIn("### Completion funnel", text)
        self.assertIn("blocked", text)
        self.assertIn("⛔", text)
        self.assertIn("needs_approval", text)
        self.assertIn("🙋", text)
        self.assertIn("retry_ready", text)
        self.assertIn("🔁", text)
        # blocked should mention manual review hint
        self.assertIn("operator review required", text)
        # needs_approval should mention #승인-대기
        self.assertIn("승인-대기", text)

    def test_locks_held_only_section(self) -> None:
        text = render_autonomy_summary_markdown(
            _empty_status_report(locks_held=("branch:repo:main",))
        )
        self.assertIn("locks held", text)
        self.assertIn("branch:repo:main", text)


# ---------------------------------------------------------------------------
# AutonomyTickSummary helpers
# ---------------------------------------------------------------------------


class TickSummaryHelpersTests(unittest.TestCase):
    def test_has_actionable_signal_true_for_error(self) -> None:
        tick = AutonomyTickSummary(
            tick_id="x",
            started_at="t",
            finished_at="t",
            next_task_source=None,
            summary_line="",
            error="boom",
        )
        self.assertTrue(tick.has_actionable_signal())

    def test_has_actionable_signal_false_for_clean_dispatch(self) -> None:
        tick = AutonomyTickSummary(
            tick_id="x",
            started_at="t",
            finished_at="t",
            next_task_source=None,
            summary_line="",
            dispatches=(
                AutonomyDispatchSummary(
                    source="approved_coding_job",
                    outcome=AUTONOMY_OUTCOME_DISPATCHED,
                ),
            ),
        )
        self.assertFalse(tick.has_actionable_signal())

    def test_funnel_actionable_for_blocked_or_needs_approval(self) -> None:
        for status in ("blocked", "needs_approval"):
            self.assertTrue(
                CompletionFunnelSummary(
                    session_id="s",
                    job_id="j",
                    job_type="t",
                    completion_status=status,
                    ticked=False,
                ).is_actionable()
            )
        for status in ("done", "retry_ready"):
            self.assertFalse(
                CompletionFunnelSummary(
                    session_id="s",
                    job_id="j",
                    job_type="t",
                    completion_status=status,
                    ticked=True,
                ).is_actionable()
            )


if __name__ == "__main__":
    unittest.main()
