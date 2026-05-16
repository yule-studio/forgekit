"""subprocess supervisor — A-M6.0 unit tests.

Pin the parent-process behaviour: dry-run output lists every
implemented service, child crash triggers backoff + restart,
shutdown event terminates children gracefully, exit code 78
prevents restart, reserved services are skipped.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any, List

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.runtime.services import ENGINEERING_PROFILE
from yule_orchestrator.runtime.subprocess_supervisor import (
    DEFAULT_BACKOFF_SCHEDULE,
    EXIT_PREVENT_RESTART,
    build_dry_run_plan,
    render_dry_run_plan,
    run_runtime_up,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fake process — looks like an asyncio.subprocess.Process for the
# parts the supervisor actually touches (wait / terminate / kill /
# returncode).
# ---------------------------------------------------------------------------


class _FakeProcess:
    def __init__(self, *, exit_code_sequence: list, terminate_event: asyncio.Event) -> None:
        # Each entry in the sequence is the exit code returned by one
        # ``wait()`` call. Pop from the front so successive runs of
        # the same managed process can succeed / fail in any order.
        self._exit_codes = exit_code_sequence
        self._terminate_event = terminate_event
        self.returncode: Any = None
        self.terminated = False
        self.killed = False

    async def wait(self) -> int:
        # Real asyncio.subprocess.Process.wait blocks until the
        # child exits. Our fake either:
        #   - pops the next exit code from the sequence (simulates
        #     the child having "exited" with that code), or
        #   - waits for the terminate event so the supervisor's
        #     drain path observes us still alive until SIGTERM.
        if self._exit_codes:
            code = self._exit_codes.pop(0)
            self.returncode = code
            return code
        await self._terminate_event.wait()
        # SIGTERM → return 0 (graceful)
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self._terminate_event.set()

    def kill(self) -> None:
        self.killed = True
        self._terminate_event.set()


# ---------------------------------------------------------------------------
# Dry-run / list mode
# ---------------------------------------------------------------------------


class DryRunPlanTests(unittest.TestCase):
    def test_dry_run_lists_every_auto_spawn_service(self) -> None:
        plan = build_dry_run_plan(profile="engineering")
        # Every auto_spawn engineering service is in the plan. History:
        # M6.1b-2 flipped the gateway to implemented (12 services).
        # #73 added eng-coding-executor as opt-in (auto_spawn=False) —
        # NOT in the spawn plan even though it is implemented.
        # F13 #122 added eng-digest-scheduler (13). P0-C #132 added 7
        # eng-member-* member bots (20 auto-spawn). P0-T added
        # eng-github-work-order-executor (21 auto-spawn). Opt-in
        # (coding executor) stays out.
        ids = {entry[0] for entry in plan.services}
        self.assertEqual(len(plan.services), 21)
        self.assertNotIn("eng-coding-executor", ids)
        self.assertIn("eng-research-worker", ids)
        self.assertIn("eng-role-tech-lead", ids)
        self.assertIn("eng-supervisor-watch", ids)
        self.assertIn("eng-discord-gateway", ids)
        self.assertIn("eng-github-work-order-executor", ids)
        # P0-C: every member bot in the spawn plan.
        for role in (
            "tech-lead",
            "backend-engineer",
            "qa-engineer",
            "devops-engineer",
            "ai-engineer",
            "frontend-engineer",
            "product-designer",
        ):
            self.assertIn(f"eng-member-{role}", ids)
        # cmd shape — ``yule run-service <id>``.
        for service_id, _description, cmd in plan.services:
            self.assertEqual(cmd[:2], ("yule", "run-service"))
            self.assertEqual(cmd[2], service_id)

    def test_dry_run_lists_opt_in_coding_executor_as_skipped(self) -> None:
        # #73 — eng-coding-executor is opt-in (auto_spawn=False) so
        # ``yule runtime up`` skips it without operator opt-in. The
        # dry-run plan still surfaces it in the skipped list with the
        # ``[opt-in / auto_spawn=False]`` marker so the operator sees
        # what's available.
        plan = build_dry_run_plan(profile="engineering")
        skipped_ids = {sid for sid, _ in plan.skipped}
        self.assertIn("eng-coding-executor", skipped_ids)
        marker = next(
            desc for sid, desc in plan.skipped if sid == "eng-coding-executor"
        )
        self.assertIn("opt-in", marker)
        self.assertIn("auto_spawn=False", marker)

    def test_render_includes_profile_and_counts(self) -> None:
        plan = build_dry_run_plan(profile="engineering")
        rendered = render_dry_run_plan(plan)
        self.assertIn("profile: engineering", rendered)
        # 22 specs total; 1 opt-in (coding executor) → 21 spawned.
        # The remaining 21 = 1 supervisor + 1 research + 7 role workers
        # + 1 approval + 1 obsidian + 1 work_order_executor (P0-T) + 1
        # gateway + 1 digest + 7 member.
        self.assertIn("services to start: 21", rendered)
        self.assertIn("eng-research-worker", rendered)
        self.assertIn("eng-discord-gateway", rendered)
        # No reserved block now that gateway is implemented.
        self.assertNotIn("reserved (not started)", rendered)

    def test_env_flag_flips_coding_executor_into_spawn_set(self) -> None:
        # #73 Round 2 — when the operator sets
        # ``YULE_CODING_EXECUTOR_AUTOSPAWN=true`` in their .env.local,
        # the dry-run plan must list eng-coding-executor in the
        # services-to-start block (and remove it from the opt-in
        # block). Verified by reloading services.py + supervisor under
        # a patched env so the import-time evaluation picks up the flag.
        import importlib
        import os

        from yule_orchestrator.runtime import (
            services as services_mod,
            subprocess_supervisor as supervisor_mod,
        )

        original = os.environ.get("YULE_CODING_EXECUTOR_AUTOSPAWN")
        os.environ["YULE_CODING_EXECUTOR_AUTOSPAWN"] = "true"
        try:
            importlib.reload(services_mod)
            importlib.reload(supervisor_mod)
            plan = supervisor_mod.build_dry_run_plan(profile="engineering")
            spawned_ids = {sid for sid, _, _ in plan.services}
            self.assertIn("eng-coding-executor", spawned_ids)
            opt_in_ids = {sid for sid, _ in plan.skipped}
            self.assertNotIn("eng-coding-executor", opt_in_ids)
        finally:
            if original is None:
                del os.environ["YULE_CODING_EXECUTOR_AUTOSPAWN"]
            else:
                os.environ["YULE_CODING_EXECUTOR_AUTOSPAWN"] = original
            importlib.reload(services_mod)
            importlib.reload(supervisor_mod)


# ---------------------------------------------------------------------------
# Spawn + supervise
# ---------------------------------------------------------------------------


class SpawnAndSuperviseTests(unittest.TestCase):
    def test_runtime_up_spawns_each_implemented_service_once(self) -> None:
        spawned: List[List[str]] = []
        terminate_events: List[asyncio.Event] = []

        async def spawn_fn(cmd, env):
            evt = asyncio.Event()
            terminate_events.append(evt)
            spawned.append(list(cmd))
            return _FakeProcess(exit_code_sequence=[], terminate_event=evt)

        async def fast_sleep(_secs):
            return None

        async def driver():
            shutdown = asyncio.Event()
            # Schedule shutdown after a brief delay so spawns happen.
            async def trigger_shutdown():
                await asyncio.sleep(0)  # yield
                await asyncio.sleep(0)
                shutdown.set()

            shutdown_task = asyncio.create_task(trigger_shutdown())
            result = await run_runtime_up(
                profile="engineering",
                spawn_fn=spawn_fn,
                sleep_fn=fast_sleep,
                shutdown_event=shutdown,
                shutdown_timeout=0.0,
            )
            await shutdown_task
            return result

        rc = _run(driver())
        self.assertEqual(rc, 0)
        # Auto-spawn count history: 12 (M6.1b-2) → 13 (F13 #122 digest)
        # → 20 (P0-C #132 added 7 eng-member-* member bots) → 21 (P0-T
        # added eng-github-work-order-executor).
        # eng-coding-executor stays out as opt-in (auto_spawn=False).
        spawned_ids = [cmd[-1] for cmd in spawned]
        self.assertEqual(len(spawned_ids), 21)
        self.assertIn("eng-discord-gateway", spawned_ids)
        self.assertIn("eng-github-work-order-executor", spawned_ids)
        self.assertNotIn("eng-coding-executor", spawned_ids)
        # P0-C: every member bot was spawned exactly once.
        for role in (
            "tech-lead",
            "backend-engineer",
            "qa-engineer",
            "devops-engineer",
            "ai-engineer",
            "frontend-engineer",
            "product-designer",
        ):
            self.assertEqual(spawned_ids.count(f"eng-member-{role}"), 1)
        # Every fake process received terminate() during drain.
        self.assertTrue(all(evt.is_set() for evt in terminate_events))

    def test_child_crash_triggers_restart_with_backoff(self) -> None:
        # First spawn returns a process whose wait() resolves to exit
        # code 1 (crash). Second spawn returns a process that holds
        # until terminate. The supervisor should restart once.
        spawn_count = {"n": 0}
        backoff_sleeps: List[float] = []

        async def spawn_fn(cmd, env):
            evt = asyncio.Event()
            spawn_count["n"] += 1
            if spawn_count["n"] == 1:
                # Crash immediately.
                return _FakeProcess(
                    exit_code_sequence=[1], terminate_event=evt
                )
            # Stay alive until terminate.
            return _FakeProcess(exit_code_sequence=[], terminate_event=evt)

        async def fake_sleep(secs):
            backoff_sleeps.append(secs)

        async def driver():
            shutdown = asyncio.Event()

            async def trigger_shutdown():
                # Wait long enough for spawn #2 to land before shutdown.
                for _ in range(20):
                    await asyncio.sleep(0)
                shutdown.set()

            t = asyncio.create_task(trigger_shutdown())
            # Restrict the profile to just one service so we can
            # observe its restart precisely.
            from yule_orchestrator.runtime import services as svc_mod

            original = svc_mod.PROFILES
            svc_mod.PROFILES = {  # type: ignore[assignment]
                "engineering": (svc_mod.ENGINEERING_PROFILE[1],),  # research worker only
            }
            try:
                result = await run_runtime_up(
                    profile="engineering",
                    spawn_fn=spawn_fn,
                    sleep_fn=fake_sleep,
                    backoff_schedule=(0.5, 1.0),
                    shutdown_event=shutdown,
                    shutdown_timeout=0.0,
                )
            finally:
                svc_mod.PROFILES = original  # type: ignore[assignment]
            await t
            return result

        rc = _run(driver())
        self.assertEqual(rc, 0)
        # At least 2 spawns: initial + restart after crash.
        self.assertGreaterEqual(spawn_count["n"], 2)
        # Backoff sleeps must include the first-tier value (0.5) so
        # we know the restart actually applied the backoff schedule.
        self.assertIn(0.5, backoff_sleeps)

    def test_circuit_open_suppresses_further_restarts(self) -> None:
        # A-M7: every spawn crashes with exit_code=1. With a tight
        # circuit policy (1 restart in 60s) the breaker opens after
        # the first crash → the supervisor must stop respawning.
        from yule_orchestrator.runtime.circuit_breaker import (
            CircuitBreakerPolicy,
            CircuitBreakerRegistry,
        )

        spawn_count = {"n": 0}

        async def spawn_fn(cmd, env):
            spawn_count["n"] += 1
            evt = asyncio.Event()
            return _FakeProcess(
                exit_code_sequence=[1], terminate_event=evt
            )

        async def fast_sleep(_secs):
            return None

        async def driver():
            shutdown = asyncio.Event()

            async def trigger_shutdown():
                # Yield long enough for the supervisor to run, hit
                # the breaker, and exit on its own. Then set shutdown
                # so run_runtime_up's outer await resolves.
                for _ in range(60):
                    await asyncio.sleep(0)
                shutdown.set()

            t = asyncio.create_task(trigger_shutdown())
            from yule_orchestrator.runtime import services as svc_mod

            original = svc_mod.PROFILES
            svc_mod.PROFILES = {  # type: ignore[assignment]
                "engineering": (svc_mod.ENGINEERING_PROFILE[1],),  # research only
            }
            registry = CircuitBreakerRegistry(
                policy=CircuitBreakerPolicy(
                    window_seconds=60.0, max_restarts=1
                )
            )
            try:
                rc = await run_runtime_up(
                    profile="engineering",
                    spawn_fn=spawn_fn,
                    sleep_fn=fast_sleep,
                    backoff_schedule=(0.0,),
                    shutdown_event=shutdown,
                    shutdown_timeout=0.0,
                    circuit_registry=registry,
                )
            finally:
                svc_mod.PROFILES = original  # type: ignore[assignment]
            await t
            return rc, registry

        rc, registry = _run(driver())
        self.assertEqual(rc, 0)
        # max_restarts=1 → tripping requires 2 events. Spawns =
        # initial + 1 retry = 2; on the next loop iteration the
        # breaker is open and the supervisor leaves.
        self.assertEqual(spawn_count["n"], 2)
        snap = registry.snapshot()
        self.assertTrue(
            snap["eng-research-worker"].is_open,
            f"breaker should have tripped, snap={snap}",
        )

    def test_exit_code_78_prevents_restart(self) -> None:
        spawn_count = {"n": 0}

        async def spawn_fn(cmd, env):
            spawn_count["n"] += 1
            evt = asyncio.Event()
            # Always return 78 from wait() — supervisor must respect
            # the "config error" contract and NOT restart.
            return _FakeProcess(
                exit_code_sequence=[EXIT_PREVENT_RESTART], terminate_event=evt
            )

        async def fast_sleep(_secs):
            return None

        async def driver():
            shutdown = asyncio.Event()

            async def trigger_shutdown():
                # Give the supervisor time to observe the 78 exit
                # and decide not to restart.
                for _ in range(50):
                    await asyncio.sleep(0)
                shutdown.set()

            t = asyncio.create_task(trigger_shutdown())
            from yule_orchestrator.runtime import services as svc_mod

            original = svc_mod.PROFILES
            svc_mod.PROFILES = {  # type: ignore[assignment]
                "engineering": (svc_mod.ENGINEERING_PROFILE[1],),
            }
            try:
                rc = await run_runtime_up(
                    profile="engineering",
                    spawn_fn=spawn_fn,
                    sleep_fn=fast_sleep,
                    shutdown_event=shutdown,
                    shutdown_timeout=0.0,
                )
            finally:
                svc_mod.PROFILES = original  # type: ignore[assignment]
            await t
            return rc

        rc = _run(driver())
        self.assertEqual(rc, 0)
        # Exactly one spawn — the supervisor saw 78 and stopped.
        self.assertEqual(spawn_count["n"], 1)


if __name__ == "__main__":
    unittest.main()
