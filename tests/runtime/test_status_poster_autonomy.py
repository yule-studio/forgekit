"""Autonomy / completion-funnel surface in status_poster — Round 4 of #73.

The poster's job grew alongside the autonomy producer:

  * Dedup key now considers errored ticks, persistent locked dispatches,
    and parked funnel sessions — a transition into / out of any of those
    states must trigger a fresh post.
  * is_clean_state reflects the new signals so an "all clear" Discord
    post stays accurate.
  * post_runtime_status_summary appends the autonomy markdown sections
    after the M7 summary so the operator sees both the heartbeat
    health AND the runtime's recent decisions in one post.
  * collect_recent_completion_funnel scrapes session.extra without
    depending on the workflow cache layer at import time.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Mapping, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.runtime.status import (
    AUTONOMY_OUTCOME_DISPATCHED,
    AUTONOMY_OUTCOME_ERROR,
    AUTONOMY_OUTCOME_LOCKED,
    AutonomyDispatchSummary,
    AutonomyTickSummary,
    CompletionFunnelSummary,
    HEALTH_ALIVE,
    RuntimeStatusReport,
    ServiceStatus,
)
from yule_orchestrator.runtime.status_poster import (
    StatusPosterStateStore,
    collect_recent_completion_funnel,
    compute_status_dedup_key,
    is_clean_state,
    post_runtime_status_summary,
    should_post_status,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _service(service_id: str = "eng-x", health: str = HEALTH_ALIVE) -> ServiceStatus:
    return ServiceStatus(
        service_id=service_id,
        kind="research_worker",
        role=None,
        description="x",
        implemented=True,
        health=health,
        heartbeat_age_seconds=1.0,
        heartbeat_last_beat=None,
        pid=None,
        metadata={},
        job_type=None,
    )


def _report(
    *,
    autonomy_recent: Sequence[AutonomyTickSummary] = (),
    funnel: Sequence[CompletionFunnelSummary] = (),
    locks_held: Sequence[str] = (),
) -> RuntimeStatusReport:
    return RuntimeStatusReport(
        profile="engineering",
        generated_at=1_731_000_000.0,
        deadline_seconds=90.0,
        services=(_service(),),
        job_types=(),
        failed_recent=(),
        warnings=(),
        autonomy_recent=tuple(autonomy_recent),
        completion_funnel_recent=tuple(funnel),
        autonomy_locks_held=tuple(locks_held),
    )


# ---------------------------------------------------------------------------
# Dedup key sensitivity
# ---------------------------------------------------------------------------


class DedupKeySensitivityTests(unittest.TestCase):
    def test_clean_to_errored_tick_changes_key(self) -> None:
        clean = _report()
        errored = _report(
            autonomy_recent=(
                AutonomyTickSummary(
                    tick_id="t-bad",
                    started_at="t",
                    finished_at="t",
                    next_task_source=None,
                    summary_line="",
                    error="selector_failed",
                ),
            )
        )
        self.assertNotEqual(
            compute_status_dedup_key(report=clean),
            compute_status_dedup_key(report=errored),
        )

    def test_clean_to_locked_dispatch_changes_key(self) -> None:
        clean = _report()
        locked = _report(
            autonomy_recent=(
                AutonomyTickSummary(
                    tick_id="t1",
                    started_at="t",
                    finished_at="t",
                    next_task_source=None,
                    summary_line="",
                    dispatches=(
                        AutonomyDispatchSummary(
                            source="approved_coding_job",
                            outcome=AUTONOMY_OUTCOME_LOCKED,
                            session_id="sess-A",
                        ),
                    ),
                ),
            )
        )
        self.assertNotEqual(
            compute_status_dedup_key(report=clean),
            compute_status_dedup_key(report=locked),
        )

    def test_blocked_funnel_changes_key(self) -> None:
        clean = _report()
        blocked = _report(
            funnel=(
                CompletionFunnelSummary(
                    session_id="s-b",
                    job_id="j",
                    job_type="coding_execute",
                    completion_status="blocked",
                    ticked=False,
                ),
            )
        )
        self.assertNotEqual(
            compute_status_dedup_key(report=clean),
            compute_status_dedup_key(report=blocked),
        )

    def test_all_dispatched_does_not_change_key(self) -> None:
        # A successful tick should NOT make the poster repost — only
        # operator-actionable bits do.
        clean = _report()
        with_dispatch = _report(
            autonomy_recent=(
                AutonomyTickSummary(
                    tick_id="t1",
                    started_at="t",
                    finished_at="t",
                    next_task_source="approved_coding_job",
                    summary_line="",
                    dispatches=(
                        AutonomyDispatchSummary(
                            source="approved_coding_job",
                            outcome=AUTONOMY_OUTCOME_DISPATCHED,
                            session_id="sess-A",
                        ),
                    ),
                ),
            )
        )
        self.assertEqual(
            compute_status_dedup_key(report=clean),
            compute_status_dedup_key(report=with_dispatch),
        )


class IsCleanStateTests(unittest.TestCase):
    def test_blocked_funnel_marks_dirty(self) -> None:
        report = _report(
            funnel=(
                CompletionFunnelSummary(
                    session_id="s",
                    job_id="j",
                    job_type="t",
                    completion_status="blocked",
                    ticked=False,
                ),
            )
        )
        self.assertFalse(is_clean_state(report=report))

    def test_errored_tick_marks_dirty(self) -> None:
        report = _report(
            autonomy_recent=(
                AutonomyTickSummary(
                    tick_id="t",
                    started_at="t",
                    finished_at="t",
                    next_task_source=None,
                    summary_line="",
                    error="boom",
                ),
            )
        )
        self.assertFalse(is_clean_state(report=report))

    def test_clean_with_only_dispatched_ticks(self) -> None:
        report = _report(
            autonomy_recent=(
                AutonomyTickSummary(
                    tick_id="t",
                    started_at="t",
                    finished_at="t",
                    next_task_source="approved_coding_job",
                    summary_line="",
                    dispatches=(
                        AutonomyDispatchSummary(
                            source="approved_coding_job",
                            outcome=AUTONOMY_OUTCOME_DISPATCHED,
                        ),
                    ),
                ),
            )
        )
        self.assertTrue(is_clean_state(report=report))


# ---------------------------------------------------------------------------
# Markdown body — autonomy section appended after base summary
# ---------------------------------------------------------------------------


class PostMarkdownAppendsAutonomyTests(unittest.TestCase):
    def test_post_includes_autonomy_section(self) -> None:
        captured: List[str] = []

        async def post_fn(content: str):
            captured.append(content)
            return {"posted_message_id": 5}

        with tempfile.TemporaryDirectory() as tmp:
            store = StatusPosterStateStore(
                path=Path(tmp) / "state.json"
            )
            funnel = (
                CompletionFunnelSummary(
                    session_id="sess-1",
                    job_id="j",
                    job_type="coding_execute",
                    completion_status="needs_approval",
                    ticked=False,
                ),
            )
            tick = AutonomyTickSummary(
                tick_id="tick-Z",
                started_at="t",
                finished_at="t",
                next_task_source="approved_coding_job",
                summary_line="autonomy producer ran",
                dispatches=(
                    AutonomyDispatchSummary(
                        source="approved_coding_job",
                        outcome=AUTONOMY_OUTCOME_DISPATCHED,
                        session_id="sess-1",
                        executor_role="backend-engineer",
                    ),
                ),
            )
            report = _report(autonomy_recent=(tick,), funnel=funnel)
            outcome = _run(
                post_runtime_status_summary(
                    report=report,
                    state_store=store,
                    post_fn=post_fn,
                )
            )
        self.assertTrue(outcome.did_post)
        self.assertEqual(len(captured), 1)
        body = captured[0]
        # Base summary header still present.
        self.assertIn("runtime status", body)
        # Autonomy section appended after.
        self.assertIn("Autonomy producer", body)
        self.assertIn("tick-Z", body)
        # Funnel section differentiates needs_approval.
        self.assertIn("needs_approval", body)

    def test_post_leads_with_top_action_banner_when_actionable(self) -> None:
        # Round 4 마무리: the post body should lead with a one-line
        # operator-action banner so the Discord notification preview
        # carries the next-step before the operator opens the message.
        captured: List[str] = []

        async def post_fn(content: str):
            captured.append(content)
            return {"posted_message_id": 7}

        with tempfile.TemporaryDirectory() as tmp:
            store = StatusPosterStateStore(path=Path(tmp) / "state.json")
            funnel = (
                CompletionFunnelSummary(
                    session_id="sess-banner",
                    job_id="j",
                    job_type="coding_execute",
                    completion_status="blocked",
                    ticked=False,
                    reason="protected_branch_blocked",
                ),
            )
            report = _report(funnel=funnel)
            outcome = _run(
                post_runtime_status_summary(
                    report=report,
                    state_store=store,
                    post_fn=post_fn,
                )
            )
        self.assertTrue(outcome.did_post)
        body = captured[0]
        # Banner is a quoted block — must come before the base header.
        banner_idx = body.find("[high]")
        header_idx = body.find("runtime status")
        self.assertGreaterEqual(banner_idx, 0)
        self.assertGreaterEqual(header_idx, 0)
        self.assertLess(banner_idx, header_idx)
        self.assertIn("다음 단계:", body)

    def test_post_skips_top_action_banner_when_clean(self) -> None:
        # Healthy snapshot must not grow the post with an empty banner.
        captured: List[str] = []

        async def post_fn(content: str):
            captured.append(content)
            return {"posted_message_id": 8}

        with tempfile.TemporaryDirectory() as tmp:
            store = StatusPosterStateStore(path=Path(tmp) / "state.json")
            outcome = _run(
                post_runtime_status_summary(
                    report=_report(),
                    state_store=store,
                    post_fn=post_fn,
                )
            )
        self.assertTrue(outcome.did_post)
        body = captured[0]
        # No operator-action quote line should appear at the top.
        self.assertFalse(body.lstrip().startswith(">"))

    def test_post_skips_when_dedup_matches_with_only_dispatched_tick(self) -> None:
        # Successful dispatched ticks must not flap the post.
        async def post_fn(content: str):
            return {"posted_message_id": 1}

        clean = _report(
            autonomy_recent=(
                AutonomyTickSummary(
                    tick_id="t1",
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
                ),
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = StatusPosterStateStore(path=Path(tmp) / "state.json")
            store.save(
                last_dedup_key=compute_status_dedup_key(report=clean),
                last_posted_at=1.0,
            )
            outcome = _run(
                post_runtime_status_summary(
                    report=clean,
                    state_store=store,
                    post_fn=post_fn,
                )
            )
        self.assertFalse(outcome.did_post)
        self.assertEqual(outcome.skipped_reason, "dedup_key_matches_last_post")


# ---------------------------------------------------------------------------
# collect_recent_completion_funnel
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    session_id: str
    extra: Mapping[str, Any] = field(default_factory=dict)


class CollectRecentCompletionFunnelTests(unittest.TestCase):
    def test_aggregates_history_across_sessions(self) -> None:
        sessions = [
            _FakeSession(
                session_id="sess-A",
                extra={
                    "completion_funnel": {
                        "history": [
                            {
                                "session_id": "sess-A",
                                "job_id": "j-1",
                                "job_type": "coding_execute",
                                "completion_status": "needs_approval",
                                "ticked": False,
                                "reason": "awaiting reply",
                                "at": "2026-05-10T00:00:01+00:00",
                            },
                            {
                                "session_id": "sess-A",
                                "job_id": "j-2",
                                "job_type": "coding_execute",
                                "completion_status": "done",
                                "ticked": True,
                                "recommended_source": "next_task_default",
                                "at": "2026-05-10T00:00:00+00:00",
                            },
                        ]
                    }
                },
            ),
            _FakeSession(
                session_id="sess-B",
                extra={
                    "completion_funnel": {
                        "history": [
                            {
                                "session_id": "sess-B",
                                "job_id": "j-3",
                                "job_type": "role_take",
                                "completion_status": "blocked",
                                "ticked": False,
                                "reason": "external",
                                "at": "2026-05-10T00:00:02+00:00",
                            }
                        ]
                    }
                },
            ),
        ]

        def lister(*, limit=50):
            return tuple(sessions[:limit])

        rows = collect_recent_completion_funnel(
            session_lister=lister, funnel_limit=10
        )
        # Newest-first by ``at``: sess-B blocked at 00:00:02 > sess-A
        # needs_approval at 00:00:01 > sess-A done at 00:00:00.
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].session_id, "sess-B")
        self.assertEqual(rows[0].completion_status, "blocked")
        self.assertEqual(rows[1].completion_status, "needs_approval")
        self.assertEqual(rows[2].completion_status, "done")

    def test_returns_empty_when_lister_raises(self) -> None:
        def lister(**_kwargs):
            raise RuntimeError("workflow store down")

        rows = collect_recent_completion_funnel(session_lister=lister)
        self.assertEqual(rows, ())

    def test_skips_sessions_without_funnel_block(self) -> None:
        sessions = [
            _FakeSession(session_id="empty", extra={}),
            _FakeSession(
                session_id="ok",
                extra={
                    "completion_funnel": {
                        "history": [
                            {
                                "session_id": "ok",
                                "job_id": "j",
                                "job_type": "coding_execute",
                                "completion_status": "done",
                                "ticked": True,
                                "at": "2026-05-10T00:00:00+00:00",
                            }
                        ]
                    }
                },
            ),
        ]

        def lister(*, limit=50):
            return tuple(sessions)

        rows = collect_recent_completion_funnel(session_lister=lister)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].session_id, "ok")

    def test_skips_malformed_history_entries(self) -> None:
        sessions = [
            _FakeSession(
                session_id="x",
                extra={
                    "completion_funnel": {
                        "history": [
                            "not a dict",
                            {"completion_status": ""},  # blank status
                            {
                                "session_id": "x",
                                "job_id": "j",
                                "job_type": "t",
                                "completion_status": "blocked",
                                "ticked": False,
                                "at": "2026-05-10T00:00:00+00:00",
                            },
                        ]
                    }
                },
            )
        ]

        def lister(*, limit=50):
            return tuple(sessions)

        rows = collect_recent_completion_funnel(session_lister=lister)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].completion_status, "blocked")


if __name__ == "__main__":
    unittest.main()
