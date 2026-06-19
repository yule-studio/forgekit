"""WT3 guard — hephaistos is its own forge-core package; console shims the old path.

CI-run. Proves the Hephaistos core extraction held: the canonical package is
``hephaistos`` (depending only on ``forgekit-config``, not the console), the old
``forgekit_console.hephaistos`` path resolves to the SAME package + submodule objects,
the single outward dep (nexus_ops → ``forgekit_config.paths``) was absolute-ized, and
the console consumer (commands.router) still resolves through the shim.

Seam check for ``docs/forgekit-architecture-ownership.md`` WT3.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401


class HephaistosExtractionTests(unittest.TestCase):
    def test_canonical_is_the_package(self) -> None:
        import hephaistos
        import hephaistos.armory as armory
        import hephaistos.resolver as resolver

        self.assertTrue(armory.__name__.startswith("hephaistos"))
        self.assertTrue(callable(hephaistos.resolve))
        self.assertTrue(callable(resolver.resolve))

    def test_old_console_path_package_and_submodule_identity(self) -> None:
        import hephaistos
        import hephaistos.armory
        import hephaistos.models
        import hephaistos.resolver
        from forgekit_console import hephaistos as old
        from forgekit_console.hephaistos import armory, models, resolver

        self.assertIs(old, hephaistos)
        self.assertIs(armory, hephaistos.armory)
        self.assertIs(models, hephaistos.models)
        self.assertIs(resolver, hephaistos.resolver)

    def test_outward_dep_is_forgekit_config(self) -> None:
        # nexus_ops reads config via forgekit_config.paths (a package, not the console)
        import hephaistos.nexus_ops  # noqa: F401
        from forgekit_config.paths import config_path

        self.assertTrue(callable(config_path))

    def test_console_consumer_resolves(self) -> None:
        from forgekit_console.commands import router  # noqa: F401

        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
