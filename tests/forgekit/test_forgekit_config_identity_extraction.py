"""WT2 guard — forgekit-config also owns agent identity; console shims the old path.

CI-run. Proves identity moved cleanly into forgekit-config: canonical is
``forgekit_config.identity`` (self-contained, no console deps), the old
``forgekit_console.identity`` path resolves to the SAME package + submodule objects,
and console importers (commands.router, vault.authorship) still resolve.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401


class ConfigIdentityExtractionTests(unittest.TestCase):
    def test_canonical_is_forgekit_config(self) -> None:
        from forgekit_config.identity import AgentIdentity  # re-exported
        import forgekit_config.identity.registry as reg

        self.assertTrue(reg.__name__.startswith("forgekit_config.identity"))
        self.assertTrue(AgentIdentity is not None)

    def test_old_path_package_and_submodule_identity(self) -> None:
        import forgekit_config.identity
        import forgekit_config.identity.attribution
        import forgekit_config.identity.registry
        from forgekit_console import identity as old
        from forgekit_console.identity import attribution, registry

        self.assertIs(old, forgekit_config.identity)
        self.assertIs(attribution, forgekit_config.identity.attribution)
        self.assertIs(registry, forgekit_config.identity.registry)

    def test_console_importers_resolve(self) -> None:
        from forgekit_console.commands import router  # noqa: F401
        from forgekit_console.vault import authorship  # noqa: F401

        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
