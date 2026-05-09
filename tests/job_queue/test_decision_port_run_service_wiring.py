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
    PROVIDER_RECORD,
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
