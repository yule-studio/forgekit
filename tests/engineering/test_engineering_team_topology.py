"""engineering-agent team topology governance.

Locks the new "4 core execution teams" structure so the coding department
doesn't silently drift back into a flat role list. The SSoT is the department
manifest's ``team_topology`` block plus the human-facing team-structure doc.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "agents" / "engineering-agent" / "manifest.json"
TEAM_STRUCTURE_PATH = (
    REPO_ROOT
    / "policies"
    / "runtime"
    / "agents"
    / "engineering-agent"
    / "team-structure.md"
)


class EngineeringTeamTopologyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.topology = self.manifest.get("team_topology") or {}
        self.doc_text = TEAM_STRUCTURE_PATH.read_text(encoding="utf-8")

    def test_topology_phase_is_mvp_4_core_teams(self) -> None:
        self.assertEqual(self.topology.get("phase"), "mvp-4-core-teams")

    def test_core_execution_team_ids_are_locked(self) -> None:
        teams = self.topology.get("core_execution_teams") or []
        ids = [team.get("team_id") for team in teams]
        self.assertEqual(
            ids,
            [
                "forgekit-core-team",
                "platform-runtime-team",
                "skill-rnd-team",
                "qa-governance-team",
            ],
        )

    def test_each_core_team_has_structure(self) -> None:
        known_roles = set(self.manifest.get("members") or ())
        known_roles.update(self.manifest.get("cross_cutting_reviewers") or ())
        known_roles.update(
            {"platform-runtime-engineer", "knowledge-engineer", "ops-observer"}
        )
        for team in self.topology.get("core_execution_teams") or ():
            with self.subTest(team=team.get("team_id")):
                self.assertTrue(team.get("purpose"))
                self.assertIn(team.get("lead_role"), known_roles)
                members = team.get("member_roles") or []
                self.assertGreaterEqual(len(members), 1)
                for role in members:
                    self.assertIn(role, known_roles)
                self.assertGreaterEqual(len(team.get("owns") or ()), 2)
                self.assertGreaterEqual(len(team.get("expands_to") or ()), 1)

    def test_growth_path_is_4_to_8_to_12(self) -> None:
        growth = self.topology.get("growth_path") or {}
        self.assertEqual(len(growth.get("mvp_4") or ()), 4)
        self.assertEqual(len(growth.get("growth_8") or ()), 8)
        self.assertEqual(len(growth.get("target_12") or ()), 12)
        self.assertIn("revenue-product-lab", growth.get("growth_8") or ())
        self.assertIn(
            "security-governance-engineering-division",
            growth.get("target_12") or (),
        )

    def test_team_structure_doc_mentions_core_teams(self) -> None:
        for needle in (
            "forgekit-core-team",
            "platform-runtime-team",
            "skill-rnd-team",
            "qa-governance-team",
            "4개 핵심 실행팀",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.doc_text)


if __name__ == "__main__":
    unittest.main()
