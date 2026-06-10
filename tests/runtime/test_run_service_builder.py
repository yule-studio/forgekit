"""run_service ``_build_process_job`` — A-M6.1a wiring tests.

Pin that the M6.0 placeholders (``_no_runner_yet``,
``_no_role_runner_yet``) are gone for the implemented service
kinds and replaced with the standalone runner factories.

The tests reach into the private ``_build_process_job`` because
that's where the wiring lives — the public ``run_service_main``
spawns asyncio loops which we can't drive in a unit test
without real workers.
"""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.job_queue import HeartbeatStore, JobQueue
from yule_engineering.runtime.run_service import _build_process_job
from yule_engineering.runtime.services import resolve_service


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:  # noqa: D401 - test setup
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._db = Path(self._tmp.name) / "queue.sqlite3"
        self.queue = JobQueue(db_path=self._db)
        self.heartbeats = HeartbeatStore(db_path=self._db)


class ResearchBuilderTests(_Fixture):
    def test_research_worker_uses_standalone_runner_not_placeholder(self) -> None:
        spec = resolve_service("eng-research-worker")
        assert spec is not None
        process_fn = _build_process_job(
            spec, queue=self.queue, heartbeats=self.heartbeats
        )
        # The closure captures ``research_runner`` which is the
        # build_research_runner factory output — never the M6.0
        # ``_no_runner_yet`` placeholder.
        free = inspect.getclosurevars(process_fn).nonlocals
        runner = free.get("research_runner")
        self.assertIsNotNone(runner)
        self.assertNotEqual(getattr(runner, "__name__", ""), "_no_runner_yet")


class RoleBuilderTests(_Fixture):
    def test_role_worker_uses_standalone_runner_not_placeholder(self) -> None:
        spec = resolve_service("eng-role-backend-engineer")
        assert spec is not None
        process_fn = _build_process_job(
            spec, queue=self.queue, heartbeats=self.heartbeats
        )
        free = inspect.getclosurevars(process_fn).nonlocals
        runner = free.get("role_runner")
        self.assertIsNotNone(runner)
        self.assertNotEqual(
            getattr(runner, "__name__", ""), "_no_role_runner_yet"
        )

    def test_role_worker_passes_role_filter_to_worker(self) -> None:
        spec = resolve_service("eng-role-qa-engineer")
        assert spec is not None
        process_fn = _build_process_job(
            spec, queue=self.queue, heartbeats=self.heartbeats
        )
        free = inspect.getclosurevars(process_fn).nonlocals
        worker = free.get("worker")
        self.assertIsNotNone(worker)
        # Internal attribute for the role filter — keeps the M6.0
        # role isolation contract observable from a unit test.
        self.assertEqual(worker._role_filter, "qa-engineer")


class ApprovalBuilderTests(_Fixture):
    def test_approval_worker_uses_production_post_fn(self) -> None:
        # M6.1b-1 replaced the M6.1a ``_no_post_fn_yet`` placeholder
        # with ``build_production_post_fn`` output (closure named
        # ``_post_fn``). M6.2 wraps the channel resolver with a
        # NAME-fallback closure (``build_approval_channel_resolver``)
        # so the per-process cache + Discord REST GET both live in
        # one factory.
        spec = resolve_service("eng-approval-worker")
        assert spec is not None
        process_fn = _build_process_job(
            spec, queue=self.queue, heartbeats=self.heartbeats
        )
        free = inspect.getclosurevars(process_fn).nonlocals
        worker = free.get("worker")
        self.assertIsNotNone(worker)
        # _post_fn is now the closure produced by the production
        # factory — never the placeholder.
        self.assertNotEqual(
            getattr(worker._post_fn, "__name__", ""), "_no_post_fn_yet"
        )
        # Channel resolver is the NAME-fallback closure, not the
        # bare id-only resolver.
        self.assertEqual(
            getattr(worker._channel_resolver, "__name__", ""), "_resolve"
        )
        # The resolver must still default to env (call with no args
        # raises nothing and returns None when env is empty).
        self.assertIsNone(worker._channel_resolver())


class ObsidianBuilderTests(_Fixture):
    def test_obsidian_worker_uses_default_render_and_write_fns(self) -> None:
        # Obsidian writer's defaults already exist (default_render_fn /
        # default_write_fn / default_vault_root_resolver). M6.1a
        # confirms the builder wires them — no placeholder there.
        spec = resolve_service("eng-obsidian-writer")
        assert spec is not None
        process_fn = _build_process_job(
            spec, queue=self.queue, heartbeats=self.heartbeats
        )
        free = inspect.getclosurevars(process_fn).nonlocals
        worker = free.get("worker")
        self.assertIsNotNone(worker)
        from yule_engineering.agents.job_queue import (
            default_render_fn,
            default_vault_root_resolver,
            default_write_fn,
        )
        self.assertIs(worker._render_fn, default_render_fn)
        self.assertIs(worker._write_fn, default_write_fn)
        self.assertIs(
            worker._vault_root_resolver, default_vault_root_resolver
        )


if __name__ == "__main__":
    unittest.main()
