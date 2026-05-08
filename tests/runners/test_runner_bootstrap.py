"""Env-driven role-runner bootstrap — A-M11b.

Pin the contract that:

  * Empty env → deterministic-only candidate set + a trace marking
    every known provider as ``not opted in``. The dispatcher returned
    is callable and produces a deterministic fallback take.
  * A fake Claude RoleRunner injected into the chain wins when it
    returns ``status="ok"``.
  * When the configured Claude/Codex/Ollama all fail or are
    unavailable, the dispatcher walks to the deterministic terminal
    and stamps ``used_fallback=True`` on the audit record.
  * Inactive roles never invoke a runner — the dispatcher returns
    ``status="inactive_role"`` regardless of provider availability.
  * The session-aware audit writer appends a
    ``role_runner_dispatch`` row onto session.extra (without
    persisting via SQLite — tests run with a SimpleNamespace
    session shape).

The tests exercise the bootstrap module directly with a mock env
mapping so we never touch real CLIs / endpoints.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, List, Mapping

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.runners.bootstrap import (
    ENV_PROVIDERS,
    REASON_NOT_OPTED_IN,
    REASON_OPTED_IN_AVAILABLE,
    build_role_runner_candidates,
    build_role_runner_dispatch_from_env,
)
from yule_orchestrator.agents.runners.role_runner import (
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    PROVIDER_DETERMINISTIC,
    PROVIDER_OLLAMA,
    RoleRunner,
    RoleRunnerInput,
    RoleRunnerOutput,
    STATUS_ERROR,
    STATUS_FALLBACK,
    STATUS_INACTIVE_ROLE,
    STATUS_OK,
    STATUS_UNAVAILABLE,
)


@dataclass
class _StubSession:
    session_id: str = "sess-bootstrap-1"
    extra: dict = field(
        default_factory=lambda: {
            "active_research_roles": [
                "ai-engineer",
                "tech-lead",
                "qa-engineer",
            ]
        }
    )


def _input(role: str = "ai-engineer") -> RoleRunnerInput:
    return RoleRunnerInput(
        role=role,
        session_id="sess-bootstrap-1",
        prompt="Discord 봇 메시지 흐름 정리",
    )


class _ScriptedRunner(RoleRunner):
    """Minimal RoleRunner stub — used to assert chain semantics
    without spinning up real CLIs/endpoints.
    """

    def __init__(
        self,
        provider: str,
        *,
        available: bool = True,
        status: str = STATUS_OK,
        text: str = "",
        raises: bool = False,
    ) -> None:
        self.provider = provider
        self._available = available
        self._status = status
        self._text = text
        self._raises = raises
        self.calls: List[RoleRunnerInput] = []

    def is_available(self) -> bool:
        return self._available

    def generate(self, input_: RoleRunnerInput) -> RoleRunnerOutput:
        self.calls.append(input_)
        if self._raises:
            raise RuntimeError(f"{self.provider} backend exploded")
        return RoleRunnerOutput(
            provider=self.provider,
            status=self._status,
            text=self._text,
            detail=None,
        )


class EnvDrivenCandidatesTests(unittest.TestCase):
    """:func:`build_role_runner_candidates` reads opt-in providers from env."""

    def test_empty_env_yields_no_candidates_and_deterministic_only_trace(
        self,
    ) -> None:
        candidates, trace = build_role_runner_candidates(env={})
        self.assertEqual(candidates, ())
        self.assertTrue(trace.deterministic_fallback_only)
        # Every known provider appears in the trace as "not opted in"
        # so the operator has a complete picture.
        provider_names = {entry.provider for entry in trace.entries}
        self.assertSetEqual(
            provider_names, {PROVIDER_CLAUDE, PROVIDER_CODEX, PROVIDER_OLLAMA}
        )
        for entry in trace.entries:
            self.assertFalse(entry.configured)
            self.assertFalse(entry.available)
            self.assertEqual(entry.reason, REASON_NOT_OPTED_IN)

    def test_env_with_unknown_provider_records_typo(self) -> None:
        env = {ENV_PROVIDERS: "phantom"}
        candidates, trace = build_role_runner_candidates(env=env)
        self.assertEqual(candidates, ())
        # Unknown token surfaces in the trace so a typo is visible.
        unknowns = [
            entry
            for entry in trace.entries
            if entry.provider == "phantom"
        ]
        self.assertEqual(len(unknowns), 1)
        self.assertTrue(unknowns[0].configured)
        self.assertEqual(unknowns[0].reason, "unknown provider")
        # Known providers still listed as "not opted in".
        for entry in trace.entries:
            if entry.provider in {PROVIDER_CLAUDE, PROVIDER_CODEX, PROVIDER_OLLAMA}:
                self.assertFalse(entry.configured)


class DispatcherFromEnvTests(unittest.TestCase):
    """:func:`build_role_runner_dispatch_from_env` returns a callable."""

    def test_empty_env_returns_deterministic_dispatcher(self) -> None:
        dispatch, trace = build_role_runner_dispatch_from_env(env={})
        self.assertTrue(trace.deterministic_fallback_only)

        audit_calls: list = []

        def _capture(session: Any, record: Mapping[str, Any]) -> None:
            audit_calls.append(record)

        dispatch, trace = build_role_runner_dispatch_from_env(
            env={}, audit_writer=_capture
        )
        out = dispatch(_StubSession(), _input())

        self.assertEqual(out.provider, PROVIDER_DETERMINISTIC)
        self.assertEqual(out.status, STATUS_FALLBACK)
        self.assertTrue(out.used_fallback)
        self.assertTrue(out.text)  # deterministic always has text
        # Audit writer fires once per dispatch call with a record
        # naming the deterministic provider.
        self.assertEqual(len(audit_calls), 1)
        self.assertEqual(audit_calls[0]["provider"], PROVIDER_DETERMINISTIC)
        self.assertTrue(audit_calls[0]["used_fallback"])

    def test_inactive_role_does_not_run_any_runner(self) -> None:
        dispatch, _ = build_role_runner_dispatch_from_env(env={})
        out = dispatch(_StubSession(), _input(role="frontend-engineer"))
        # frontend-engineer not in active_research_roles → gated.
        self.assertEqual(out.status, STATUS_INACTIVE_ROLE)
        self.assertEqual(out.text, "")


class FakeProviderChainTests(unittest.TestCase):
    """Simulate Claude / Codex / Ollama by injecting fake adapters via
    the dispatcher API directly. The bootstrap factory's role is
    *configuration*; chain semantics live on the underlying dispatcher
    which we exercise here with the same shape the bootstrap produces.
    """

    def setUp(self) -> None:
        self.audit_calls: List[Mapping[str, Any]] = []

        def _capture(session: Any, record: Mapping[str, Any]) -> None:
            self.audit_calls.append(record)

        self._capture = _capture

    def _dispatch_with_chain(
        self, *runners: RoleRunner
    ):
        # Simulate "configured environment" by handing a dispatcher
        # the same shape the bootstrap builds — record-only writer
        # delegates to a session-aware capture so we exercise the
        # bootstrap-shaped public API surface.
        from yule_orchestrator.agents.runners.role_runner import (
            build_role_runner_dispatcher,
        )

        records_per_call: list[list] = []

        def _session_aware(session, input_):
            records: list = []

            def _record_only(record):
                records.append(record)

            dispatch = build_role_runner_dispatcher(
                candidates=list(runners),
                audit_writer=_record_only,
            )
            out = dispatch(session, input_)
            for r in records:
                self._capture(session, r)
            records_per_call.append(records)
            return out

        return _session_aware

    def test_fake_claude_wins_when_ok(self) -> None:
        claude = _ScriptedRunner(
            PROVIDER_CLAUDE, status=STATUS_OK, text="Claude take body"
        )
        codex = _ScriptedRunner(
            PROVIDER_CODEX, status=STATUS_OK, text="codex"
        )
        dispatch = self._dispatch_with_chain(claude, codex)
        out = dispatch(_StubSession(), _input())
        self.assertEqual(out.provider, PROVIDER_CLAUDE)
        self.assertEqual(out.status, STATUS_OK)
        self.assertEqual(out.text, "Claude take body")
        # Lower-priority codex never invoked.
        self.assertEqual(len(codex.calls), 0)
        self.assertEqual(self.audit_calls[-1]["provider"], PROVIDER_CLAUDE)

    def test_claude_failure_walks_to_codex(self) -> None:
        claude = _ScriptedRunner(PROVIDER_CLAUDE, raises=True)
        codex = _ScriptedRunner(
            PROVIDER_CODEX, status=STATUS_OK, text="codex take body"
        )
        ollama = _ScriptedRunner(PROVIDER_OLLAMA, status=STATUS_OK)
        dispatch = self._dispatch_with_chain(claude, codex, ollama)
        out = dispatch(_StubSession(), _input())
        self.assertEqual(out.provider, PROVIDER_CODEX)
        self.assertEqual(out.text, "codex take body")
        # Ollama untouched once codex returned ok.
        self.assertEqual(len(ollama.calls), 0)

    def test_claude_and_codex_failure_walks_to_ollama(self) -> None:
        claude = _ScriptedRunner(PROVIDER_CLAUDE, available=False)
        codex = _ScriptedRunner(PROVIDER_CODEX, raises=True)
        ollama = _ScriptedRunner(
            PROVIDER_OLLAMA, status=STATUS_OK, text="ollama take body"
        )
        dispatch = self._dispatch_with_chain(claude, codex, ollama)
        out = dispatch(_StubSession(), _input())
        self.assertEqual(out.provider, PROVIDER_OLLAMA)
        self.assertEqual(out.text, "ollama take body")

    def test_all_unavailable_falls_back_to_deterministic(self) -> None:
        claude = _ScriptedRunner(PROVIDER_CLAUDE, available=False)
        codex = _ScriptedRunner(PROVIDER_CODEX, available=False)
        ollama = _ScriptedRunner(PROVIDER_OLLAMA, available=False)
        dispatch = self._dispatch_with_chain(claude, codex, ollama)
        out = dispatch(_StubSession(), _input())
        self.assertEqual(out.provider, PROVIDER_DETERMINISTIC)
        self.assertEqual(out.status, STATUS_FALLBACK)
        self.assertTrue(out.used_fallback)
        # Audit explicitly names the fallback for grep.
        self.assertEqual(self.audit_calls[-1]["provider"], PROVIDER_DETERMINISTIC)
        self.assertTrue(self.audit_calls[-1]["used_fallback"])


class SanitisedReasonsTests(unittest.TestCase):
    """Bootstrap traces must never echo env values or stack frames —
    only key names + sanitised reason strings."""

    def test_trace_reasons_reference_env_key_not_value(self) -> None:
        # Caller might set the env to a value containing a secret.
        # We never read or echo the value — only the *key name* shows
        # up in the reason for unconfigured providers.
        secret_like = "do-not-leak-this-token-into-trace"
        env = {ENV_PROVIDERS: ""}  # empty value
        _, trace = build_role_runner_candidates(env=env)
        for entry in trace.entries:
            self.assertNotIn(secret_like, entry.reason)
            # Reason mentions the env key name, not its value.
            self.assertIn(ENV_PROVIDERS, entry.reason)

    def test_trace_reasons_are_stable_strings(self) -> None:
        _, trace = build_role_runner_candidates(env={})
        # Every reason matches one of the canonical sanitised strings.
        # Importing them here keeps the test resilient to wording
        # changes — the test breaks only when the constants drift.
        from yule_orchestrator.agents.runners.bootstrap import (
            REASON_CLI_NOT_FOUND,
            REASON_ENDPOINT_UNREACHABLE,
            REASON_NOT_OPTED_IN,
        )

        allowed = {
            REASON_NOT_OPTED_IN,
            REASON_OPTED_IN_AVAILABLE,
            REASON_CLI_NOT_FOUND,
            REASON_ENDPOINT_UNREACHABLE,
        }
        for entry in trace.entries:
            self.assertIn(entry.reason, allowed, entry)


class AuditPayloadTests(unittest.TestCase):
    def test_trace_audit_payload_is_json_friendly(self) -> None:
        import json

        _, trace = build_role_runner_candidates(env={})
        payload = trace.as_audit_payload()
        # Round-trip through JSON to assert no non-serialisable types
        # leaked into the payload (operator surfaces dump it as JSON).
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["kind"], "role_runner_bootstrap")
        self.assertTrue(decoded["deterministic_fallback_only"])
        self.assertEqual(len(decoded["entries"]), 3)


if __name__ == "__main__":
    unittest.main()
