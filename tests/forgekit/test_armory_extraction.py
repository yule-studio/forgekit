"""RWT2 guard — Armory is its own catalog package; Hephaistos depends on it (no cycle).

CI-run. Proves the Armory extraction held:
- the canonical catalog is ``armory.catalog`` + the spec vocabulary is ``armory.models``
  (a real package, leaf — imports nothing from hephaistos → no cycle);
- Hephaistos keeps the forge-output types (WorkPacketDraft / ResolvedForgePlan) and
  re-exports the catalog vocabulary from armory for back-compat
  (``from hephaistos.models import SkillSpec`` still works, same object);
- ``hephaistos.armory`` is a thin shim onto ``armory.catalog``;
- the resolve flow (Hephaistos reading the Armory catalog) still produces a plan.
"""

from __future__ import annotations

import pathlib
import re
import unittest

from tests.forgekit import _SRC  # noqa: F401

REPO = pathlib.Path(__file__).resolve().parents[2]


class ArmoryPackageTests(unittest.TestCase):
    def test_canonical_catalog_and_models(self) -> None:
        import armory
        from armory.catalog import all_skills, all_loadouts, all_weapons
        from armory.models import SkillSpec, LoadoutSpec, WeaponSpec, NexusSourceRef  # noqa: F401

        self.assertTrue(armory.catalog.__name__ == "armory.catalog")
        self.assertGreater(len(all_skills()), 0)
        self.assertGreater(len(all_loadouts()), 0)
        self.assertGreater(len(all_weapons()), 0)

    def test_armory_is_a_leaf_no_hephaistos_import(self) -> None:
        # static check: no armory source file imports hephaistos (would be a cycle)
        armory_src = REPO / "packages" / "armory" / "src" / "armory"
        offenders = []
        pat = re.compile(r"^\s*(?:from|import)\s+hephaistos\b")
        for py in armory_src.rglob("*.py"):
            if any(pat.match(l) for l in py.read_text(encoding="utf-8").splitlines()):
                offenders.append(py.name)
        self.assertEqual(offenders, [], f"armory must not import hephaistos (cycle): {offenders}")


class HephaistosArmoryBoundaryTests(unittest.TestCase):
    def test_forge_types_stay_in_hephaistos(self) -> None:
        from hephaistos.models import WorkPacketDraft, ResolvedForgePlan

        self.assertTrue(WorkPacketDraft.__module__.startswith("hephaistos"))
        self.assertTrue(ResolvedForgePlan.__module__.startswith("hephaistos"))

    def test_catalog_vocab_reexported_with_identity(self) -> None:
        import armory.models
        from hephaistos.models import SkillSpec, NexusSourceRef, WeaponSpec, LoadoutSpec

        self.assertIs(SkillSpec, armory.models.SkillSpec)
        self.assertIs(NexusSourceRef, armory.models.NexusSourceRef)
        self.assertIs(WeaponSpec, armory.models.WeaponSpec)
        self.assertIs(LoadoutSpec, armory.models.LoadoutSpec)

    def test_hephaistos_armory_is_a_shim(self) -> None:
        import armory.catalog
        from hephaistos.armory import all_skills, skill

        self.assertIs(all_skills, armory.catalog.all_skills)
        self.assertIs(skill, armory.catalog.skill)

    def test_resolve_flow_reads_armory_catalog(self) -> None:
        from hephaistos import resolve

        plan = resolve("Spring Boot JWT refresh token")
        self.assertTrue(plan.selected_agent)        # picked an agent from the catalog
        self.assertGreater(len(plan.selected_skills), 0)


if __name__ == "__main__":
    unittest.main()
