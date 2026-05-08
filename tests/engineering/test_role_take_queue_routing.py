"""A-M4 routing integration test.

Pin the contract that ``handle_research_turn_message`` and
``_handle_research_open_call`` enqueue ``role_take`` jobs instead of
running the role-take render directly. Each gateway/member-bot call
that produces a forum comment now leaves a ``role_take`` row in
``queued → assigned → in_progress → saved`` order, scoped per
``(session_id, role, kind)``.

The exercise also proves the user-visible payload (``message``) is
unchanged: the runner output flows back through the worker into the
``ResearchTurnOutcome`` the member bot already expects.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue import (
    JOB_TYPE_ROLE_TAKE,
    KIND_OPEN,
    JobQueue,
    JobState,
)
from yule_orchestrator.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
)
from yule_orchestrator.discord.engineering_team_runtime import (
    handle_research_turn_message,
)


def _session(*, active_roles=None, role_sequence=()) -> WorkflowSession:
    extra: dict[str, Any] = {}
    if active_roles is not None:
        extra["active_research_roles"] = list(active_roles)
    now = datetime(2026, 5, 7)
    return WorkflowSession(
        session_id="sessm4abc",
        prompt="k8s 운영 자료 정리",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=now,
        updated_at=now,
        role_sequence=tuple(role_sequence),
        extra=extra,
    )


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        try:
            from tests._helpers import isolate_cache_for_test
        except ImportError:  # pragma: no cover
            from _helpers import isolate_cache_for_test  # type: ignore

        isolate_cache_for_test(self)

        # Reset M4's in-process dedup ring so prior tests in the same
        # process can't shadow our (role, session, kind) triple.
        from yule_orchestrator.discord import engineering_team_runtime as etr

        etr._HANDLED_TURNS.clear()
        etr._HANDLED_TURNS_SET.clear()


class OpenCallEnqueuesRoleTakeTests(_Fixture):
    """``handle_research_turn_message`` for a research-open marker
    must land a ``role_take`` row scoped to the bot's role.
    """

    def test_open_marker_creates_saved_role_take_job(self) -> None:
        session = _session(
            active_roles=["tech-lead", "ai-engineer", "qa-engineer"]
        )

        outcome = handle_research_turn_message(
            role="ai-engineer",
            text="[research-open:sessm4abc]",
            session_loader=lambda _sid: session,
            pack_loader=lambda _s: None,
        )

        self.assertIsNotNone(outcome)
        # Behaviour preserved — outcome.message still contains the
        # legacy "자율 조사 메모" footer the open-call render uses.
        assert outcome is not None  # mypy
        self.assertIn("자율 조사 메모", outcome.message)

        # And the queue carries a SAVED row scoped to (session, role,
        # kind=open) — the supervisor sees the work after the fact.
        queue = JobQueue()
        rows = queue.list_for_session("sessm4abc")
        role_take_rows = [
            row
            for row in rows
            if row.job_type == JOB_TYPE_ROLE_TAKE
            and row.role == "ai-engineer"
            and (row.payload or {}).get("kind") == KIND_OPEN
        ]
        self.assertEqual(len(role_take_rows), 1)
        self.assertEqual(role_take_rows[0].state, JobState.SAVED)

    def test_inactive_role_does_not_enqueue(self) -> None:
        # frontend-engineer wasn't selected — its handler returns
        # None at the cheap pre-gate, so no role_take row should
        # land in the queue. This also pins the "queue isn't a
        # dumping ground for skipped turns" contract.
        session = _session(
            active_roles=["tech-lead", "ai-engineer", "qa-engineer"]
        )

        outcome = handle_research_turn_message(
            role="frontend-engineer",
            text="[research-open:sessm4abc]",
            session_loader=lambda _sid: session,
            pack_loader=lambda _s: None,
        )

        self.assertIsNone(outcome)
        queue = JobQueue()
        rows = [
            row
            for row in queue.list_for_session("sessm4abc")
            if row.job_type == JOB_TYPE_ROLE_TAKE
            and row.role == "frontend-engineer"
        ]
        self.assertEqual(rows, [])

    def test_duplicate_open_marker_dedups_and_returns_silent(self) -> None:
        # First call lands a SAVED row. Second call hits the
        # in-process dedup AND would hit the queue dedup if dedup
        # cleared — both gates collaborate to keep the forum from
        # double-posting.
        session = _session(
            active_roles=["tech-lead", "ai-engineer", "qa-engineer"]
        )

        first = handle_research_turn_message(
            role="ai-engineer",
            text="[research-open:sessm4abc]",
            session_loader=lambda _sid: session,
            pack_loader=lambda _s: None,
        )
        second = handle_research_turn_message(
            role="ai-engineer",
            text="[research-open:sessm4abc]",
            session_loader=lambda _sid: session,
            pack_loader=lambda _s: None,
        )
        self.assertIsNotNone(first)
        # Second call: in-process dedup makes it return None silently.
        self.assertIsNone(second)

        # Only one role_take row landed — the queue did not double-create.
        queue = JobQueue()
        ai_rows = [
            row
            for row in queue.list_for_session("sessm4abc")
            if row.job_type == JOB_TYPE_ROLE_TAKE
            and row.role == "ai-engineer"
        ]
        self.assertEqual(len(ai_rows), 1)


class OpenCallExceptionFailsRetryableTests(_Fixture):
    """When the role-take render body raises (e.g. transient
    deliberation 503), the role_take job lands in
    ``failed_retryable`` instead of vanishing — exactly the recovery
    surface M2's reaper / a future requeue pass can act on.
    """

    def test_runner_exception_lands_failed_retryable(self) -> None:
        # Patch the open-call body builder so it raises. We do this
        # via attribute replacement so we don't hit the real
        # _build_open_call_outcome which is widely tested elsewhere.
        from yule_orchestrator.discord import engineering_team_runtime as etr

        original = etr._build_open_call_outcome

        def boom(**_kwargs):
            raise RuntimeError("deliberation provider 503")

        etr._build_open_call_outcome = boom  # type: ignore[assignment]
        self.addCleanup(
            lambda: setattr(etr, "_build_open_call_outcome", original)
        )

        session = _session(
            active_roles=["tech-lead", "ai-engineer", "qa-engineer"]
        )

        outcome = handle_research_turn_message(
            role="ai-engineer",
            text="[research-open:sessm4abc]",
            session_loader=lambda _sid: session,
            pack_loader=lambda _s: None,
        )
        # Member bot stays silent — the forum doesn't get a half-baked
        # comment. The queue carries the failure for the supervisor.
        self.assertIsNone(outcome)

        queue = JobQueue()
        retryable = queue.list_for_session(
            "sessm4abc", states=[JobState.FAILED_RETRYABLE]
        )
        self.assertEqual(len(retryable), 1)
        self.assertEqual(retryable[0].role, "ai-engineer")
        self.assertIn(
            "deliberation provider 503",
            retryable[0].result.get("error", ""),
        )


if __name__ == "__main__":
    unittest.main()
