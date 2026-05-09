"""claude_decision_seam — Round 4 of #73.

Pin the runtime ↔ external decision-layer contract:

  * DecisionRequest payload roundtrips through to_payload.
  * DeterministicDecisionPort always answers ``advance=True``.
  * compose_decision_port returns the first actionable verdict.
  * a port that raises is logged + skipped, fallback wins.
  * non-actionable verdicts pass through to the next port + fallback.
  * runtime_checkable protocol matches a duck-typed port.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.claude_decision_seam import (
    ClaudeDecisionPort,
    DECISION_KIND_DISCUSSION_FOLLOWUP,
    DecisionRequest,
    DecisionResponse,
    DeterministicDecisionPort,
    compose_decision_port,
)


class _StubPort:
    def __init__(self, response: DecisionResponse, name: str = "stub") -> None:
        self.response = response
        self.calls = 0
        self.name = name

    def decide(self, *, request: DecisionRequest) -> DecisionResponse:
        self.calls += 1
        return self.response


class _RaisingPort:
    name = "raising"

    def decide(self, *, request: DecisionRequest) -> DecisionResponse:
        raise RuntimeError("upstream down")


class DecisionRequestPayloadTests(unittest.TestCase):
    def test_to_payload_is_serialisable(self) -> None:
        req = DecisionRequest(
            kind=DECISION_KIND_DISCUSSION_FOLLOWUP,
            summary="needs role takes",
            facts={"missing_roles": ["backend"]},
            session_id="S1",
            requested_at="2026-05-09T00:00:00+00:00",
        )
        payload = req.to_payload()
        self.assertEqual(payload["kind"], DECISION_KIND_DISCUSSION_FOLLOWUP)
        self.assertEqual(payload["facts"], {"missing_roles": ["backend"]})
        self.assertEqual(payload["session_id"], "S1")


class DeterministicDecisionPortTests(unittest.TestCase):
    def test_always_advances(self) -> None:
        port = DeterministicDecisionPort()
        response = port.decide(
            request=DecisionRequest(kind="x", summary="y")
        )
        self.assertTrue(response.advance)
        self.assertFalse(response.skip)
        self.assertTrue(response.is_actionable())
        self.assertIn("deterministic", response.reason)


class ComposeDecisionPortTests(unittest.TestCase):
    def test_first_actionable_wins(self) -> None:
        first = _StubPort(
            DecisionResponse(advance=False, skip=False),  # non-actionable
            name="first",
        )
        second = _StubPort(
            DecisionResponse(skip=True, reason="duplicate"),
            name="second",
        )
        third = _StubPort(
            DecisionResponse(advance=True, reason="should not be reached"),
            name="third",
        )
        port = compose_decision_port(first, second, third)
        response = port.decide(
            request=DecisionRequest(kind="next_task", summary="x")
        )
        self.assertTrue(response.skip)
        self.assertEqual(response.reason, "duplicate")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)
        self.assertEqual(third.calls, 0)

    def test_raising_port_is_skipped(self) -> None:
        raising = _RaisingPort()
        winner = _StubPort(
            DecisionResponse(advance=True, reason="ok"),
            name="winner",
        )
        # Silence the seam logger so the deliberate raise doesn't
        # spam test output.
        import logging

        seam_logger = logging.getLogger(
            "yule_orchestrator.agents.job_queue.claude_decision_seam"
        )
        previous = seam_logger.level
        seam_logger.setLevel(logging.CRITICAL)
        try:
            port = compose_decision_port(raising, winner)
            response = port.decide(
                request=DecisionRequest(kind="x", summary="y")
            )
        finally:
            seam_logger.setLevel(previous)
        self.assertTrue(response.advance)
        self.assertEqual(winner.calls, 1)

    def test_falls_back_when_no_port_actionable(self) -> None:
        empty = _StubPort(
            DecisionResponse(advance=False, skip=False),
            name="empty",
        )
        port = compose_decision_port(empty)
        response = port.decide(
            request=DecisionRequest(kind="x", summary="y")
        )
        # Falls through to the deterministic fallback.
        self.assertTrue(response.advance)
        self.assertIn("deterministic", response.reason)


class ProtocolDuckTypingTests(unittest.TestCase):
    def test_stub_port_satisfies_protocol(self) -> None:
        stub = _StubPort(DecisionResponse(advance=True))
        # ``runtime_checkable`` Protocol — isinstance must succeed for
        # any object that exposes a callable ``decide``.
        self.assertIsInstance(stub, ClaudeDecisionPort)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
