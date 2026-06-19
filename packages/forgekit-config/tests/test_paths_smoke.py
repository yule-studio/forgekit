"""forgekit-config paths smoke — public surface + env override + compat-shim identity.

Pure/CI-safe (no textual, no IO writes). Proves the WT2 extraction: the canonical
module is ``forgekit_config.paths`` and the old ``forgekit_console.runtime_paths``
path still resolves to the SAME module object (object identity preserved).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from forgekit_config import paths as P


class PathsSurfaceTests(unittest.TestCase):
    def test_default_home_and_env_override(self) -> None:
        # forgekit_home() resolves symlinks (.resolve()), so compare against the same
        # resolution — portable across macOS (/tmp→/private/tmp) and Linux CI (/tmp).
        self.assertEqual(P.forgekit_home({}), Path("~/.forgekit").expanduser().resolve())
        # FORGEKIT_HOME override points the whole tree at a tempdir-like root
        env = {P.ENV_HOME: "/tmp/fk-test-home"}
        self.assertEqual(P.forgekit_home(env), Path("/tmp/fk-test-home").resolve())
        self.assertEqual(P.forgekit_home(env).name, "fk-test-home")

    def test_subpaths_are_under_home(self) -> None:
        env = {P.ENV_HOME: "/tmp/fk-test-home"}
        home = P.forgekit_home(env)
        for fn in (
            P.brain_root, P.personal_brain_dir, P.starter_pack_dir,
            P.config_path, P.state_dir, P.escalation_ledger_path, P.operator_inbox_path,
        ):
            self.assertTrue(str(fn(env)).startswith(str(home)), fn.__name__)

    def test_pure_no_side_effects(self) -> None:
        # calling a path helper never creates anything on disk
        env = {P.ENV_HOME: "/tmp/fk-nonexistent-xyz"}
        _ = P.config_path(env)
        self.assertFalse(Path("/tmp/fk-nonexistent-xyz").exists())


class CompatShimTests(unittest.TestCase):
    def test_old_console_path_aliases_same_module(self) -> None:
        try:
            from forgekit_console import runtime_paths as old
        except ImportError:
            self.skipTest("forgekit_console not on path")
        # the shim aliases itself to the canonical module → identity preserved
        self.assertIs(old, P)
        self.assertIs(old.config_path, P.config_path)


if __name__ == "__main__":
    unittest.main()
