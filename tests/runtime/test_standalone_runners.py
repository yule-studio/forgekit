"""standalone_runners — A-M6.1a unit tests.

Pin that the production runners actually do work (load session,
call the collector / role-take body, persist results) instead of
the M6.0 placeholders that always raised. Tests inject stub
loaders / collectors so they don't touch SQLite or the live
collector.
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, List, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.standalone_runners import (
    build_research_runner,
    build_role_take_runner,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@dataclass
class _StubSession:
    session_id: str = "sess-runner-1"
    prompt: str = "k8s 운영 자료 정리"
    task_type: str = "research"
    references_user: tuple = ()
    extra: dict = field(default_factory=dict)


def _job(payload: Optional[dict] = None, *, role: Optional[str] = None) -> Any:
    return SimpleNamespace(
        job_id="job-1",
        session_id="sess-runner-1",
        role=role,
        payload=payload or {},
    )


# ---------------------------------------------------------------------------
# build_research_runner
# ---------------------------------------------------------------------------


class ResearchRunnerTests(unittest.TestCase):
    def test_runner_loads_session_and_calls_collector(self) -> None:
        session = _StubSession()
        seen: List[dict] = []

        def collect_fn(**kwargs):
            seen.append(kwargs)
            return SimpleNamespace(
                pack=SimpleNamespace(title="k8s ingress"),
                collection_outcome="ok",
                forum_status_message="forum thread 게시 완료",
            )

        persisted: List[Any] = []

        def persist_fn(*, session, outcome):
            persisted.append((session, outcome))

        runner = build_research_runner(
            session_loader=lambda _sid: session,
            collect_fn=collect_fn,
            persist_fn=persist_fn,
        )
        outcome = _run(
            runner(
                _job(
                    {
                        "role_for_research": "devops-engineer",
                        "prompt_excerpt": "k8s 운영",
                    }
                )
            )
        )

        # Runner returned the collector's outcome unchanged so the
        # worker's process_job can stash whatever the M3 contract
        # already expects.
        self.assertEqual(outcome.forum_status_message, "forum thread 게시 완료")
        # Collector received the role + prompt the producer stamped
        # on the job's payload, plus the reloaded session metadata.
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["role"], "devops-engineer")
        self.assertEqual(seen[0]["prompt"], "k8s 운영")
        self.assertEqual(seen[0]["session_id"], "sess-runner-1")
        # Persistence hook fired with the same session + outcome.
        self.assertEqual(len(persisted), 1)
        self.assertIs(persisted[0][0], session)

    def test_runner_falls_back_to_session_prompt_when_payload_missing(self) -> None:
        session = _StubSession(prompt="fallback prompt body")
        seen: List[dict] = []

        def collect_fn(**kwargs):
            seen.append(kwargs)
            return SimpleNamespace(pack=None, collection_outcome=None)

        runner = build_research_runner(
            session_loader=lambda _sid: session,
            collect_fn=collect_fn,
            persist_fn=lambda **_: None,
        )
        _run(runner(_job({})))

        self.assertEqual(seen[0]["prompt"], "fallback prompt body")
        # Default role when producer didn't stamp one.
        self.assertEqual(seen[0]["role"], "tech-lead")

    def test_runner_raises_when_session_missing(self) -> None:
        runner = build_research_runner(
            session_loader=lambda _sid: None,
            collect_fn=lambda **_: None,
            persist_fn=lambda **_: None,
        )
        # Worker's process_job catches this and lands the row in
        # failed_retryable. The runner just has to raise loudly so
        # the row carries a useful error.
        with self.assertRaises(RuntimeError) as ctx:
            _run(runner(_job({})))
        self.assertIn("session", str(ctx.exception).lower())

    def test_runner_swallows_persist_exception(self) -> None:
        # Persistence is best-effort observability; a SQLite blip
        # must NOT make the runner fail and re-collect on retry.
        session = _StubSession()

        def boom(**_kwargs):
            raise RuntimeError("sqlite lock")

        runner = build_research_runner(
            session_loader=lambda _sid: session,
            collect_fn=lambda **_: SimpleNamespace(pack=None),
            persist_fn=boom,
        )
        outcome = _run(runner(_job({})))
        # No exception — outcome flows through.
        self.assertIsNotNone(outcome)


# ---------------------------------------------------------------------------
# build_role_take_runner
# ---------------------------------------------------------------------------


class RoleTakeRunnerTests(unittest.TestCase):
    def test_runner_dispatches_to_open_call_body(self) -> None:
        session = _StubSession()
        seen: List[dict] = []

        def open_call_fn(*, role, session_id, session, pack_loader):
            seen.append({"role": role, "session_id": session_id})
            return SimpleNamespace(
                role=role,
                session_id=session_id,
                message="rendered take body",
                next_directive=None,
                is_synthesis=False,
            )

        runner = build_role_take_runner(
            session_loader=lambda _sid: session,
            open_call_fn=open_call_fn,
            persist_outcome_fn=lambda **_: None,
        )
        outcome = runner(
            _job({"kind": "open"}, role="ai-engineer")
        )
        self.assertEqual(outcome.role, "ai-engineer")
        self.assertEqual(outcome.message, "rendered take body")
        self.assertEqual(seen[0]["role"], "ai-engineer")

    def test_runner_dispatches_to_turn_body(self) -> None:
        # A-M6.2 wired the chained turn body through the standalone
        # runner. Producer stamps ``effective_role`` on payload so a
        # tech-lead bot answering for ai-engineer routes correctly.
        session = _StubSession()
        seen: List[dict] = []

        def turn_call_fn(*, role, session_id, session, pack_loader, payload):
            seen.append({
                "role": role,
                "session_id": session_id,
                "effective_role": payload.get("effective_role"),
            })
            return SimpleNamespace(
                role=role,
                session_id=session_id,
                message="rendered turn body\n\n[research-turn:sess <next>]",
                next_directive="[research-turn:sess <next>]",
                is_synthesis=False,
            )

        runner = build_role_take_runner(
            session_loader=lambda _sid: session,
            turn_call_fn=turn_call_fn,
            persist_outcome_fn=lambda **_: None,
        )
        outcome = runner(
            _job(
                {"kind": "turn", "effective_role": "ai-engineer"},
                role="tech-lead",
            )
        )
        self.assertEqual(outcome.role, "tech-lead")
        self.assertIn("[research-turn", outcome.message)
        self.assertEqual(seen[0]["effective_role"], "ai-engineer")

    def test_runner_dispatches_to_synthesis_body(self) -> None:
        # synthesis kind closes the chain; the runner returns the
        # is_synthesis=True outcome the member bot's render path
        # uses to stamp the closing comment.
        session = _StubSession()
        called: List[dict] = []

        def synthesis_call_fn(*, role, session_id, session, pack_loader):
            called.append({"role": role, "session_id": session_id})
            return SimpleNamespace(
                role=role,
                session_id=session_id,
                message="tech-lead synthesis 종합",
                next_directive=None,
                is_synthesis=True,
            )

        runner = build_role_take_runner(
            session_loader=lambda _sid: session,
            synthesis_call_fn=synthesis_call_fn,
            persist_outcome_fn=lambda **_: None,
        )
        outcome = runner(
            _job({"kind": "synthesis"}, role="tech-lead")
        )
        self.assertTrue(outcome.is_synthesis)
        self.assertEqual(outcome.message, "tech-lead synthesis 종합")
        self.assertEqual(called[0]["role"], "tech-lead")

    def test_runner_rejects_unknown_kind(self) -> None:
        # An unknown kind would silently do nothing if we accepted it.
        # Surface loudly so the supervisor row carries a useful error.
        runner = build_role_take_runner(
            session_loader=lambda _sid: _StubSession(),
            open_call_fn=lambda **_: None,
            persist_outcome_fn=lambda **_: None,
        )
        with self.assertRaises(RuntimeError) as ctx:
            runner(_job({"kind": "ghost"}, role="ai-engineer"))
        self.assertIn("ghost", str(ctx.exception))

    def test_runner_raises_on_missing_session(self) -> None:
        runner = build_role_take_runner(
            session_loader=lambda _sid: None,
            open_call_fn=lambda **_: None,
            persist_outcome_fn=lambda **_: None,
        )
        with self.assertRaises(RuntimeError):
            runner(_job({"kind": "open"}, role="qa-engineer"))

    def test_runner_raises_on_missing_role(self) -> None:
        runner = build_role_take_runner(
            session_loader=lambda _sid: _StubSession(),
            open_call_fn=lambda **_: None,
            persist_outcome_fn=lambda **_: None,
        )
        with self.assertRaises(RuntimeError):
            runner(_job({"kind": "open"}, role=None))


# ---------------------------------------------------------------------------
# A-M11 — role_runner_dispatch wiring inside build_role_take_runner
# ---------------------------------------------------------------------------


class _DispatchOutput:
    """Minimal stand-in for :class:`RoleRunnerOutput`."""

    def __init__(
        self, *, provider: str, status: str, text: str = "", used_fallback: bool = False
    ) -> None:
        self.provider = provider
        self.status = status
        self.text = text
        self.used_fallback = used_fallback
        self.detail = None


class RoleRunnerWiringTests(unittest.TestCase):
    def test_configured_runner_replaces_message_for_open_kind(self) -> None:
        # Open-call body produces the deterministic outcome; the
        # role_runner_dispatch returns ok → adapter swaps the message
        # for the configured provider's text and stamps the provider
        # tag.
        session = _StubSession()

        def open_call_fn(*, role, session_id, session, pack_loader):
            return SimpleNamespace(
                role=role,
                session_id=session_id,
                message="deterministic open-call body",
                next_directive=None,
                is_synthesis=False,
            )

        seen_inputs: List[Any] = []

        def dispatch(_session, runner_input):
            seen_inputs.append(runner_input)
            return _DispatchOutput(
                provider="claude",
                status="ok",
                text="LLM-driven role take body",
            )

        runner = build_role_take_runner(
            session_loader=lambda _sid: session,
            open_call_fn=open_call_fn,
            persist_outcome_fn=lambda **_: None,
            role_runner_dispatch=dispatch,
        )
        outcome = runner(_job({"kind": "open"}, role="ai-engineer"))

        self.assertIn("LLM-driven role take body", outcome.message)
        self.assertIn("provider: claude", outcome.message)
        # Dispatcher saw the role + session in its input.
        self.assertEqual(len(seen_inputs), 1)
        self.assertEqual(seen_inputs[0].role, "ai-engineer")
        self.assertEqual(seen_inputs[0].session_id, "sess-runner-1")

    def test_inactive_role_dispatch_keeps_deterministic_outcome(self) -> None:
        # The dispatcher reports status="inactive_role" → the
        # standalone runner must keep the deterministic outcome and
        # NOT splice runner text in.
        session = _StubSession()

        def open_call_fn(**_kwargs):
            return SimpleNamespace(
                role="qa-engineer",
                session_id="sess-runner-1",
                message="deterministic body — fallback",
                next_directive=None,
                is_synthesis=False,
            )

        def dispatch(_session, _runner_input):
            return _DispatchOutput(
                provider="deterministic",
                status="inactive_role",
                text="",
            )

        runner = build_role_take_runner(
            session_loader=lambda _sid: session,
            open_call_fn=open_call_fn,
            persist_outcome_fn=lambda **_: None,
            role_runner_dispatch=dispatch,
        )
        outcome = runner(_job({"kind": "open"}, role="qa-engineer"))
        self.assertEqual(outcome.message, "deterministic body — fallback")
        self.assertNotIn("provider:", outcome.message)

    def test_dispatch_failure_keeps_deterministic_outcome(self) -> None:
        # Dispatcher raising must not propagate — deterministic body
        # already produced a valid outcome and we don't want a runner
        # bug to derail the role take.
        session = _StubSession()

        def open_call_fn(**_kwargs):
            return SimpleNamespace(
                role="ai-engineer",
                session_id="sess-runner-1",
                message="deterministic open body",
                next_directive=None,
                is_synthesis=False,
            )

        def dispatch(_session, _runner_input):
            raise RuntimeError("dispatcher exploded")

        runner = build_role_take_runner(
            session_loader=lambda _sid: session,
            open_call_fn=open_call_fn,
            persist_outcome_fn=lambda **_: None,
            role_runner_dispatch=dispatch,
        )
        outcome = runner(_job({"kind": "open"}, role="ai-engineer"))
        self.assertEqual(outcome.message, "deterministic open body")

    def test_dispatch_returning_fallback_keeps_deterministic_outcome(self) -> None:
        # The deterministic runner inside the dispatcher might fire
        # (status="fallback"), but for the standalone runner we keep
        # the body's deterministic message — the dispatcher's fallback
        # text already carries a placeholder, so swapping the message
        # would be net-negative. The audit captures the fallback
        # provenance via the dispatcher's audit_writer, which is
        # tested at the dispatcher level.
        session = _StubSession()

        def turn_call_fn(*, role, session_id, session, pack_loader, payload):
            return SimpleNamespace(
                role=role,
                session_id=session_id,
                message="deterministic turn body",
                next_directive="[research-turn:next]",
                is_synthesis=False,
            )

        def dispatch(_session, _runner_input):
            return _DispatchOutput(
                provider="deterministic",
                status="fallback",
                text="deterministic placeholder",
                used_fallback=True,
            )

        runner = build_role_take_runner(
            session_loader=lambda _sid: session,
            turn_call_fn=turn_call_fn,
            persist_outcome_fn=lambda **_: None,
            role_runner_dispatch=dispatch,
        )
        outcome = runner(
            _job({"kind": "turn", "effective_role": "ai-engineer"}, role="ai-engineer")
        )
        self.assertEqual(outcome.message, "deterministic turn body")

    def test_no_dispatch_means_legacy_behavior(self) -> None:
        # Default: role_runner_dispatch=None → behavior identical to
        # pre-M11 (no message rewrite, no extra audit).
        session = _StubSession()
        seen: List[dict] = []

        def open_call_fn(*, role, session_id, session, pack_loader):
            seen.append({"role": role})
            return SimpleNamespace(
                role=role,
                session_id=session_id,
                message="legacy deterministic body",
                next_directive=None,
                is_synthesis=False,
            )

        runner = build_role_take_runner(
            session_loader=lambda _sid: session,
            open_call_fn=open_call_fn,
            persist_outcome_fn=lambda **_: None,
        )
        outcome = runner(_job({"kind": "open"}, role="ai-engineer"))
        self.assertEqual(outcome.message, "legacy deterministic body")
        self.assertEqual(seen[0]["role"], "ai-engineer")

    def test_synthesis_kind_skips_dispatch(self) -> None:
        # Synthesis path has its own M7 fallback automation; the
        # role-runner dispatcher must NOT be applied to it (otherwise
        # the synthesis text would be replaced with a per-role take).
        session = _StubSession()
        called: List[bool] = []

        def synthesis_call_fn(*, role, session_id, session, pack_loader):
            return SimpleNamespace(
                role=role,
                session_id=session_id,
                message="synthesis body",
                next_directive=None,
                is_synthesis=True,
            )

        def dispatch(_session, _runner_input):
            called.append(True)
            return _DispatchOutput(
                provider="claude", status="ok", text="should not be used"
            )

        runner = build_role_take_runner(
            session_loader=lambda _sid: session,
            synthesis_call_fn=synthesis_call_fn,
            persist_outcome_fn=lambda **_: None,
            role_runner_dispatch=dispatch,
        )
        outcome = runner(_job({"kind": "synthesis"}, role="tech-lead"))
        self.assertEqual(outcome.message, "synthesis body")
        # Dispatcher never invoked for synthesis.
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
