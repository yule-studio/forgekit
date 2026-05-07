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

from yule_orchestrator.agents.job_queue import HeartbeatStore, JobQueue
from yule_orchestrator.runtime.run_service import _build_process_job
from yule_orchestrator.runtime.services import resolve_service


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
    def test_approval_worker_still_uses_no_post_fn_placeholder(self) -> None:
        # M6.1a intentionally leaves the approval post_fn placeholder
        # in place — the production Discord wrapper lands in M6.1b.
        # This test is a tripwire: when M6.1b lands, this assertion
        # flips to the negative variant and the placeholder helper
        # is removed.
        spec = resolve_service("eng-approval-worker")
        assert spec is not None
        process_fn = _build_process_job(
            spec, queue=self.queue, heartbeats=self.heartbeats
        )
        free = inspect.getclosurevars(process_fn).nonlocals
        worker = free.get("worker")
        self.assertIsNotNone(worker)
        # _post_fn is the placeholder until M6.1b. Confirm the name
        # so a future cleanup deletes the placeholder + flips this
        # assertion in lockstep.
        self.assertEqual(
            getattr(worker._post_fn, "__name__", ""), "_no_post_fn_yet"
        )


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
        from yule_orchestrator.agents.job_queue import (
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
