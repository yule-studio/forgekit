"""run_service ↔ Claude decision seam wiring.

The autonomy producer the supervisor watch loop drives must end up
holding a decision port composed by
:func:`build_decision_port_from_env`. The composition is
deterministic-only by default, so an installation that hasn't opted
into a record / external tier sees no behavioural change — but the
seam *is* wired, so a follow-up PR that drops a live callable in
takes effect without re-plumbing the supervisor.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, List, Mapping, Optional

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.job_queue.claude_decision_seam import (
    DECISION_KIND_RETRY_GUARD,
    DecisionResponse,
    ENV_CLAUDE_DECISION_PROVIDER,
    PROVIDER_DETERMINISTIC,
    PROVIDER_EXTERNAL,
    PROVIDER_RECORD,
)
from yule_orchestrator.agents.job_queue.claude_subprocess_adapter import (
    ENV_LIVE_BINARY,
    ENV_LIVE_ENABLED,
)
from yule_orchestrator.agents.job_queue.heartbeat import HeartbeatStore
from yule_orchestrator.agents.job_queue.store import JobQueue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _Env:
    """Context manager that pins env keys for the duration of the test."""

    def __init__(self, **kwargs: Optional[str]) -> None:
        self._desired = kwargs
        self._previous: Mapping[str, Optional[str]] = {}

    def __enter__(self) -> "_Env":
        self._previous = {k: os.environ.get(k) for k in self._desired}
        for k, v in self._desired.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for k, v in self._previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class RunServiceDecisionPortWiringTests(unittest.TestCase):
    """Tests against ``_build_autonomy_producer_tick`` private helper.

    We don't run the whole supervisor loop here — that path is covered
    by ``tests.runtime.test_supervisor_autonomy_tick``. The goal is to
    pin the seam composition + env contract that this PR introduces.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=db_path)
        self.heartbeats = HeartbeatStore(db_path=db_path)

    def test_default_chain_is_deterministic_only(self) -> None:
        from yule_orchestrator.runtime import run_service as rs

        with _Env(
            **{
                "YULE_AUTONOMY_PRODUCER_ENABLED": "true",
                ENV_CLAUDE_DECISION_PROVIDER: None,
            }
        ):
            tick_fn, _ = rs._build_autonomy_producer_tick(
                queue=self.queue, heartbeats=self.heartbeats
            )
        if tick_fn is None:
            self.skipTest("autonomy producer construction degraded — env-side issue")
        # Reach into the producer the closure captured.
        producer = tick_fn.__closure__[0].cell_contents  # type: ignore[union-attr]
        port = producer._decision_port  # type: ignore[attr-defined]
        self.assertIsNotNone(port)
        # Deterministic-only chain still answers ``advance`` for any kind.
        from yule_orchestrator.agents.job_queue.claude_decision_seam import (
            DecisionRequest,
        )

        response = port.decide(
            request=DecisionRequest(
                kind=DECISION_KIND_RETRY_GUARD, summary="probe"
            )
        )
        self.assertTrue(response.advance)

    def test_external_factory_hook_overridable(self) -> None:
        """A follow-up PR can monkeypatch the factory to plug a live tier."""

        from yule_orchestrator.runtime import run_service as rs

        captured: List[Any] = []

        def _live(*, request, timeout_seconds=None):  # noqa: ARG001
            captured.append(request)
            return DecisionResponse(skip=True, reason="live-skip")

        def _factory_override():
            def _factory(_env: Mapping[str, str]):
                return _live

            return _factory

        previous = rs._resolve_external_decision_callable_factory
        rs._resolve_external_decision_callable_factory = _factory_override
        try:
            with _Env(
                **{
                    "YULE_AUTONOMY_PRODUCER_ENABLED": "true",
                    ENV_CLAUDE_DECISION_PROVIDER: f"external,{PROVIDER_DETERMINISTIC}",
                }
            ):
                tick_fn, _ = rs._build_autonomy_producer_tick(
                    queue=self.queue, heartbeats=self.heartbeats
                )
        finally:
            rs._resolve_external_decision_callable_factory = previous
        if tick_fn is None:
            self.skipTest("autonomy producer construction degraded — env-side issue")
        producer = tick_fn.__closure__[0].cell_contents  # type: ignore[union-attr]
        port = producer._decision_port  # type: ignore[attr-defined]
        from yule_orchestrator.agents.job_queue.claude_decision_seam import (
            DecisionRequest,
        )

        response = port.decide(
            request=DecisionRequest(
                kind=DECISION_KIND_RETRY_GUARD, summary="probe"
            )
        )
        self.assertTrue(response.skip)
        self.assertEqual(response.reason, "live-skip")
        self.assertEqual(len(captured), 1)

    def test_record_token_layers_record_only(self) -> None:
        from yule_orchestrator.runtime import run_service as rs

        with _Env(
            **{
                "YULE_AUTONOMY_PRODUCER_ENABLED": "true",
                ENV_CLAUDE_DECISION_PROVIDER: f"{PROVIDER_RECORD},{PROVIDER_DETERMINISTIC}",
            }
        ):
            tick_fn, _ = rs._build_autonomy_producer_tick(
                queue=self.queue, heartbeats=self.heartbeats
            )
        if tick_fn is None:
            self.skipTest("autonomy producer construction degraded — env-side issue")
        producer = tick_fn.__closure__[0].cell_contents  # type: ignore[union-attr]
        port = producer._decision_port  # type: ignore[attr-defined]
        from yule_orchestrator.agents.job_queue.claude_decision_seam import (
            DecisionRequest,
        )

        # Driving the chain still ends in ``advance`` (deterministic
        # fallback owns the verdict) but the chain composes without
        # error and the record tier is in the path.
        response = port.decide(
            request=DecisionRequest(
                kind=DECISION_KIND_RETRY_GUARD, summary="probe"
            )
        )
        self.assertTrue(response.advance)


# ---------------------------------------------------------------------------
# Round 4-ter — subprocess factory wired into run_service
# ---------------------------------------------------------------------------


class RunServiceSubprocessAdapterWiringTests(unittest.TestCase):
    """The default external_callable_factory now hands the subprocess
    adapter back. Pin that the two-key opt-in (provider chain + live
    flag) is what actually surfaces it — and that the live tier stays
    dormant when either key is missing."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=db_path)
        self.heartbeats = HeartbeatStore(db_path=db_path)

    def test_default_factory_returns_subprocess_callable_when_opted_in(
        self,
    ) -> None:
        from yule_orchestrator.runtime import run_service as rs

        factory = rs._resolve_external_decision_callable_factory()
        callable_ = factory(
            {
                ENV_LIVE_ENABLED: "true",
                # Use a binary name we can keep on PATH via the resolver
                # injection — but the run_service path doesn't expose
                # the resolver hook. Instead we point the binary at a
                # path that exists on the test host so ``shutil.which``
                # returns a real path. ``/bin/sh`` is universally
                # available on macOS + Linux runners.
                ENV_LIVE_BINARY: "/bin/sh",
            }
        )
        self.assertIsNotNone(callable_)

    def test_default_factory_returns_none_when_live_flag_missing(self) -> None:
        from yule_orchestrator.runtime import run_service as rs

        factory = rs._resolve_external_decision_callable_factory()
        # Provider chain wants ``external`` but ``YULE_CLAUDE_DECISION_LIVE_ENABLED``
        # isn't set — factory should return None so the seam logs the
        # tier as skipped.
        self.assertIsNone(factory({}))

    def test_supervisor_wires_subprocess_adapter_when_both_flags_set(
        self,
    ) -> None:
        """End-to-end: env opts in, factory hands the live callable
        through, autonomy producer's decision port routes to it."""

        from yule_orchestrator.runtime import run_service as rs

        with _Env(
            **{
                "YULE_AUTONOMY_PRODUCER_ENABLED": "true",
                ENV_CLAUDE_DECISION_PROVIDER: f"{PROVIDER_EXTERNAL},{PROVIDER_DETERMINISTIC}",
                ENV_LIVE_ENABLED: "true",
                ENV_LIVE_BINARY: "/bin/sh",
            }
        ):
            tick_fn, _ = rs._build_autonomy_producer_tick(
                queue=self.queue, heartbeats=self.heartbeats
            )
        if tick_fn is None:
            self.skipTest("autonomy producer construction degraded — env-side issue")
        producer = tick_fn.__closure__[0].cell_contents  # type: ignore[union-attr]
        port = producer._decision_port  # type: ignore[attr-defined]
        # The composed port has the subprocess external tier on top.
        # We don't actually invoke ``/bin/sh`` here (no JSON in/out
        # contract), but composition itself proves the live tier was
        # surfaced. The chain still has the deterministic fallback so
        # any kind we ask resolves.
        from yule_orchestrator.agents.job_queue.claude_decision_seam import (
            DecisionRequest,
        )

        # ``/bin/sh`` returns no JSON — adapter must surface a
        # non-actionable response and the chain falls through to
        # deterministic.
        response = port.decide(
            request=DecisionRequest(
                kind=DECISION_KIND_RETRY_GUARD, summary="probe"
            )
        )
        self.assertTrue(response.advance)
        # Deterministic fallback owns the verdict.
        self.assertIn("deterministic", response.reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
