"""claude_decision_seam — Round 4 of #73 (+ Round 4-bis hardening).

Pin the runtime ↔ external decision-layer contract:

  * DecisionRequest payload roundtrips through to_payload.
  * DeterministicDecisionPort always answers ``advance=True``.
  * compose_decision_port returns the first actionable verdict.
  * a port that raises is logged + skipped, fallback wins.
  * non-actionable verdicts pass through to the next port + fallback.
  * runtime_checkable protocol matches a duck-typed port.

Round 4-bis additions (provider composition / env contract):

  * RecordOnlyDecisionPort captures every request without overriding
    the chain — ring buffer + JSONL append both work.
  * ExternalDecisionPort is no-op when its callable is missing,
    swallows raises, normalises Mapping returns, and never crashes
    the runtime callsite.
  * build_decision_port_from_env composes deterministic-only by
    default, layers record-only / external when env asks, and emits
    a DecisionPortBuildTrace that names every tier.
  * coerce_decision_request lifts a loose Mapping into the typed
    request the live tier expects.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.claude_decision_seam import (
    ClaudeDecisionPort,
    DECISION_KIND_DISCUSSION_FOLLOWUP,
    DECISION_KIND_RETRY_GUARD,
    DEFAULT_EXTERNAL_TIMEOUT_SECONDS,
    DEFAULT_RECORD_BUFFER_SIZE,
    DecisionInvocationTrace,
    DecisionPortBuildTrace,
    DecisionRequest,
    DecisionResponse,
    DeterministicDecisionPort,
    ENV_CLAUDE_DECISION_EXTERNAL_TIMEOUT,
    ENV_CLAUDE_DECISION_PROVIDER,
    ENV_CLAUDE_DECISION_RECORD_BUFFER,
    ENV_CLAUDE_DECISION_RECORD_PATH,
    ExternalDecisionPort,
    PROVIDER_DETERMINISTIC,
    PROVIDER_EXTERNAL,
    PROVIDER_RECORD,
    RecordOnlyDecisionPort,
    build_decision_port_from_env,
    coerce_decision_request,
    compose_decision_port,
    consult_decision_port,
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


# ---------------------------------------------------------------------------
# Round 4-bis — coerce_decision_request
# ---------------------------------------------------------------------------


class CoerceDecisionRequestTests(unittest.TestCase):
    def test_passthrough_when_already_typed(self) -> None:
        original = DecisionRequest(kind="x", summary="y")
        coerced = coerce_decision_request(original)
        self.assertIs(coerced, original)

    def test_lifts_envelope_keys_off_mapping(self) -> None:
        coerced = coerce_decision_request(
            {
                "kind": DECISION_KIND_RETRY_GUARD,
                "summary": "ci flaky?",
                "session_id": "S99",
                "missing_roles": ["qa-engineer"],
                "attempt": 3,
            }
        )
        self.assertEqual(coerced.kind, DECISION_KIND_RETRY_GUARD)
        self.assertEqual(coerced.session_id, "S99")
        # Non-envelope keys land in facts so the live prompt sees them.
        self.assertEqual(coerced.facts.get("missing_roles"), ["qa-engineer"])
        self.assertEqual(coerced.facts.get("attempt"), 3)

    def test_explicit_facts_override_loose_keys(self) -> None:
        coerced = coerce_decision_request(
            {
                "kind": "x",
                "facts": {"a": 1},
                "ignored": "loose",
            }
        )
        self.assertEqual(coerced.facts, {"a": 1})

    def test_unknown_type_raises(self) -> None:
        with self.assertRaises(TypeError):
            coerce_decision_request(object())


# ---------------------------------------------------------------------------
# Round 4-bis — RecordOnlyDecisionPort
# ---------------------------------------------------------------------------


class RecordOnlyDecisionPortTests(unittest.TestCase):
    def test_records_request_without_overriding_chain(self) -> None:
        port = RecordOnlyDecisionPort()
        response = port.decide(
            request=DecisionRequest(
                kind=DECISION_KIND_DISCUSSION_FOLLOWUP,
                summary="needs role takes",
                facts={"missing_roles": ["backend"]},
                session_id="S1",
            )
        )
        # Non-actionable so compose_decision_port falls through to the
        # next port (typically the deterministic fallback).
        self.assertFalse(response.is_actionable())
        self.assertIn("record-only", response.reason)
        recorded = port.recorded()
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["kind"], DECISION_KIND_DISCUSSION_FOLLOWUP)
        self.assertEqual(recorded[0]["session_id"], "S1")
        self.assertIn("_recorded_at", recorded[0])

    def test_ring_buffer_drops_oldest_when_capped(self) -> None:
        port = RecordOnlyDecisionPort(buffer_size=3)
        for i in range(5):
            port.decide(
                request=DecisionRequest(
                    kind="x",
                    summary=f"call {i}",
                    facts={"i": i},
                )
            )
        recorded = port.recorded()
        self.assertEqual(len(recorded), 3)
        # First two entries dropped — surviving entries cover i=2..4.
        survivors = [entry["facts"]["i"] for entry in recorded]
        self.assertEqual(survivors, [2, 3, 4])

    def test_jsonl_path_appends_one_line_per_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit" / "shadow.jsonl"
            port = RecordOnlyDecisionPort(jsonl_path=path)
            for i in range(3):
                port.decide(
                    request=DecisionRequest(
                        kind="x", summary=f"call {i}", facts={"i": i}
                    )
                )
            self.assertTrue(path.exists())
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            parsed = [json.loads(line) for line in lines]
            self.assertEqual([entry["facts"]["i"] for entry in parsed], [0, 1, 2])

    def test_falls_through_to_deterministic_in_chain(self) -> None:
        recorder = RecordOnlyDecisionPort()
        chain = compose_decision_port(recorder)
        response = chain.decide(
            request=DecisionRequest(kind="x", summary="y")
        )
        # Recorder captured the request, deterministic fallback drove
        # the actual verdict.
        self.assertEqual(len(recorder.recorded()), 1)
        self.assertTrue(response.advance)
        self.assertIn("deterministic", response.reason)


# ---------------------------------------------------------------------------
# Round 4-bis — ExternalDecisionPort
# ---------------------------------------------------------------------------


class ExternalDecisionPortTests(unittest.TestCase):
    def test_no_callable_returns_non_actionable(self) -> None:
        port = ExternalDecisionPort(callable=None)
        response = port.decide(request=DecisionRequest(kind="x", summary="y"))
        self.assertFalse(response.is_actionable())
        self.assertIn("not configured", response.reason)
        self.assertEqual(response.metadata.get("configured"), False)

    def test_decision_response_passthrough(self) -> None:
        verdict = DecisionResponse(
            skip=True, reason="dup", confidence="high"
        )

        def _live(*, request, timeout_seconds=None):  # noqa: ARG001
            return verdict

        port = ExternalDecisionPort(callable=_live)
        response = port.decide(request=DecisionRequest(kind="x", summary="y"))
        self.assertIs(response, verdict)

    def test_mapping_normalised_into_response(self) -> None:
        def _live(*, request, timeout_seconds):
            assert timeout_seconds == DEFAULT_EXTERNAL_TIMEOUT_SECONDS
            return {
                "skip": True,
                "reason": "duplicate of prior turn",
                "confidence": "high",
                "metadata": {"upstream": "stub"},
            }

        port = ExternalDecisionPort(callable=_live)
        response = port.decide(request=DecisionRequest(kind="x", summary="y"))
        self.assertTrue(response.skip)
        self.assertFalse(response.advance)
        self.assertEqual(response.reason, "duplicate of prior turn")
        self.assertEqual(response.metadata.get("upstream"), "stub")

    def test_callable_without_timeout_kwarg_still_called(self) -> None:
        captured = []

        def _live_no_timeout(*, request):
            captured.append(request)
            return DecisionResponse(advance=True, reason="ok")

        port = ExternalDecisionPort(callable=_live_no_timeout)
        response = port.decide(request=DecisionRequest(kind="x", summary="y"))
        self.assertTrue(response.advance)
        self.assertEqual(len(captured), 1)

    def test_raise_swallowed_into_fallback_response(self) -> None:
        def _broken(*, request, timeout_seconds=None):  # noqa: ARG001
            raise RuntimeError("upstream down")

        import logging

        seam_logger = logging.getLogger(
            "yule_orchestrator.agents.job_queue.claude_decision_seam"
        )
        previous = seam_logger.level
        seam_logger.setLevel(logging.CRITICAL)
        try:
            port = ExternalDecisionPort(callable=_broken)
            response = port.decide(
                request=DecisionRequest(kind="x", summary="y")
            )
        finally:
            seam_logger.setLevel(previous)
        self.assertFalse(response.is_actionable())
        self.assertEqual(response.reason, "external_raise")
        self.assertEqual(response.metadata.get("fallback"), True)

    def test_unsupported_return_type_falls_back(self) -> None:
        def _live(*, request, timeout_seconds=None):  # noqa: ARG001
            return 42

        import logging

        seam_logger = logging.getLogger(
            "yule_orchestrator.agents.job_queue.claude_decision_seam"
        )
        previous = seam_logger.level
        seam_logger.setLevel(logging.CRITICAL)
        try:
            port = ExternalDecisionPort(callable=_live)
            response = port.decide(
                request=DecisionRequest(kind="x", summary="y")
            )
        finally:
            seam_logger.setLevel(previous)
        self.assertEqual(response.reason, "external_bad_type")


# ---------------------------------------------------------------------------
# Round 4-bis — env-driven composition
# ---------------------------------------------------------------------------


class BuildDecisionPortFromEnvTests(unittest.TestCase):
    def test_default_chain_is_deterministic_only(self) -> None:
        port, trace = build_decision_port_from_env(env={})
        self.assertEqual(trace.requested, (PROVIDER_DETERMINISTIC,))
        self.assertEqual(trace.enabled, (PROVIDER_DETERMINISTIC,))
        self.assertEqual(trace.skipped, ())
        # Composed port still answers via the deterministic fallback.
        response = port.decide(request=DecisionRequest(kind="x", summary="y"))
        self.assertTrue(response.advance)

    def test_record_token_layers_record_only_above_fallback(self) -> None:
        port, trace = build_decision_port_from_env(
            env={ENV_CLAUDE_DECISION_PROVIDER: "record,deterministic"}
        )
        self.assertEqual(
            trace.enabled, (PROVIDER_RECORD, PROVIDER_DETERMINISTIC)
        )
        # Calling the chain still ends in deterministic advance, but
        # the record port captured the request along the way.
        response = port.decide(
            request=DecisionRequest(kind="x", summary="y", session_id="Sx")
        )
        self.assertTrue(response.advance)

    def test_external_token_skipped_without_factory(self) -> None:
        port, trace = build_decision_port_from_env(
            env={ENV_CLAUDE_DECISION_PROVIDER: "external,deterministic"}
        )
        self.assertEqual(trace.enabled, (PROVIDER_DETERMINISTIC,))
        self.assertEqual(len(trace.skipped), 1)
        self.assertEqual(trace.skipped[0][0], PROVIDER_EXTERNAL)
        # Chain still works.
        self.assertTrue(
            port.decide(request=DecisionRequest(kind="x", summary="y")).advance
        )

    def test_external_token_uses_factory_callable(self) -> None:
        captured = {}

        def _live(*, request, timeout_seconds=None):
            captured["called"] = True
            captured["timeout"] = timeout_seconds
            return DecisionResponse(skip=True, reason="external skip")

        def _factory(env):
            captured["env_seen"] = env
            return _live

        port, trace = build_decision_port_from_env(
            env={
                ENV_CLAUDE_DECISION_PROVIDER: "external,deterministic",
                ENV_CLAUDE_DECISION_EXTERNAL_TIMEOUT: "1.5",
            },
            external_callable_factory=_factory,
        )
        self.assertIn(PROVIDER_EXTERNAL, trace.enabled)
        response = port.decide(request=DecisionRequest(kind="x", summary="y"))
        self.assertTrue(response.skip)
        self.assertEqual(response.reason, "external skip")
        self.assertEqual(captured.get("timeout"), 1.5)
        # Factory must have received the env mapping it composed against.
        env_seen = captured.get("env_seen")
        self.assertIsNotNone(env_seen)
        self.assertEqual(
            env_seen.get(ENV_CLAUDE_DECISION_EXTERNAL_TIMEOUT), "1.5"
        )

    def test_unknown_token_recorded_as_skipped(self) -> None:
        _, trace = build_decision_port_from_env(
            env={ENV_CLAUDE_DECISION_PROVIDER: "warp,deterministic"}
        )
        self.assertEqual(trace.enabled, (PROVIDER_DETERMINISTIC,))
        self.assertEqual(trace.skipped[0][0], "warp")

    def test_record_path_writes_jsonl_via_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "audit" / "shadow.jsonl"
            port, _ = build_decision_port_from_env(
                env={
                    ENV_CLAUDE_DECISION_PROVIDER: "record,deterministic",
                    ENV_CLAUDE_DECISION_RECORD_PATH: str(target),
                    ENV_CLAUDE_DECISION_RECORD_BUFFER: "8",
                }
            )
            port.decide(
                request=DecisionRequest(
                    kind=DECISION_KIND_DISCUSSION_FOLLOWUP,
                    summary="missing role",
                    facts={"missing_roles": ["qa"]},
                    session_id="S77",
                )
            )
            self.assertTrue(target.exists())
            entry = json.loads(target.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["session_id"], "S77")
            self.assertEqual(
                entry["kind"], DECISION_KIND_DISCUSSION_FOLLOWUP
            )

    def test_record_buffer_clamped_to_safe_band(self) -> None:
        # A wildly out-of-band buffer setting should still produce a
        # working chain (clamped) rather than blowing up.
        port, _ = build_decision_port_from_env(
            env={
                ENV_CLAUDE_DECISION_PROVIDER: "record,deterministic",
                ENV_CLAUDE_DECISION_RECORD_BUFFER: "999999999",
            }
        )
        # Drive a single request to verify the chain composes.
        port.decide(request=DecisionRequest(kind="x", summary="y"))

    def test_factory_raise_disables_external_tier(self) -> None:
        def _bad_factory(_env):
            raise RuntimeError("bad config")

        import logging

        seam_logger = logging.getLogger(
            "yule_orchestrator.agents.job_queue.claude_decision_seam"
        )
        previous = seam_logger.level
        seam_logger.setLevel(logging.CRITICAL)
        try:
            port, trace = build_decision_port_from_env(
                env={ENV_CLAUDE_DECISION_PROVIDER: "external,deterministic"},
                external_callable_factory=_bad_factory,
            )
        finally:
            seam_logger.setLevel(previous)
        self.assertNotIn(PROVIDER_EXTERNAL, trace.enabled)
        self.assertTrue(
            port.decide(request=DecisionRequest(kind="x", summary="y")).advance
        )

    def test_trace_is_dataclass(self) -> None:
        _, trace = build_decision_port_from_env(env={})
        self.assertIsInstance(trace, DecisionPortBuildTrace)


# ---------------------------------------------------------------------------
# Round 4-ter — consult_decision_port + DecisionInvocationTrace
# ---------------------------------------------------------------------------


class ConsultDecisionPortTests(unittest.TestCase):
    """Pin the contract every callsite (autonomy producer / discussion
    follow-up / future implementation-candidate gate) shares."""

    def test_unwired_port_returns_non_actionable_with_trace(self) -> None:
        response, trace = consult_decision_port(
            None,
            request=DecisionRequest(kind=DECISION_KIND_RETRY_GUARD, summary="x"),
        )
        self.assertFalse(response.is_actionable())
        self.assertTrue(trace.fell_through)
        self.assertFalse(trace.raised)
        self.assertEqual(trace.provider, "unwired")
        self.assertEqual(trace.kind, DECISION_KIND_RETRY_GUARD)

    def test_skip_response_surfaces_through_trace(self) -> None:
        verdict = DecisionResponse(
            skip=True,
            reason="duplicate",
            confidence="high",
            metadata={"port": "external", "extra": 1},
        )

        class _Port:
            name = "external"

            def decide(self, *, request):
                return verdict

        response, trace = consult_decision_port(
            _Port(),
            request=DecisionRequest(kind=DECISION_KIND_RETRY_GUARD, summary="y"),
        )
        self.assertTrue(response.skip)
        self.assertTrue(trace.actionable)
        self.assertFalse(trace.fell_through)
        self.assertFalse(trace.raised)
        self.assertEqual(trace.provider, "external")
        self.assertEqual(trace.metadata.get("extra"), 1)

    def test_raise_swallowed_into_trace(self) -> None:
        class _Port:
            def decide(self, *, request):
                raise RuntimeError("upstream down")

        import logging

        seam_logger = logging.getLogger(
            "yule_orchestrator.agents.job_queue.claude_decision_seam"
        )
        previous = seam_logger.level
        seam_logger.setLevel(logging.CRITICAL)
        try:
            response, trace = consult_decision_port(
                _Port(),
                request=DecisionRequest(
                    kind=DECISION_KIND_RETRY_GUARD, summary="y"
                ),
            )
        finally:
            seam_logger.setLevel(previous)
        self.assertFalse(response.is_actionable())
        self.assertTrue(trace.raised)
        self.assertTrue(trace.fell_through)
        self.assertEqual(trace.raised_type, "RuntimeError")
        self.assertEqual(trace.provider, "raised")

    def test_bad_type_return_swallowed_into_trace(self) -> None:
        class _Port:
            def decide(self, *, request):
                return {"skip": True, "reason": "duck-typed"}

        response, trace = consult_decision_port(
            _Port(),
            request=DecisionRequest(kind="x", summary="y"),
        )
        # Duck-typed return is rejected — typed contract enforced.
        self.assertFalse(response.is_actionable())
        self.assertEqual(trace.provider, "bad_type")
        self.assertTrue(trace.fell_through)
        self.assertFalse(trace.raised)

    def test_advance_response_marked_actionable(self) -> None:
        class _Port:
            def decide(self, *, request):
                return DecisionResponse(
                    advance=True,
                    reason="proceed",
                    metadata={"port": "deterministic"},
                )

        response, trace = consult_decision_port(
            _Port(),
            request=DecisionRequest(kind="x", summary="y"),
        )
        self.assertTrue(response.advance)
        self.assertTrue(trace.actionable)
        self.assertFalse(trace.fell_through)
        self.assertEqual(trace.provider, "deterministic")

    def test_trace_payload_is_json_safe(self) -> None:
        # The trace gets stamped on AutonomyDispatch.payload, which is
        # eventually serialised to dashboards / audit JSONL. The
        # ``to_payload`` shape must therefore be flat enough to JSON
        # encode without a custom encoder.
        import json as _json

        response, trace = consult_decision_port(
            None, request=DecisionRequest(kind="x", summary="y")
        )
        payload = trace.to_payload()
        # round-trip round-trip
        encoded = _json.dumps(payload, sort_keys=True)
        decoded = _json.loads(encoded)
        self.assertEqual(decoded["kind"], "x")
        self.assertEqual(decoded["actionable"], False)
        self.assertEqual(decoded["provider"], "unwired")

    def test_trace_is_dataclass(self) -> None:
        _, trace = consult_decision_port(
            None, request=DecisionRequest(kind="x", summary="y")
        )
        self.assertIsInstance(trace, DecisionInvocationTrace)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
