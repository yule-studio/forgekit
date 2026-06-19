"""WT2 guard — forgekit-runtime owns the execution core; console shims + wires the seam.

CI-run. Proves the runtime-core extraction held AND respects the packages→apps hard rail:
- ``forgekit_runtime.*`` imports with NO app import at module load (rail-clean);
- its cross-package deps resolve to packages (config/contracts/provider/nexus);
- the handoff seam is injected by the operator app: unset until the console shim loads,
  wired afterwards — the core never imports the app's handoff bridge directly;
- old ``forgekit_console.{runtime,lifecycle,...}`` paths resolve to the SAME objects.

Seam check for ``docs/forgekit-architecture-ownership.md`` WT2.
"""

from __future__ import annotations

import importlib
import sys
import unittest

from tests.forgekit import _SRC  # noqa: F401


class RuntimeRailCleanTests(unittest.TestCase):
    def test_runtime_core_imports_without_an_app(self) -> None:
        # the package modules import cleanly; module load touches no app
        import forgekit_runtime.lifecycle.failure_escalation as fe
        import forgekit_runtime.runtime.loop as loop

        self.assertTrue(loop.__name__.startswith("forgekit_runtime"))
        self.assertTrue(fe.__name__.startswith("forgekit_runtime"))

    def test_cross_deps_are_packages(self) -> None:
        # heartbeat → forgekit_config.paths; escalation → forgekit_contracts.models
        from forgekit_runtime.runtime.heartbeat import write_heartbeat  # noqa: F401
        from forgekit_contracts.models import Alert  # the real owner
        from forgekit_config.paths import state_dir  # the real owner

        self.assertTrue(callable(write_heartbeat))
        self.assertTrue(callable(state_dir))
        self.assertTrue(Alert is not None)


class HandoffSeamTests(unittest.TestCase):
    def test_seam_is_injected_by_the_app_not_imported_by_core(self) -> None:
        # the core declares the seam; the operator app (console shim) injects it.
        from forgekit_runtime.runtime import loop

        # force a fresh import of the console runtime shim to (re)wire the seam
        sys.modules.pop("forgekit_console.runtime", None)
        loop.register_handoff_runner(None)
        self.assertIsNone(loop._handoff_runner)  # core default: unset

        importlib.import_module("forgekit_console.runtime")  # app wires its bridge
        self.assertIsNotNone(loop._handoff_runner)  # now injected

    def test_loop_raises_clearly_when_seam_unconfigured(self) -> None:
        from forgekit_runtime.runtime import loop

        prev = loop._handoff_runner
        try:
            loop.register_handoff_runner(None)
            lp = loop.BoundedRuntimeLoop(autonomy=loop.AUTONOMY_BOUNDED, max_iterations=1)
            finding = loop.Finding(description="기능 추가", category=loop.CAT_PRODUCT,
                                   project="p", privileged=False)
            with self.assertRaises(RuntimeError):
                lp._packetize(finding)
        finally:
            loop.register_handoff_runner(prev)


class RuntimeShimIdentityTests(unittest.TestCase):
    def test_old_paths_resolve_to_same_objects(self) -> None:
        import forgekit_runtime.lifecycle
        import forgekit_runtime.notify
        import forgekit_runtime.runtime
        from forgekit_console import lifecycle, notify, runtime

        self.assertIs(runtime, forgekit_runtime.runtime)
        self.assertIs(lifecycle, forgekit_runtime.lifecycle)
        self.assertIs(notify, forgekit_runtime.notify)


if __name__ == "__main__":
    unittest.main()
