"""claude_subprocess_adapter — Round 4-ter of #73.

The Round 4-bis seam ended with an :class:`ExternalDecisionPort` that
wraps an injected callable but never imported a live client. Round
4-ter lands the first concrete callable: a bounded ``claude -p``
subprocess adapter.

The contract this file pins:

  * The adapter is **off by default**. Without
    ``YULE_CLAUDE_DECISION_LIVE_ENABLED`` truthy the env factory
    returns ``None`` so the seam composes without a live tier.
  * Even with the env flag truthy, a missing binary on PATH yields
    ``None`` so a typo in the binary name doesn't surface as a real
    shell call.
  * Once enabled, the callable round-trips a ``claude -p`` invocation
    and normalises the JSON stdout into a :class:`DecisionResponse`.
  * Every failure mode (timeout / non-zero exit / empty stdout /
    malformed JSON / runner raise / unsupported payload) becomes a
    *non-actionable* response. The composer above falls through to
    the next port without the runtime ever stalling.
  * The non-actionable responses carry a stable
    ``metadata['subprocess_outcome']`` string so an operator reading
    the audit JSONL can grep for "why did the live tier punt".
"""

from __future__ import annotations

import json
import subprocess
import unittest
from typing import Any, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue.claude_decision_seam import (
    DECISION_KIND_RETRY_GUARD,
    DecisionRequest,
    DecisionResponse,
)
from yule_engineering.agents.job_queue.claude_subprocess_adapter import (
    ClaudeSubprocessConfig,
    DEFAULT_LIVE_BINARY,
    DEFAULT_LIVE_TIMEOUT_SECONDS,
    ENV_LIVE_BINARY,
    ENV_LIVE_ENABLED,
    ENV_LIVE_EXTRA_ARGS,
    ENV_LIVE_MODEL,
    ENV_LIVE_TIMEOUT,
    SUBPROCESS_OUTCOME_BAD_JSON,
    SUBPROCESS_OUTCOME_BINARY_MISSING,
    SUBPROCESS_OUTCOME_EMPTY,
    SUBPROCESS_OUTCOME_NONZERO_EXIT,
    SUBPROCESS_OUTCOME_OK,
    SUBPROCESS_OUTCOME_RUNNER_RAISED,
    SUBPROCESS_OUTCOME_TIMEOUT,
    SUBPROCESS_OUTCOME_UNSUPPORTED_PAYLOAD,
    build_claude_subprocess_callable,
    claude_subprocess_factory_from_env,
    render_subprocess_prompt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request() -> DecisionRequest:
    return DecisionRequest(
        kind=DECISION_KIND_RETRY_GUARD,
        summary="ci flaky?",
        facts={"pr_number": 42, "attempt": 3},
        session_id="S99",
    )


def _completed(
    *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=("claude", "-p"),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class _StubResolver:
    """Resolver that pretends every binary lives at ``/fake/<name>``."""

    def __call__(self, name: str) -> Optional[str]:
        return f"/fake/{name}"


_present_resolver = _StubResolver()


# ---------------------------------------------------------------------------
# render_subprocess_prompt
# ---------------------------------------------------------------------------


class RenderSubprocessPromptTests(unittest.TestCase):
    def test_payload_round_trips_through_json(self) -> None:
        prompt = render_subprocess_prompt(_request())
        # Last paragraph of the prompt is the JSON payload.
        json_blob = prompt.split("\n\n", 1)[1]
        decoded = json.loads(json_blob)
        self.assertEqual(decoded["kind"], DECISION_KIND_RETRY_GUARD)
        self.assertEqual(decoded["session_id"], "S99")
        self.assertEqual(decoded["facts"]["pr_number"], 42)

    def test_contract_reminder_documents_skip_advance(self) -> None:
        prompt = render_subprocess_prompt(_request())
        self.assertIn("skip", prompt)
        self.assertIn("advance", prompt)


# ---------------------------------------------------------------------------
# build_claude_subprocess_callable — happy path
# ---------------------------------------------------------------------------


class SubprocessCallableHappyPathTests(unittest.TestCase):
    def test_skip_payload_normalised_into_decision_response(self) -> None:
        captured: List[Mapping[str, Any]] = []

        def _runner(argv, **kwargs):
            captured.append({"argv": argv, "kwargs": kwargs})
            return _completed(
                stdout=json.dumps(
                    {
                        "skip": True,
                        "advance": False,
                        "reason": "duplicate of attempt 2",
                        "confidence": "high",
                        "metadata": {"upstream": "live"},
                    }
                )
            )

        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(timeout_seconds=2.0),
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        response = call(request=_request())

        self.assertIsInstance(response, DecisionResponse)
        self.assertTrue(response.skip)
        self.assertFalse(response.advance)
        self.assertEqual(response.reason, "duplicate of attempt 2")
        self.assertEqual(response.confidence, "high")
        self.assertEqual(response.metadata.get("upstream"), "live")
        self.assertEqual(response.metadata.get("provider"), "claude_subprocess")
        self.assertEqual(
            response.metadata.get("subprocess_outcome"),
            SUBPROCESS_OUTCOME_OK,
        )

        # Runner saw the resolved binary path + the prompt on stdin +
        # the requested timeout.
        self.assertEqual(captured[0]["argv"][0], "/fake/claude")
        self.assertEqual(captured[0]["argv"][1], "-p")
        self.assertIn(captured[0]["kwargs"].get("input"), (
            render_subprocess_prompt(_request()),
        ))
        self.assertEqual(captured[0]["kwargs"].get("timeout"), 2.0)

    def test_advance_payload_passes_through(self) -> None:
        def _runner(argv, **kwargs):
            return _completed(
                stdout=json.dumps(
                    {"advance": True, "reason": "go ahead", "confidence": "medium"}
                )
            )

        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(),
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        response = call(request=_request())
        self.assertTrue(response.advance)
        self.assertFalse(response.skip)
        self.assertEqual(response.reason, "go ahead")

    def test_model_and_extra_args_forwarded(self) -> None:
        captured = {}

        def _runner(argv, **kwargs):
            captured["argv"] = argv
            return _completed(stdout='{"advance": true, "reason": "ok"}')

        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(
                binary="claude",
                model="claude-haiku-4-5-20251001",
                extra_args=("--no-update", "--allowedTools=none"),
                timeout_seconds=1.0,
            ),
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        call(request=_request())

        argv = captured["argv"]
        self.assertEqual(argv[1], "-p")
        self.assertIn("--model", argv)
        self.assertIn("claude-haiku-4-5-20251001", argv)
        self.assertIn("--no-update", argv)
        self.assertIn("--allowedTools=none", argv)

    def test_per_call_timeout_override_takes_priority(self) -> None:
        captured = {}

        def _runner(argv, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return _completed(stdout='{"advance": true}')

        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(timeout_seconds=10.0),
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        call(request=_request(), timeout_seconds=1.5)
        self.assertEqual(captured["timeout"], 1.5)

    def test_log_chatter_around_json_still_parses(self) -> None:
        # CLI sometimes prints update notices before the JSON object —
        # the parser falls back to scanning for the first ``{...}``.
        chatter = (
            "tip-of-day: claude is up to date.\n"
            '{"skip": true, "reason": "live decline"}\n'
        )

        def _runner(argv, **kwargs):
            return _completed(stdout=chatter)

        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(),
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        response = call(request=_request())
        self.assertTrue(response.skip)
        self.assertEqual(response.reason, "live decline")


# ---------------------------------------------------------------------------
# build_claude_subprocess_callable — failure modes
# ---------------------------------------------------------------------------


class SubprocessCallableFailureTests(unittest.TestCase):
    def test_binary_missing_yields_non_actionable(self) -> None:
        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(binary="claude-nope"),
            runner=lambda *a, **kw: self.fail("runner must not be called"),
            binary_resolver=lambda _name: None,
        )
        response = call(request=_request())
        self.assertFalse(response.is_actionable())
        self.assertEqual(
            response.metadata.get("subprocess_outcome"),
            SUBPROCESS_OUTCOME_BINARY_MISSING,
        )

    def test_timeout_yields_non_actionable(self) -> None:
        def _runner(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(timeout_seconds=1.0),
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        response = call(request=_request())
        self.assertFalse(response.is_actionable())
        self.assertEqual(
            response.metadata.get("subprocess_outcome"),
            SUBPROCESS_OUTCOME_TIMEOUT,
        )

    def test_nonzero_exit_yields_non_actionable(self) -> None:
        def _runner(argv, **kwargs):
            return _completed(returncode=2, stderr="claude: rate limited\n")

        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(),
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        response = call(request=_request())
        self.assertFalse(response.is_actionable())
        self.assertEqual(
            response.metadata.get("subprocess_outcome"),
            SUBPROCESS_OUTCOME_NONZERO_EXIT,
        )
        self.assertIn("rate limited", response.reason)

    def test_empty_stdout_yields_non_actionable(self) -> None:
        def _runner(argv, **kwargs):
            return _completed(stdout="   \n   ")

        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(),
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        response = call(request=_request())
        self.assertFalse(response.is_actionable())
        self.assertEqual(
            response.metadata.get("subprocess_outcome"),
            SUBPROCESS_OUTCOME_EMPTY,
        )

    def test_malformed_json_yields_non_actionable(self) -> None:
        def _runner(argv, **kwargs):
            return _completed(stdout="not even close to json")

        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(),
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        response = call(request=_request())
        self.assertFalse(response.is_actionable())
        self.assertEqual(
            response.metadata.get("subprocess_outcome"),
            SUBPROCESS_OUTCOME_BAD_JSON,
        )

    def test_unsupported_payload_yields_non_actionable(self) -> None:
        # Top-level array is not a Mapping, so it falls back.
        def _runner(argv, **kwargs):
            return _completed(stdout="[1, 2, 3]")

        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(),
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        response = call(request=_request())
        self.assertFalse(response.is_actionable())
        self.assertEqual(
            response.metadata.get("subprocess_outcome"),
            SUBPROCESS_OUTCOME_UNSUPPORTED_PAYLOAD,
        )

    def test_runner_raise_yields_non_actionable(self) -> None:
        def _runner(argv, **kwargs):
            raise OSError("disk full")

        import logging

        adapter_logger = logging.getLogger(
            "yule_engineering.agents.job_queue.claude_subprocess_adapter"
        )
        previous = adapter_logger.level
        adapter_logger.setLevel(logging.CRITICAL)
        try:
            call = build_claude_subprocess_callable(
                ClaudeSubprocessConfig(),
                runner=_runner,
                binary_resolver=_present_resolver,
            )
            response = call(request=_request())
        finally:
            adapter_logger.setLevel(previous)
        self.assertFalse(response.is_actionable())
        self.assertEqual(
            response.metadata.get("subprocess_outcome"),
            SUBPROCESS_OUTCOME_RUNNER_RAISED,
        )

    def test_explicit_decline_falls_through_with_outcome_ok(self) -> None:
        # Live tier returns a well-formed JSON but neither skips nor
        # advances — the runtime should treat that as non-actionable
        # so the chain falls through, and the outcome marker should
        # still be ``ok`` (the call succeeded, the answer was just
        # "I don't know").
        def _runner(argv, **kwargs):
            return _completed(
                stdout=json.dumps(
                    {
                        "skip": False,
                        "advance": False,
                        "reason": "no opinion",
                        "metadata": {"upstream": "live"},
                    }
                )
            )

        call = build_claude_subprocess_callable(
            ClaudeSubprocessConfig(),
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        response = call(request=_request())
        self.assertFalse(response.is_actionable())
        self.assertEqual(
            response.metadata.get("subprocess_outcome"),
            SUBPROCESS_OUTCOME_OK,
        )
        self.assertEqual(response.metadata.get("upstream"), "live")


# ---------------------------------------------------------------------------
# claude_subprocess_factory_from_env — env-driven activation
# ---------------------------------------------------------------------------


class SubprocessFactoryEnvTests(unittest.TestCase):
    def test_disabled_when_flag_unset(self) -> None:
        result = claude_subprocess_factory_from_env({})
        self.assertIsNone(result)

    def test_disabled_when_flag_falsy(self) -> None:
        result = claude_subprocess_factory_from_env(
            {ENV_LIVE_ENABLED: "false"},
            binary_resolver=_present_resolver,
        )
        self.assertIsNone(result)

    def test_disabled_when_binary_missing(self) -> None:
        import logging

        adapter_logger = logging.getLogger(
            "yule_engineering.agents.job_queue.claude_subprocess_adapter"
        )
        previous = adapter_logger.level
        adapter_logger.setLevel(logging.CRITICAL)
        try:
            result = claude_subprocess_factory_from_env(
                {ENV_LIVE_ENABLED: "true"},
                binary_resolver=lambda _name: None,
            )
        finally:
            adapter_logger.setLevel(previous)
        self.assertIsNone(result)

    def test_active_when_flag_and_binary_present(self) -> None:
        captured = {}

        def _runner(argv, **kwargs):
            captured["argv"] = argv
            captured["timeout"] = kwargs.get("timeout")
            return _completed(stdout='{"advance": true, "reason": "ok"}')

        callable_ = claude_subprocess_factory_from_env(
            {
                ENV_LIVE_ENABLED: "1",
                ENV_LIVE_BINARY: "claude-mock",
                ENV_LIVE_MODEL: "claude-haiku-4-5-20251001",
                ENV_LIVE_TIMEOUT: "2.5",
                ENV_LIVE_EXTRA_ARGS: "--no-update,--allowedTools=none",
            },
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        self.assertIsNotNone(callable_)
        response = callable_(request=_request())
        self.assertTrue(response.advance)
        # Timeout clamped + forwarded.
        self.assertEqual(captured["timeout"], 2.5)
        argv = captured["argv"]
        self.assertEqual(argv[0], "/fake/claude-mock")
        self.assertIn("--model", argv)
        self.assertIn("claude-haiku-4-5-20251001", argv)
        self.assertIn("--no-update", argv)
        self.assertIn("--allowedTools=none", argv)

    def test_timeout_clamped_to_safe_band(self) -> None:
        captured = {}

        def _runner(argv, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return _completed(stdout='{"advance": true}')

        callable_ = claude_subprocess_factory_from_env(
            {ENV_LIVE_ENABLED: "true", ENV_LIVE_TIMEOUT: "9999"},
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        callable_(request=_request())
        self.assertLessEqual(captured["timeout"], 30.0)

        captured.clear()
        callable_ = claude_subprocess_factory_from_env(
            {ENV_LIVE_ENABLED: "true", ENV_LIVE_TIMEOUT: "0.0001"},
            runner=_runner,
            binary_resolver=_present_resolver,
        )
        callable_(request=_request())
        self.assertGreaterEqual(captured["timeout"], 0.5)


# ---------------------------------------------------------------------------
# Integration with the seam composer
# ---------------------------------------------------------------------------


class SubprocessAdapterSeamIntegrationTests(unittest.TestCase):
    """Wire the env factory through ``build_decision_port_from_env``.

    Pins that an operator who sets both
    ``YULE_CLAUDE_DECISION_PROVIDER=external,deterministic`` *and*
    ``YULE_CLAUDE_DECISION_LIVE_ENABLED=true`` ends up with a chain
    whose external tier is the subprocess callable.
    """

    def test_subprocess_adapter_drives_external_tier(self) -> None:
        from yule_engineering.agents.job_queue.claude_decision_seam import (
            ENV_CLAUDE_DECISION_PROVIDER,
            PROVIDER_EXTERNAL,
            build_decision_port_from_env,
        )

        def _runner(argv, **kwargs):
            return _completed(
                stdout=json.dumps(
                    {"skip": True, "reason": "live tier punts retry"}
                )
            )

        def _factory(env: Mapping[str, str]):
            return claude_subprocess_factory_from_env(
                env, runner=_runner, binary_resolver=_present_resolver
            )

        port, trace = build_decision_port_from_env(
            env={
                ENV_CLAUDE_DECISION_PROVIDER: "external,deterministic",
                ENV_LIVE_ENABLED: "true",
            },
            external_callable_factory=_factory,
        )
        self.assertIn(PROVIDER_EXTERNAL, trace.enabled)
        response = port.decide(request=_request())
        self.assertTrue(response.skip)
        self.assertEqual(response.reason, "live tier punts retry")

    def test_subprocess_disabled_falls_back_to_deterministic(self) -> None:
        from yule_engineering.agents.job_queue.claude_decision_seam import (
            ENV_CLAUDE_DECISION_PROVIDER,
            PROVIDER_EXTERNAL,
            build_decision_port_from_env,
        )

        def _factory(env: Mapping[str, str]):
            return claude_subprocess_factory_from_env(
                env, binary_resolver=_present_resolver
            )

        port, trace = build_decision_port_from_env(
            env={
                ENV_CLAUDE_DECISION_PROVIDER: "external,deterministic",
                # ENV_LIVE_ENABLED unset → factory returns None →
                # external tier marked skipped.
            },
            external_callable_factory=_factory,
        )
        self.assertNotIn(PROVIDER_EXTERNAL, trace.enabled)
        self.assertEqual(len(trace.skipped), 1)
        # Chain still answers via deterministic fallback.
        response = port.decide(request=_request())
        self.assertTrue(response.advance)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
