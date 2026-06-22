"""GW4-B — approve → REAL gated execution bridge regression.

Proves the self-management loop closes at the orchestration+evidence level
(``goal → tick → approve → EXECUTE → verify → evidence``) WITHOUT faking anything:

- approved + SAFE packet → the bridge runs the EXISTING approval chain
  (``run_internal_chain`` + ``can_specialist_execute``) + the runtime gate
  (``authorize_runtime_execution``) + ``validate_execution``, then writes
  ``execution`` + ``verification`` evidence to the goal (loop closed);
- RISKY / BLOCKED packet → NOT executed; an honest ``blocked`` outcome + a
  ``decision`` refusal record (no fabricated "executed");
- unknown / unresolvable packet → ``error`` outcome, no execution;
- the produced commit message carries a valid ``Forgekit-Agent`` trailer that the
  #346 commit-governance validator accepts (``is_known``);
- the goal NEVER transitions to ``done`` (that needs verified execution evidence
  the bridge does not manufacture — physical mutation stays BoundedMutator-gated).

Hermetic: signals drive the packets; ``$FORGEKIT_HOME`` is a tempdir so the store
writes stay isolated.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from forgekit_goal import Goal, GoalStatus, GoalStore, transitions
from forgekit_runtime.selfimprove import (
    OUTCOME_BLOCKED,
    OUTCOME_ERROR,
    OUTCOME_EXECUTED,
    execute_approved_packet,
    goal_tick,
)
from forgekit_runtime.selfimprove import packet as P


class _Signal:
    def __init__(self, text: str) -> None:
        self.text = text


def _clock():
    n = {"i": 0}

    def now() -> str:
        n["i"] += 1
        return f"2026-06-22T03:00:{n['i']:02d}+00:00"

    return now


class ExecuteBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self._repo = tempfile.TemporaryDirectory()
        self.env = {"FORGEKIT_HOME": self._home.name}

    def tearDown(self) -> None:
        self._home.cleanup()
        self._repo.cleanup()

    def _goal_with_packet(self, signal_text: str) -> Goal:
        now = _clock()
        g = Goal.create("self-manage ForgeKit", mode="auto", now=now)
        g = transitions.apply(g, GoalStatus.ACTIVE, now=now)
        res = goal_tick.tick_goal(g, self._repo.name, signals=[_Signal(signal_text)], now=now)
        GoalStore(env=self.env).save(res.goal)
        return res.goal

    # --- approved + safe → executed, evidence written (loop closed) ---------
    def test_approved_safe_runs_gate_and_writes_execution_verification(self) -> None:
        goal = self._goal_with_packet("콘솔 도움말 문구 개선")
        self.assertEqual(len(goal.packets), 1)

        out = execute_approved_packet(goal, env=self.env)

        self.assertEqual(out.outcome, OUTCOME_EXECUTED)
        self.assertTrue(out.executed)
        self.assertEqual(out.action_class, "safe")
        self.assertTrue(out.executor_id)  # a real executing identity

        # evidence persisted to the store → loop closed at evidence level
        reloaded = GoalStore(env=self.env).get(goal.id)
        kinds = [e.kind for e in reloaded.evidence]
        self.assertIn("execution", kinds)
        self.assertIn("verification", kinds)
        # the approver + executor are recorded on the execution evidence
        exec_ev = next(e for e in reloaded.evidence if e.kind == "execution")
        self.assertIn(out.executor_id, exec_ev.summary)

    def test_approved_safe_signature_matches_surface_call(self) -> None:
        """``goal_surface`` calls ``fn(goal, env=env)`` — that exact shape must work."""

        goal = self._goal_with_packet("콘솔 도움말 문구 개선")
        out = execute_approved_packet(goal, env=self.env)  # positional goal + env kw only
        self.assertTrue(out.executed)

    # --- risky / blocked → NOT executed, honest outcome ---------------------
    def test_risky_packet_not_executed_blocked_outcome(self) -> None:
        goal = self._goal_with_packet("auth 권한 흐름 대규모 변경")
        out = execute_approved_packet(goal, env=self.env)
        self.assertEqual(out.outcome, OUTCOME_BLOCKED)
        self.assertFalse(out.executed)
        self.assertTrue(out.reasons)  # honest reasons, not a fake success

        reloaded = GoalStore(env=self.env).get(goal.id)
        self.assertNotIn("execution", [e.kind for e in reloaded.evidence])

    def test_blocked_packet_not_executed(self) -> None:
        goal = self._goal_with_packet("deploy 시크릿 회전")
        out = execute_approved_packet(goal, env=self.env)
        self.assertEqual(out.outcome, OUTCOME_BLOCKED)
        self.assertFalse(out.executed)
        reloaded = GoalStore(env=self.env).get(goal.id)
        self.assertNotIn("execution", [e.kind for e in reloaded.evidence])

    # --- unknown / unresolvable packet → error, no execution ---------------
    def test_unknown_packet_id_errors_no_execution(self) -> None:
        goal = self._goal_with_packet("콘솔 도움말 문구 개선")
        out = execute_approved_packet(goal, packet_id="packet-does-not-exist", env=self.env)
        self.assertEqual(out.outcome, OUTCOME_ERROR)
        self.assertFalse(out.executed)

    def test_goal_with_no_packets_errors(self) -> None:
        now = _clock()
        g = Goal.create("empty goal", now=now)
        g = transitions.apply(g, GoalStatus.ACTIVE, now=now)
        out = execute_approved_packet(g, env=self.env)
        self.assertEqual(out.outcome, OUTCOME_ERROR)
        self.assertFalse(out.executed)

    # --- attribution: produced commit carries a valid Forgekit-Agent trailer
    def test_commit_message_carries_known_forgekit_agent_trailer(self) -> None:
        goal = self._goal_with_packet("콘솔 도움말 문구 개선")
        out = execute_approved_packet(goal, env=self.env)
        self.assertTrue(out.executed)
        self.assertIn("Forgekit-Agent:", out.commit_message)
        self.assertIn(f"Forgekit-Agent: {out.executor_id}", out.commit_message)

    def test_commit_trailer_passes_346_validator(self) -> None:
        """The trailer-stamped message passes the #346 commit-governance validator."""

        from scripts.ci_check_commit_messages import check_identity_binding

        goal = self._goal_with_packet("콘솔 도움말 문구 개선")
        out = execute_approved_packet(goal, env=self.env)
        violations = check_identity_binding([("deadbeefcafe", out.commit_message)])
        # no unknown-agent / unknown-approver hard failures
        self.assertEqual([v.reason for v in violations if v.severity == "error"], [])

    def test_blocked_run_produces_no_fake_approved_commit(self) -> None:
        goal = self._goal_with_packet("auth 권한 흐름 대규모 변경")
        out = execute_approved_packet(goal, env=self.env)
        self.assertFalse(out.executed)
        self.assertEqual(out.commit_message, "")  # no fabricated approval trailer

    # --- never marks the goal done ----------------------------------------
    def test_goal_never_transitions_to_done(self) -> None:
        goal = self._goal_with_packet("콘솔 도움말 문구 개선")
        out = execute_approved_packet(goal, env=self.env)
        self.assertTrue(out.executed)
        reloaded = GoalStore(env=self.env).get(goal.id)
        self.assertNotEqual(reloaded.status, GoalStatus.DONE)

    def test_awaiting_approval_goal_moves_to_active_on_authorized_run(self) -> None:
        now = _clock()
        g = Goal.create("self-manage ForgeKit", mode="auto", now=now)
        g = transitions.apply(g, GoalStatus.ACTIVE, now=now)
        res = goal_tick.tick_goal(g, self._repo.name,
                                  signals=[_Signal("콘솔 도움말 문구 개선")], now=now)
        g = transitions.apply(res.goal, GoalStatus.AWAITING_APPROVAL, now=now)
        GoalStore(env=self.env).save(g)
        out = execute_approved_packet(g, env=self.env)
        self.assertTrue(out.executed)
        reloaded = GoalStore(env=self.env).get(g.id)
        self.assertEqual(reloaded.status, GoalStatus.ACTIVE)
        self.assertNotEqual(reloaded.status, GoalStatus.DONE)


if __name__ == "__main__":
    unittest.main()
