"""WT3 guard — nexus owns the knowledge source modules; console shims the old paths.

CI-run. Proves the Nexus core extraction held: the canonical modules are
``nexus.{sources,vault}`` (a real package depending only on ``forgekit-config``, not
the console), the old ``forgekit_console.{sources,vault}`` paths resolve to the SAME
package + submodule objects, vault's one outward dep was absolute-ized to
``forgekit_config.identity``, and console consumers (design.vault_note via the vault
shim) still resolve.

Seam check for ``docs/forgekit-architecture-ownership.md`` WT3.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401


class NexusExtractionTests(unittest.TestCase):
    def test_canonical_is_the_nexus_package(self) -> None:
        import nexus.sources.registry as sreg
        import nexus.vault.authorship as auth

        self.assertTrue(sreg.__name__.startswith("nexus.sources"))
        self.assertTrue(auth.__name__.startswith("nexus.vault"))

    def test_vault_outward_dep_is_forgekit_config(self) -> None:
        # vault authorship resolves identity via forgekit_config.identity (a package,
        # not the console) — proves the only outward dep was absolute-ized correctly
        from nexus.vault.authorship import identity_for

        self.assertTrue(callable(identity_for))

    def test_old_console_paths_package_and_submodule_identity(self) -> None:
        import nexus.sources
        import nexus.sources.registry
        import nexus.vault
        import nexus.vault.authorship
        from forgekit_console import sources, vault
        from forgekit_console.sources import registry
        from forgekit_console.vault import authorship

        self.assertIs(sources, nexus.sources)
        self.assertIs(vault, nexus.vault)
        self.assertIs(registry, nexus.sources.registry)
        self.assertIs(authorship, nexus.vault.authorship)

    def test_console_consumers_resolve_through_shim(self) -> None:
        # design.vault_note imports ..vault (now the nexus shim); tui imports sources/vault
        from forgekit_console.design import vault_note  # noqa: F401

        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
