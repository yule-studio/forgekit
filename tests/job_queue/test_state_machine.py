"""State machine for the always-on engineering queue.

Pin the lifecycle invariants so any future edit to the transition
graph fails loudly. The graph is small on purpose — divergent paths
usually indicate a bug, not a missing edge.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.state_machine import (
    JobState,
    STATE_TRANSITIONS,
    TERMINAL_STATES,
    is_terminal,
    validate_transition,
)


class TerminalStatesTests(unittest.TestCase):
    def test_saved_and_failed_terminal_are_terminal(self) -> None:
        self.assertTrue(is_terminal(JobState.SAVED))
        self.assertTrue(is_terminal(JobState.FAILED_TERMINAL))
        self.assertEqual(
            TERMINAL_STATES,
            frozenset({JobState.SAVED, JobState.FAILED_TERMINAL}),
        )

    def test_terminal_states_have_no_outgoing_transitions(self) -> None:
        for state in TERMINAL_STATES:
            self.assertEqual(
                STATE_TRANSITIONS[state],
                frozenset(),
                f"{state.value} must not have outgoing edges",
            )


class HappyPathTransitionTests(unittest.TestCase):
    """The natural lifecycle (discovered → … → saved) must be allowed."""

    def test_role_take_happy_path(self) -> None:
        chain = [
            (JobState.DISCOVERED, JobState.QUEUED),
            (JobState.QUEUED, JobState.ASSIGNED),
            (JobState.ASSIGNED, JobState.IN_PROGRESS),
            (JobState.IN_PROGRESS, JobState.READY_FOR_OBSIDIAN),
            (JobState.READY_FOR_OBSIDIAN, JobState.SAVED),
        ]
        for current, target in chain:
            with self.subTest(transition=f"{current.value}->{target.value}"):
                validate_transition(current, target)  # must not raise

    def test_research_branch_goes_through_researching(self) -> None:
        validate_transition(JobState.IN_PROGRESS, JobState.RESEARCHING)
        validate_transition(JobState.RESEARCHING, JobState.IN_PROGRESS)
        validate_transition(JobState.RESEARCHING, JobState.READY_FOR_OBSIDIAN)

    def test_approval_branch_returns_to_in_progress(self) -> None:
        validate_transition(JobState.IN_PROGRESS, JobState.PENDING_APPROVAL)
        validate_transition(JobState.PENDING_APPROVAL, JobState.IN_PROGRESS)


class RetryPathTransitionTests(unittest.TestCase):
    def test_failed_retryable_can_requeue(self) -> None:
        validate_transition(JobState.IN_PROGRESS, JobState.FAILED_RETRYABLE)
        validate_transition(JobState.FAILED_RETRYABLE, JobState.QUEUED)

    def test_failed_retryable_can_escalate_to_terminal(self) -> None:
        validate_transition(
            JobState.FAILED_RETRYABLE, JobState.FAILED_TERMINAL
        )

    def test_assigned_can_drop_back_to_queued(self) -> None:
        # If a worker picked a job but then realised it's not ready
        # (e.g. heartbeat lost), the lease reaper drops it back via
        # failed_retryable → queued. ASSIGNED → QUEUED is also OK so
        # the worker can release voluntarily.
        validate_transition(JobState.ASSIGNED, JobState.QUEUED)


class IllegalTransitionTests(unittest.TestCase):
    def test_terminal_states_reject_outgoing(self) -> None:
        with self.assertRaises(ValueError):
            validate_transition(JobState.SAVED, JobState.QUEUED)
        with self.assertRaises(ValueError):
            validate_transition(JobState.FAILED_TERMINAL, JobState.QUEUED)

    def test_self_transition_is_rejected(self) -> None:
        # Same-state self-loop is rejected so callers don't write
        # "no-op" updates that look like state changes in the audit
        # trail.
        with self.assertRaises(ValueError):
            validate_transition(JobState.QUEUED, JobState.QUEUED)

    def test_skipping_states_is_rejected(self) -> None:
        # discovered must go through queued — can't jump straight to
        # in_progress. Catches the common refactor mistake of moving
        # a job to the worker without enqueueing it first.
        with self.assertRaises(ValueError):
            validate_transition(JobState.DISCOVERED, JobState.IN_PROGRESS)


if __name__ == "__main__":
    unittest.main()
