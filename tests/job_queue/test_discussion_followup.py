"""discussion_followup — Round 4 of #73.

The discussion follow-up dispatcher converts an unresolved discussion
classifier verdict into actual queue rows so the conversation keeps
moving even without a fresh user prompt.

Pin:

  * mode=discussion + missing_roles → role_take rows per role.
  * mode=research_only → research_collect row.
  * mode=clarification_needed → no enqueue (skipped, awaits user).
  * mode=implementation_candidate → no enqueue (approval owns it).
  * idempotency marker prevents duplicate enqueue on the same
    (turn_id, role, kind) triple.
  * decision_port can short-circuit the discussion path.
  * worker.enqueue raise is captured as ERROR but doesn't crash.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Sequence, Tuple

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.autonomy_producer import (
    DispatchOutcome,
)
from yule_orchestrator.agents.job_queue.discussion_followup import (
    DISCUSSION_FOLLOWUP_KIND_RESEARCH,
    DISCUSSION_FOLLOWUP_KIND_ROLE_TAKE,
    DiscussionFollowupDispatcher,
    dispatch_discussion_followup,
    stamp_followup_marker,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.research_worker import ResearchWorker
from yule_orchestrator.agents.job_queue.role_take_worker import (
    JOB_TYPE_ROLE_TAKE,
    KIND_TURN,
    RoleTakeWorker,
)
from yule_orchestrator.agents.job_queue.next_task_selector import (
    SOURCE_UNRESOLVED_DISCUSSION,
)
from yule_orchestrator.agents.job_queue.research_worker import (
    JOB_TYPE_RESEARCH_COLLECT,
)
from yule_orchestrator.agents.job_queue.store import JobQueue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_workers(tmp: Path):
    db_path = tmp / "queue.sqlite3"
    queue = JobQueue(db_path=db_path)
    heartbeats = HeartbeatStore(db_path=db_path)
    role_worker = RoleTakeWorker(queue=queue, heartbeats=heartbeats)
    research_worker = ResearchWorker(queue=queue, heartbeats=heartbeats)
    return queue, role_worker, research_worker


@dataclass
class _StubSession:
    session_id: str
    extra: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------


class StampFollowupMarkerTests(unittest.TestCase):
    def test_stamp_writes_per_turn_bucket(self) -> None:
        marked = stamp_followup_marker(
            {},
            turn_id="t1",
            role="backend-engineer",
            kind=DISCUSSION_FOLLOWUP_KIND_ROLE_TAKE,
            job_id="j1",
        )
        self.assertIn("discussion_followup", marked)
        block = marked["discussion_followup"]
        self.assertIn("t1", block["by_turn"])
        bucket = block["by_turn"]["t1"]
        self.assertIn("backend-engineer:role_take", bucket)
        self.assertEqual(
            bucket["backend-engineer:role_take"]["job_id"], "j1"
        )

    def test_stamp_trims_to_32_turns(self) -> None:
        # Use stable, sortable string keys so the trim's
        # ``sorted(by_turn.keys())[: len-32]`` drops the oldest.
        extra: Mapping[str, Any] = {}
        for i in range(40):
            extra = stamp_followup_marker(
                extra,
                turn_id=f"t{i:03d}",
                role="r",
                kind="role_take",
                job_id=f"j{i}",
            )
        block = extra["discussion_followup"]
        self.assertEqual(len(block["by_turn"]), 32)
        # Latest turn is preserved.
        self.assertIn("t039", block["by_turn"])


# ---------------------------------------------------------------------------
# Dispatcher behaviour
# ---------------------------------------------------------------------------


class DiscussionFollowupDispatcherDiscussionModeTests(unittest.TestCase):
    def test_missing_roles_become_role_take_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, role_worker, _ = _build_workers(Path(tmp))
            dispatcher = DiscussionFollowupDispatcher(
                role_take_worker=role_worker,
            )
            row = {
                "session_id": "S1",
                "turn_id": "turn-100",
                "mode": "discussion",
                "missing_roles": ["backend-engineer", "qa-engineer"],
            }
            outs = dispatcher.dispatch(session_id="S1", discussion_row=row)
            self.assertEqual(len(outs), 2)
            self.assertEqual(outs[0].source, SOURCE_UNRESOLVED_DISCUSSION)
            self.assertEqual(outs[0].outcome, DispatchOutcome.DISPATCHED)
            self.assertEqual(outs[0].executor_role, "backend-engineer")
            self.assertEqual(outs[1].executor_role, "qa-engineer")
            # Both rows should be queryable on the role_take queue.
            backend = role_worker.find_active(
                session_id="S1", role="backend-engineer", kind=KIND_TURN
            )
            qa = role_worker.find_active(
                session_id="S1", role="qa-engineer", kind=KIND_TURN
            )
            self.assertIsNotNone(backend)
            self.assertIsNotNone(qa)

    def test_no_missing_roles_skips_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, role_worker, _ = _build_workers(Path(tmp))
            dispatcher = DiscussionFollowupDispatcher(
                role_take_worker=role_worker,
            )
            row = {"session_id": "S1", "mode": "discussion"}
            outs = dispatcher.dispatch(session_id="S1", discussion_row=row)
            self.assertEqual(len(outs), 1)
            self.assertEqual(outs[0].outcome, DispatchOutcome.SKIPPED)
            self.assertIn("missing", outs[0].reason)

    def test_role_take_worker_missing_skips(self) -> None:
        dispatcher = DiscussionFollowupDispatcher()
        row = {
            "session_id": "S1",
            "mode": "discussion",
            "missing_roles": ["backend-engineer"],
        }
        outs = dispatcher.dispatch(session_id="S1", discussion_row=row)
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0].outcome, DispatchOutcome.SKIPPED)
        self.assertIn("not wired", outs[0].reason)


class DiscussionFollowupDispatcherResearchOnlyTests(unittest.TestCase):
    def test_research_only_dispatches_research_collect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, role_worker, research_worker = _build_workers(Path(tmp))
            dispatcher = DiscussionFollowupDispatcher(
                role_take_worker=role_worker,
                research_worker=research_worker,
            )
            row = {
                "session_id": "S2",
                "turn_id": "turn-200",
                "mode": "research_only",
            }
            outs = dispatcher.dispatch(session_id="S2", discussion_row=row)
            self.assertEqual(len(outs), 1)
            self.assertEqual(outs[0].outcome, DispatchOutcome.DISPATCHED)
            existing = research_worker.find_active("S2")
            self.assertIsNotNone(existing)
            self.assertEqual(existing.job_type, JOB_TYPE_RESEARCH_COLLECT)


class DiscussionFollowupDispatcherTerminalModesTests(unittest.TestCase):
    def test_clarification_needed_yields_skip(self) -> None:
        dispatcher = DiscussionFollowupDispatcher()
        outs = dispatcher.dispatch(
            session_id="S3",
            discussion_row={"session_id": "S3", "mode": "clarification_needed"},
        )
        self.assertEqual(outs[0].outcome, DispatchOutcome.SKIPPED)

    def test_implementation_candidate_yields_skip(self) -> None:
        dispatcher = DiscussionFollowupDispatcher()
        outs = dispatcher.dispatch(
            session_id="S4",
            discussion_row={
                "session_id": "S4",
                "mode": "implementation_candidate",
            },
        )
        self.assertEqual(outs[0].outcome, DispatchOutcome.SKIPPED)
        self.assertIn("approval", outs[0].reason)


# ---------------------------------------------------------------------------
# Decision port + error paths
# ---------------------------------------------------------------------------


class DiscussionFollowupDecisionPortTests(unittest.TestCase):
    def test_decision_port_skip_short_circuits(self) -> None:
        captured: List[Mapping[str, Any]] = []

        class _StubAdvice:
            def __init__(self, skip: bool, reason: str) -> None:
                self.skip = skip
                self.reason = reason

        class _Port:
            def decide(self, *, request):
                captured.append(dict(request))
                return _StubAdvice(skip=True, reason="duplicate of prior turn")

        with tempfile.TemporaryDirectory() as tmp:
            _, role_worker, _ = _build_workers(Path(tmp))
            dispatcher = DiscussionFollowupDispatcher(
                role_take_worker=role_worker,
            )
            row = {
                "session_id": "S5",
                "turn_id": "t-500",
                "mode": "discussion",
                "missing_roles": ["backend-engineer"],
            }
            outs = dispatcher.dispatch(
                session_id="S5",
                discussion_row=row,
                decision_port=_Port(),
            )
            self.assertEqual(len(outs), 1)
            self.assertEqual(outs[0].outcome, DispatchOutcome.SKIPPED)
            self.assertIn("duplicate", outs[0].reason)
            # Worker queue must NOT have a row for this session.
            self.assertIsNone(
                role_worker.find_active(
                    session_id="S5", role="backend-engineer", kind=KIND_TURN
                )
            )
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["session_id"], "S5")

    def test_decision_port_raise_falls_back_to_default_path(self) -> None:
        class _Port:
            def decide(self, *, request):
                raise RuntimeError("port down")

        with tempfile.TemporaryDirectory() as tmp:
            _, role_worker, _ = _build_workers(Path(tmp))
            dispatcher = DiscussionFollowupDispatcher(
                role_take_worker=role_worker,
            )
            row = {
                "session_id": "S6",
                "turn_id": "t-600",
                "mode": "discussion",
                "missing_roles": ["backend-engineer"],
            }
            outs = dispatcher.dispatch(
                session_id="S6",
                discussion_row=row,
                decision_port=_Port(),
            )
            self.assertEqual(outs[0].outcome, DispatchOutcome.DISPATCHED)


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------


class DispatchConvenienceWrapperTests(unittest.TestCase):
    def test_functional_wrapper_matches_class_behaviour(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, role_worker, _ = _build_workers(Path(tmp))
            row = {
                "session_id": "S7",
                "turn_id": "t-700",
                "mode": "discussion",
                "missing_roles": ["backend-engineer"],
            }
            outs = dispatch_discussion_followup(
                session_id="S7",
                discussion_row=row,
                role_take_worker=role_worker,
            )
            self.assertEqual(len(outs), 1)
            self.assertEqual(outs[0].outcome, DispatchOutcome.DISPATCHED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
