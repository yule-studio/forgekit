"""WT2 extraction guard — forgekit-config owns runtime paths; console keeps a shim.

CI-run (root ``tests/`` tree). Proves the ForgeKit-core extraction held:
- the canonical module is ``forgekit_config.paths`` (a real package, not console);
- the old ``forgekit_console.runtime_paths`` path still resolves to the SAME module
  object (compat shim, object identity preserved) so the 10 console importers work;
- a representative console module that pulls in runtime_paths still imports cleanly.

This is the seam check for ``docs/forgekit-architecture-ownership.md`` WT2.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401


class ForgekitConfigExtractionTests(unittest.TestCase):
    def test_canonical_module_is_the_package(self) -> None:
        from forgekit_config import paths as P

        # owner is the package, not the console app
        self.assertIn("forgekit_config", P.__name__)
        self.assertEqual(P.forgekit_home({}), Path("~/.forgekit").expanduser())

    def test_old_console_path_is_a_compat_alias(self) -> None:
        from forgekit_config import paths as P
        from forgekit_console import runtime_paths as old

        # sys.modules alias → identical module + identical function objects
        self.assertIs(old, P)
        self.assertIs(old.config_path, P.config_path)
        self.assertIs(old.state_dir, P.state_dir)

    def test_console_importers_still_resolve(self) -> None:
        # provider_ops/setup_state import runtime_paths; if the shim broke, this raises
        from forgekit_console.policy import provider_ops, setup_state  # noqa: F401

        env = {"FORGEKIT_HOME": "/tmp/fk-extraction-test"}
        from forgekit_config import paths as P

        # compare against the resolved home (forgekit_home() calls .resolve(), which
        # on macOS maps /tmp → /private/tmp; on Linux CI it stays /tmp — portable).
        home = str(P.forgekit_home(env))
        self.assertTrue(str(P.config_path(env)).startswith(home))
        self.assertEqual(P.config_path(env).name, "config.json")


if __name__ == "__main__":
    unittest.main()
