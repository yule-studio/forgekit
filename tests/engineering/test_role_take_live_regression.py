"""A-M4 live regression harness — local integration coverage.

Live Discord (real bot tokens / `#운영-리서치` forum) is not available
in the test sandbox, so this harness exercises the production code
path end-to-end against the same SQLite + workflow store + queue +
heartbeat layers that the gateway and member bots use in production.

Each test pins one of the 8 verification items spec'd for the M4
live regression:

  1. ``[research-open:<sid>]`` lands a ``role_take`` job per active role.
  2. Each role worker only picks its own role's row.
  3. Success drives ``queued → assigned → in_progress → saved``.
  4. Forum comment shape (``자율 조사 메모`` footer + role take body) is unchanged.
  5. Duplicate open-call markers do not produce duplicate jobs/comments.
  6. Runner failure lands the row in ``failed_retryable``.
  7. Per-role heartbeats land under ``eng-role-worker:<role>``.
  8. ``session.extra`` carries ``research_pack`` / ``research_forum_thread_id``
     etc. across the queue trip.

Tests intentionally use the real ``WorkflowSession`` store + the real
``handle_research_turn_message`` entry point — not a mock — so we
catch regressions in the wiring rather than just the worker core.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue import (
    JOB_TYPE_ROLE_TAKE,
    HeartbeatStore,
    JobQueue,
    JobState,
    service_id_for_role,
)
from yule_engineering.agents.workflow_state import (
    WorkflowSession,
    WorkflowState,
    load_session,
    save_session,
)
from yule_engineering.discord.engineering_team_runtime import _legacy as etr


SESSION_ID = "live-m4-sess1"
ACTIVE_ROLES = ["tech-lead", "ai-engineer", "qa-engineer"]


def _seed_session(
    *,
    research_pack: Optional[Dict[str, Any]] = None,
    forum_thread_id: Optional[int] = 4242,
) -> WorkflowSession:
    """Persist a session that mirrors the live "k8s research-only"
    intake state right after ``research_collect`` lands its pack.
    """

    extra: Dict[str, Any] = {
        "active_research_roles": list(ACTIVE_ROLES),
        "role_selection_source": "tech_lead_rule",
    }
    if research_pack is not None:
        extra["research_pack"] = research_pack
    if forum_thread_id is not None:
        extra["research_forum_thread_id"] = forum_thread_id
    now = datetime(2026, 5, 7, 13, 0)
    session = WorkflowSession(
        session_id=SESSION_ID,
        prompt="k8s 운영 자료 정리해줘",
        task_type="research",
        state=WorkflowState.IN_PROGRESS,
        created_at=now,
        updated_at=now,
        extra=extra,
    )
    save_session(session)
    return session


class _Harness(unittest.TestCase):
    """Per-test SQLite isolation + dedup ring reset.

    Without isolating the cache, prior test sessions (and their
    queue rows) would leak into our verification queries. Without
    resetting the in-process dedup ring, the second run of a
    duplicate-marker test would see "already handled" from the
    first test in the same process.
    """

    def setUp(self) -> None:  # noqa: D401 - test setup
        try:
            from tests._helpers import isolate_cache_for_test
        except ImportError:  # pragma: no cover
            from _helpers import isolate_cache_for_test  # type: ignore

        isolate_cache_for_test(self)
        etr._HANDLED_TURNS.clear()
        etr._HANDLED_TURNS_SET.clear()

    # Convenience: filter the queue to role_take rows for the harness session.
    def _role_take_rows(self) -> List[Any]:
        queue = JobQueue()
        return [
            row
            for row in queue.list_for_session(SESSION_ID)
            if row.job_type == JOB_TYPE_ROLE_TAKE
        ]


# ---------------------------------------------------------------------------
# 1+2+3+4: open-call lands a SAVED row per role; the forum render text
# preserves the legacy ``자율 조사 메모`` footer + role take body.
# ---------------------------------------------------------------------------


class OpenCallEndToEndTests(_Harness):
    """The cleanest live-regression scenario: 3 active roles each
    receive ``[research-open:<sid>]`` and produce one SAVED
    role_take row, with the forum-comment text the member bot
    would actually post.
    """

    def test_three_active_roles_each_produce_saved_role_take(self) -> None:
        seeded = _seed_session(
            research_pack={
                "title": "k8s ingress 운영",
                "summary": "stub",
                "sources": [
                    {"url": "https://k8s.io/docs/concepts/services-networking/ingress/", "title": "Ingress"}
                ],
            }
        )
        marker = f"[research-open:{SESSION_ID}]"

        # ``pack_loader=None`` lets the runtime fall through to
        # ``_load_pack_from_session_extra`` which converts the dict
        # via ``pack_from_dict`` into a real ResearchPack — the
        # legacy contract is "pack_loader returns ResearchPack | None",
        # not a raw dict.
        outcomes: Dict[str, Any] = {}
        for role in ACTIVE_ROLES:
            outcomes[role] = etr.handle_research_turn_message(
                role=role,
                text=marker,
                session_loader=lambda _sid, _seeded=seeded: load_session(_sid) or _seeded,
                pack_loader=lambda _s: None,
            )

        # Each active role got an outcome; 자율 조사 메모 footer is
        # the legacy contract for open-call comments.
        for role, outcome in outcomes.items():
            with self.subTest(role=role):
                self.assertIsNotNone(outcome, f"{role} expected outcome")
                self.assertIn("자율 조사 메모", outcome.message)

        # Each role landed exactly one SAVED row scoped to (session, role, kind=open).
        rows = self._role_take_rows()
        by_role = {row.role: row for row in rows}
        self.assertEqual(set(by_role.keys()), set(ACTIVE_ROLES))
        for role, row in by_role.items():
            with self.subTest(role=role):
                self.assertEqual(row.state, JobState.SAVED)
                self.assertEqual((row.payload or {}).get("kind"), "open")
                # picked_by/picked_until cleared on terminal — proves
                # the SAVED transition actually went through clear_lease=True.
                self.assertIsNone(row.picked_by)
                self.assertIsNone(row.picked_until)

    def test_state_machine_transitions_in_order(self) -> None:
        # Drive one role with manual transitions through the worker
        # primitives so we can assert the full queued → assigned →
        # in_progress → saved walk really happens (handle_research_*
        # only shows the start and end states from the outside).
        from yule_engineering.agents.job_queue.role_take_worker import (
            KIND_OPEN,
            RoleTakeWorker,
        )

        _seed_session(research_pack={"title": "x", "sources": []})
        queue = JobQueue()
        worker = RoleTakeWorker(queue=queue, heartbeats=HeartbeatStore())

        observed_states: List[str] = []

        def runner(job):
            observed_states.append(job.state.value)
            return "rendered"

        outcome = worker.run_one(
            session_id=SESSION_ID,
            role="ai-engineer",
            kind=KIND_OPEN,
            runner=runner,
        )
        # The runner observes the row in IN_PROGRESS — proves we went
        # through ASSIGNED first (via pick) then transitioned to
        # IN_PROGRESS before invoking the runner body.
        self.assertEqual(observed_states, ["in_progress"])
        assert outcome.job is not None
        # Final landing state.
        self.assertEqual(outcome.job.state, JobState.SAVED)


# ---------------------------------------------------------------------------
# 2 (deeper): role-scoped pick honors role filter under contention.
# ---------------------------------------------------------------------------


class RoleScopedPickTests(_Harness):
    def test_role_filter_isolates_contention(self) -> None:
        # Three queued role_take rows. Each role-scoped worker can
        # only see its own — even after another worker has picked
        # one of the others. Mirrors the M6 systemd scenario where
        # qa-engineer.service must not steal backend-engineer.service's row.
        from yule_engineering.agents.job_queue.role_take_worker import (
            JOB_TYPE_ROLE_TAKE,
            KIND_OPEN,
            RoleTakeWorker,
        )

        _seed_session(research_pack={"title": "x", "sources": []})
        queue = JobQueue()
        worker = RoleTakeWorker(queue=queue, heartbeats=HeartbeatStore())
        for role in ACTIVE_ROLES:
            worker.enqueue(
                session_id=SESSION_ID, role=role, kind=KIND_OPEN
            )

        # qa-engineer worker can only ever claim its own row, even when
        # ai-engineer's row is also queued.
        qa_picked = queue.pick(
            worker_id="qa-worker",
            job_types=[JOB_TYPE_ROLE_TAKE],
            roles=["qa-engineer"],
        )
        self.assertIsNotNone(qa_picked)
        assert qa_picked is not None
        self.assertEqual(qa_picked.role, "qa-engineer")

        # ai-engineer's worker still sees its own row queued.
        ai_picked = queue.pick(
            worker_id="ai-worker",
            job_types=[JOB_TYPE_ROLE_TAKE],
            roles=["ai-engineer"],
        )
        self.assertIsNotNone(ai_picked)
        assert ai_picked is not None
        self.assertEqual(ai_picked.role, "ai-engineer")


# ---------------------------------------------------------------------------
# 5: duplicate open-call markers don't produce duplicate jobs/comments.
# ---------------------------------------------------------------------------


class DuplicateMarkerTests(_Harness):
    def test_duplicate_open_marker_is_dedup_at_queue_level(self) -> None:
        # First call lands a SAVED row + a ResearchTurnOutcome.
        # Second call must NOT land another role_take row even if
        # the in-process dedup ring is bypassed (we clear it after
        # the first call to simulate a process restart).
        seeded = _seed_session(research_pack={"title": "x", "sources": []})
        marker = f"[research-open:{SESSION_ID}]"

        first = etr.handle_research_turn_message(
            role="ai-engineer",
            text=marker,
            session_loader=lambda _sid, _s=seeded: _s,
            pack_loader=lambda _s: None,
        )
        self.assertIsNotNone(first)

        # Simulate a member-bot restart: in-process dedup is empty
        # but the SQLite job_queue still remembers the SAVED row.
        etr._HANDLED_TURNS.clear()
        etr._HANDLED_TURNS_SET.clear()

        # However, the queue's "active states" filter excludes SAVED,
        # so a brand-new request *would* be allowed once a previous
        # one is terminal. To prove the in-flight dedup contract,
        # plant a fresh job in IN_PROGRESS and verify the second
        # call is silenced.
        from yule_engineering.agents.job_queue.role_take_worker import (
            KIND_OPEN,
            RoleTakeWorker,
        )

        worker = RoleTakeWorker(queue=JobQueue(), heartbeats=HeartbeatStore())
        in_flight, _ = worker.enqueue(
            session_id=SESSION_ID, role="ai-engineer", kind=KIND_OPEN
        )
        JobQueue().transition(in_flight.job_id, JobState.ASSIGNED)
        JobQueue().transition(in_flight.job_id, JobState.IN_PROGRESS)

        runner_calls = {"n": 0}

        # Replace _build_open_call_outcome so we can detect runner
        # invocation without going into the deliberation path.
        original = etr._build_open_call_outcome

        def counting(**kwargs):
            runner_calls["n"] += 1
            return original(**kwargs)

        etr._build_open_call_outcome = counting  # type: ignore[assignment]
        self.addCleanup(
            lambda: setattr(etr, "_build_open_call_outcome", original)
        )

        second = etr.handle_research_turn_message(
            role="ai-engineer",
            text=marker,
            session_loader=lambda _sid, _s=seeded: _s,
            pack_loader=lambda _s: None,
        )

        # Second call: queue-level dedup hits because there's an
        # IN_PROGRESS row → outcome is None (silent skip), runner is NOT
        # called, and the queue still has just the IN_PROGRESS row +
        # whatever SAVED rows the first call produced.
        self.assertIsNone(second)
        self.assertEqual(runner_calls["n"], 0)


# ---------------------------------------------------------------------------
# 6: runner failure lands failed_retryable; the queue surfaces the error
# string so the supervisor + status diagnostic can describe what failed.
# ---------------------------------------------------------------------------


class RunnerFailureTests(_Harness):
    def test_runner_exception_records_failed_retryable_with_error(self) -> None:
        seeded = _seed_session(research_pack={"title": "x", "sources": []})

        original = etr._build_open_call_outcome

        def boom(**_kwargs):
            raise RuntimeError("ollama 503 transient")

        etr._build_open_call_outcome = boom  # type: ignore[assignment]
        self.addCleanup(
            lambda: setattr(etr, "_build_open_call_outcome", original)
        )

        outcome = etr.handle_research_turn_message(
            role="ai-engineer",
            text=f"[research-open:{SESSION_ID}]",
            session_loader=lambda _sid, _s=seeded: _s,
            pack_loader=lambda _s: None,
        )
        # Member bot stays silent on failure — forum doesn't get a
        # half-baked comment.
        self.assertIsNone(outcome)

        retryable = [
            row
            for row in self._role_take_rows()
            if row.state == JobState.FAILED_RETRYABLE
        ]
        self.assertEqual(len(retryable), 1)
        self.assertIn("RuntimeError", retryable[0].result.get("error", ""))
        self.assertIn(
            "ollama 503 transient", retryable[0].result.get("error", "")
        )
        # Lease cleared so the M2 reaper / a manual requeue can pick it up.
        self.assertIsNone(retryable[0].picked_by)
        self.assertIsNone(retryable[0].picked_until)


# ---------------------------------------------------------------------------
# 7: per-role heartbeats. Three role workers should each emit a row
# under "eng-role-worker:<role>" so the supervisor sweep can list
# living workers per role.
# ---------------------------------------------------------------------------


class HeartbeatTests(_Harness):
    def test_per_role_worker_records_distinct_heartbeats(self) -> None:
        seeded = _seed_session(research_pack={"title": "x", "sources": []})
        marker = f"[research-open:{SESSION_ID}]"

        for role in ACTIVE_ROLES:
            etr.handle_research_turn_message(
                role=role,
                text=marker,
                session_loader=lambda _sid, _s=seeded: _s,
                pack_loader=lambda _s: None,
            )

        store = HeartbeatStore()
        beats = {
            role: store.get(service_id_for_role(role)) for role in ACTIVE_ROLES
        }
        for role, beat in beats.items():
            with self.subTest(role=role):
                # Each role's worker landed a heartbeat — supervisor
                # sweep would list it as alive.
                self.assertIsNotNone(beat, f"missing heartbeat for {role}")


# ---------------------------------------------------------------------------
# 8: session.extra carries research_pack + research_forum_thread_id
# across the queue trip. The role_take flow must not clobber existing
# session state.
# ---------------------------------------------------------------------------


class SessionExtraPreservationTests(_Harness):
    def test_session_extra_preserves_pack_and_forum_thread(self) -> None:
        original_pack = {
            "title": "k8s 운영",
            "summary": "stub summary",
            "sources": [
                {"url": "https://k8s.io/docs/", "title": "Docs"},
                {"url": "https://kubernetes.io/blog/", "title": "Blog"},
            ],
        }
        seeded = _seed_session(
            research_pack=original_pack, forum_thread_id=4242
        )
        marker = f"[research-open:{SESSION_ID}]"

        for role in ACTIVE_ROLES:
            etr.handle_research_turn_message(
                role=role,
                text=marker,
                session_loader=lambda _sid, _s=seeded: _s,
                pack_loader=lambda _s: None,
            )

        # Re-load the session from the store and assert the
        # research_pack + forum thread id survived the queue trip
        # and the role_research_results writes the worker may have
        # appended did not erase prior keys.
        reloaded = load_session(SESSION_ID)
        self.assertIsNotNone(reloaded)
        assert reloaded is not None
        extra = dict(reloaded.extra or {})
        self.assertEqual(
            extra.get("research_pack", {}).get("title"), "k8s 운영"
        )
        self.assertEqual(
            extra.get("research_pack", {}).get("sources"),
            original_pack["sources"],
        )
        self.assertEqual(extra.get("research_forum_thread_id"), 4242)
        # active_research_roles also preserved — the role selection
        # decision is not overwritten by the role_take worker.
        self.assertEqual(
            list(extra.get("active_research_roles") or []), ACTIVE_ROLES
        )


# ---------------------------------------------------------------------------
# Summary report: count + state breakdown for the operator-facing
# sanity check. Surfaces if any state is unexpected (e.g. a job stuck
# in ASSIGNED would show up here loudly).
# ---------------------------------------------------------------------------


class HarnessSummaryTests(_Harness):
    def test_full_session_state_breakdown_after_open_calls(self) -> None:
        seeded = _seed_session(research_pack={"title": "x", "sources": []})
        marker = f"[research-open:{SESSION_ID}]"
        for role in ACTIVE_ROLES:
            etr.handle_research_turn_message(
                role=role,
                text=marker,
                session_loader=lambda _sid, _s=seeded: _s,
                pack_loader=lambda _s: None,
            )

        rows = self._role_take_rows()
        # Three rows, one per active role, all SAVED.
        self.assertEqual(len(rows), 3)
        states = {row.state for row in rows}
        self.assertEqual(states, {JobState.SAVED})
        # Roles match active_research_roles exactly — no stray rows.
        self.assertEqual(
            {row.role for row in rows}, set(ACTIVE_ROLES)
        )
        # Every row carries kind="open" — chained dispatch / synthesis
        # would land different kinds and these markers don't trigger them.
        kinds = {(row.payload or {}).get("kind") for row in rows}
        self.assertEqual(kinds, {"open"})


if __name__ == "__main__":
    unittest.main()
