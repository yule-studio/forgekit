"""SelfImprovementDispatcher — end-to-end tick + dispatch tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, List, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.lifecycle.delegated_operator import (
    DelegatedRateLedger,
)
from yule_engineering.agents.lifecycle.problem_ledger import (
    ProblemLedger,
    ProblemStatus,
)
from yule_engineering.agents.lifecycle.runtime_self_improvement_loop import (
    SelfImprovementDispatcher,
)
from yule_engineering.agents.lifecycle.self_improvement import (
    SEVERITY_HIGH,
    SelfImprovementSignal,
)
from yule_engineering.agents.lifecycle.self_improvement_seed_detectors import (
    ObservationContext,
    SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH,
    SIGNAL_QA_TEST_MISCLASSIFICATION,
)
from yule_engineering.agents.lifecycle.self_improvement_worktree import (
    InMemoryWorktreeRegistry,
    WorktreeProvisioner,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _RecordingProvisioner:
    """In-memory worktree provisioner — records create/exists/remove."""

    def __init__(self) -> None:
        self.created: list = []
        self.removed: list = []
        self._existing: set = set()

    def create(self, *, branch: str, path: str, base_branch: str, cwd: str) -> None:
        self.created.append((branch, path, base_branch, cwd))
        self._existing.add((branch, path))

    def exists(self, *, branch: str, path: str) -> bool:
        return (branch, path) in self._existing

    def remove(self, *, branch: str, path: str, force: bool = False) -> None:
        self.removed.append((branch, path, force))
        self._existing.discard((branch, path))


def _job(
    *,
    job_type: str,
    state: str = "saved",
    payload: Mapping[str, Any] = None,
    result: Mapping[str, Any] = None,
    job_id: str = "j",
) -> Any:
    return SimpleNamespace(
        job_type=job_type,
        state=SimpleNamespace(value=state),
        payload=payload or {},
        result=result or {},
        job_id=job_id,
    )


def _session(
    *,
    session_id: str = "s",
    prompt: str = "",
    extra: Mapping[str, Any] = None,
) -> Any:
    return SimpleNamespace(
        session_id=session_id, prompt=prompt, extra=extra or {}
    )


def _make_observation(*, jobs: Sequence[Any] = (), sessions: Sequence[Any] = ()) -> ObservationContext:
    return ObservationContext(
        jobs=jobs,
        failed_jobs=jobs,
        sessions=sessions,
        heartbeats={},
        now=datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Dispatcher tick tests
# ---------------------------------------------------------------------------


class DispatcherDelegatedPathTests(unittest.TestCase):
    """signal → triage → delegated_ok → executor handoff hook 호출 검증."""

    def test_engineering_write_signal_routes_to_executor_with_worktree(self) -> None:
        provisioner = _RecordingProvisioner()
        executor_calls: list = []
        operator_calls: list = []

        observation = _make_observation(
            jobs=(
                _job(
                    job_type="approval_post",
                    payload={"approval_kind": "engineering_write"},
                    result={"last_no_match_reason": "no_matching_approval"},
                ),
            )
        )

        dispatcher = SelfImprovementDispatcher(
            observation_provider=lambda: observation,
            problem_ledger=ProblemLedger(),
            rate_ledger=DelegatedRateLedger(),
            worktree_registry=InMemoryWorktreeRegistry(),
            worktree_provisioner=provisioner,
            operator_action_hook=lambda **kw: (
                operator_calls.append(kw) or "op-1"
            ),
            executor_handoff_hook=lambda **kw: (
                executor_calls.append(kw) or "exec-job-1"
            ),
        )

        report = dispatcher.run_tick()
        self.assertTrue(len(report.handled) >= 1)
        # backend signal should be delegated → executor hook called, no
        # operator action card.
        target = next(
            o
            for o in report.handled
            if o.problem.signal_id == SIGNAL_ENGINEERING_WRITE_REPLY_MISMATCH
        )
        self.assertTrue(target.problem.delegated_ok)
        self.assertEqual(target.final_status, ProblemStatus.FIXING)
        self.assertEqual(target.executor_handoff_job_id, "exec-job-1")
        self.assertIsNone(target.operator_action_id)
        self.assertEqual(len(provisioner.created), 1)
        self.assertEqual(len(executor_calls), 1)
        self.assertEqual(len(operator_calls), 0)
        # worktree branch is stamped on the problem.
        self.assertIsNotNone(target.problem.worktree_branch)
        self.assertTrue(
            target.problem.worktree_branch.startswith("codex/self-improve/")
        )

    def test_duplicate_signal_reuses_worktree(self) -> None:
        provisioner = _RecordingProvisioner()
        executor_calls: list = []

        observation = _make_observation(
            jobs=(
                _job(
                    job_type="approval_post",
                    payload={"approval_kind": "engineering_write"},
                    result={"last_no_match_reason": "x"},
                ),
            )
        )

        dispatcher = SelfImprovementDispatcher(
            observation_provider=lambda: observation,
            problem_ledger=ProblemLedger(),
            rate_ledger=DelegatedRateLedger(),
            worktree_registry=InMemoryWorktreeRegistry(),
            worktree_provisioner=provisioner,
            executor_handoff_hook=lambda **kw: (
                executor_calls.append(kw) or "exec-1"
            ),
        )
        dispatcher.run_tick()
        first_created = len(provisioner.created)
        # Same observation -> same signal -> same problem signature.
        dispatcher.run_tick()
        # Worktree should NOT be created twice.
        self.assertEqual(len(provisioner.created), first_created)


class DispatcherOperatorEscalationTests(unittest.TestCase):
    """triage hint = needs_human → operator action hook 호출 / executor skip."""

    def test_unknown_signal_routes_to_operator(self) -> None:
        operator_calls: list = []
        executor_calls: list = []

        # Build an observation that triggers no seed signal — we'll
        # inject a synthetic signal via the dispatch_fn directly.
        observation = _make_observation()

        dispatcher = SelfImprovementDispatcher(
            observation_provider=lambda: observation,
            problem_ledger=ProblemLedger(),
            rate_ledger=DelegatedRateLedger(),
            worktree_registry=InMemoryWorktreeRegistry(),
            operator_action_hook=lambda **kw: (
                operator_calls.append(kw) or "op-1"
            ),
            executor_handoff_hook=lambda **kw: (
                executor_calls.append(kw) or "exec-1"
            ),
        )

        outcome = dispatcher.dispatch_fn(
            SelfImprovementSignal(
                signal="brand_new_unknown_signal",
                severity="medium",
                summary="something nobody has triaged before",
                evidence={"topic_key": "k"},
                detected_at="2026-05-16T12:00:00+00:00",
            ),
            plan=None,
        )
        # Unknown signal → fallback to tech-lead + needs_human.
        self.assertFalse(outcome.problem.delegated_ok)
        self.assertEqual(outcome.final_status, ProblemStatus.WAITING_OPERATOR)
        self.assertEqual(outcome.operator_action_id, "op-1")
        self.assertIsNone(outcome.executor_handoff_job_id)
        self.assertEqual(len(executor_calls), 0)


class DispatcherTerminalSkipTests(unittest.TestCase):
    """terminal problem 은 매 tick 마다 재처리되지 않아야 한다."""

    def test_completed_problem_is_skipped(self) -> None:
        executor_calls: list = []

        observation = _make_observation(
            jobs=(
                _job(
                    job_type="approval_post",
                    payload={"approval_kind": "engineering_write"},
                    result={"last_no_match_reason": "x"},
                ),
            )
        )
        ledger = ProblemLedger()
        dispatcher = SelfImprovementDispatcher(
            observation_provider=lambda: observation,
            problem_ledger=ledger,
            rate_ledger=DelegatedRateLedger(),
            worktree_registry=InMemoryWorktreeRegistry(),
            worktree_provisioner=_RecordingProvisioner(),
            executor_handoff_hook=lambda **kw: (
                executor_calls.append(kw) or "exec-1"
            ),
        )
        dispatcher.run_tick()
        # Mark the problem as completed.
        sig = ledger.all()[0].signature
        ledger.transition(sig, status=ProblemStatus.COMPLETED)
        executor_before = len(executor_calls)
        dispatcher.run_tick()
        # No new executor handoff for the completed problem.
        self.assertEqual(len(executor_calls), executor_before)


class DispatcherRateLimitTests(unittest.TestCase):
    """delegated rate-limit 가 ledger 와 통합돼 동작한다."""

    def test_retry_cap_forces_escalation_after_N_ticks(self) -> None:
        operator_calls: list = []
        executor_calls: list = []

        observation = _make_observation(
            jobs=(
                _job(
                    job_type="approval_post",
                    payload={"approval_kind": "engineering_write"},
                    result={"last_no_match_reason": "x"},
                ),
            )
        )
        dispatcher = SelfImprovementDispatcher(
            observation_provider=lambda: observation,
            problem_ledger=ProblemLedger(),
            rate_ledger=DelegatedRateLedger(),
            worktree_registry=InMemoryWorktreeRegistry(),
            worktree_provisioner=_RecordingProvisioner(),
            operator_action_hook=lambda **kw: (
                operator_calls.append(kw) or "op-x"
            ),
            executor_handoff_hook=lambda **kw: (
                executor_calls.append(kw) or "exec-x"
            ),
        )
        # Default retry cap is 3 — 4th tick should escalate.
        for _ in range(3):
            dispatcher.run_tick()
        dispatcher.run_tick()
        # By the 4th tick the rate-limit forces escalation:
        self.assertTrue(len(operator_calls) >= 1)


if __name__ == "__main__":
    unittest.main()
