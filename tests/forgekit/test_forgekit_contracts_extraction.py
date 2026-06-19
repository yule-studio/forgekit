"""WT2 extraction guard — forgekit-contracts owns the console data models; console shims.

CI-run (root ``tests/`` tree). Proves the schema extraction held: the canonical module
is ``forgekit_contracts.models`` (a pure stdlib package, not the console), the old
``forgekit_console.models`` path resolves to the SAME module object, and a representative
core importer (commands.parser) still resolves through the shim.

Seam check for ``docs/forgekit-architecture-ownership.md`` WT2.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401


class ContractsExtractionTests(unittest.TestCase):
    def test_canonical_is_the_package(self) -> None:
        from forgekit_contracts import models

        self.assertTrue(models.__name__.startswith("forgekit_contracts"))
        # the kind/mode constants are present (pure schema, no IO)
        self.assertEqual(models.KIND_INFO, "info")
        self.assertEqual(models.MODE_OPERATOR, "operator")

    def test_old_console_path_is_a_compat_alias(self) -> None:
        from forgekit_console import models as old
        from forgekit_contracts import models as canon

        self.assertIs(old, canon)
        self.assertIs(old.KIND_INFO, canon.KIND_INFO)

    def test_core_importers_still_resolve(self) -> None:
        # commands.parser/registry import the top-level models via the shim
        from forgekit_console.commands import parser, registry  # noqa: F401

        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
