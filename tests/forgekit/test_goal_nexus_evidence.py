"""Goal progression → Nexus evidence axis (real artifacts accumulate).

Proves this lane: a goal's append-only EvidenceRecords are MIRRORED into the Nexus evidence
axis as authored notes carrying the fixed schema (goal_id/lane/packet_id/role/status/
created_at/evidence_path), so ``/goal evidence`` reflects real artifact growth — and role/
agent colour separation is carried by schema + identity, not ad-hoc text. Honest: no vault
→ no write; idempotent re-publish; no raw dump (structured note).

Network-free + deterministic: isolated FORGEKIT_HOME (goal store) + throwaway vault.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import goal_surface as gs
from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.router import ConsoleContext, route
from forgekit_goal import Goal, GoalStore
from nexus.vault import EvidenceMeta, LANE_GOAL, build_evidence_note, write_evidence_note


def _rmtree(p: Path) -> None:
    __import__("shutil").rmtree(p, ignore_errors=True)


class NexusEvidenceSchemaTests(unittest.TestCase):
    """The reusable EvidenceMeta schema + writer (nexus.vault.evidence)."""

    def test_schema_keys_in_frontmatter(self) -> None:
        meta = EvidenceMeta(goal_id="goal-x", lane=LANE_GOAL, packet_id="pkt-1",
                            role="platform-runtime-engineer", source="goal-progression",
                            status="active", created_at="2026-06-23")
        note = build_evidence_note(meta, title="t", summary="s")
        for key in ("goal_id:", "lane:", "packet_id:", "role:", "status:", "evidence_path:"):
            self.assertIn(key, note)
        # role drives authored colour/visibility (schema-based separation, not text)
        self.assertIn("cssclasses: [fk-platform]", note)
        self.assertIn("agent_color:", note)

    def test_write_stamps_self_path_and_lands_in_evidence_dir(self) -> None:
        vault = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rmtree(vault))
        meta = EvidenceMeta(goal_id="goal-x", lane=LANE_GOAL, created_at="2026-06-23")
        path, stamped = write_evidence_note(meta, vault, title="t", summary="s", slug="exec-0")
        rel = path.relative_to(vault).as_posix()
        self.assertEqual(rel, "00-inbox/evidence/goal/goal-x/exec-0.md")
        self.assertEqual(stamped.evidence_path, rel)                 # self-describing
        self.assertIn(f"evidence_path: {rel}", path.read_text(encoding="utf-8"))

    def test_no_vault_returns_none(self) -> None:
        meta = EvidenceMeta(goal_id="g", lane=LANE_GOAL)
        self.assertIsNone(write_evidence_note(meta, None, title="t", summary="s", slug="z"))

    def test_frontmatter_extra_is_emitted(self) -> None:
        from nexus.vault import build_authored_note

        note = build_authored_note("knowledge-engineer", title="t", body="b",
                                   extra={"goal_id": "g1", "lane": "goal"})
        self.assertIn("goal_id: g1", note)
        self.assertIn("lane: goal", note)

    def test_discovery_intake_seam_uses_same_schema(self) -> None:
        # the future external-collection loop attaches here with the SAME schema/location.
        from nexus.vault import LANE_DISCOVERY, discovery_intake_meta, write_evidence_note

        vault = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rmtree(vault))
        meta = discovery_intake_meta("geeknews", created_at="2026-06-23")
        self.assertEqual(meta.lane, LANE_DISCOVERY)
        self.assertEqual(meta.source, "discovery-intake")
        path, stamped = write_evidence_note(meta, vault, title="intake", summary="s", slug="a")
        self.assertEqual(path.relative_to(vault).as_posix(),
                         "00-inbox/evidence/discovery/geeknews/a.md")


class GoalPublishBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.vault = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: _rmtree(self.home))
        self.addCleanup(lambda: _rmtree(self.vault))
        self.env = {"FORGEKIT_HOME": str(self.home)}
        self.store = GoalStore(env=self.env)

    def _goal_with_evidence(self):
        g = Goal.create("Nexus evidence lane", intent="evidence 축 누적")
        g = g.add_evidence("proposal", "packet pkt-1 제안", ref="pkt-1")
        g = g.add_evidence("execution", "pkt-1 safe 실행", ref="pkt-1")
        g = g.add_evidence("decision", "operator 승인")
        self.store.save(g)
        return g

    def test_publish_mirrors_records_and_grows_evidence(self) -> None:
        g = self._goal_with_evidence()
        before = len(g.evidence)
        ok, lines = gs.apply_publish_evidence(self.env, g.id, {"nexus_root": str(self.vault)})
        self.assertTrue(ok)
        reloaded = self.store.load(g.id)
        # 3 source records → 3 nexus-note records appended
        self.assertEqual(len(reloaded.evidence), before + 3)
        notes = [e for e in reloaded.evidence if e.kind == "nexus-note"]
        self.assertEqual(len(notes), 3)
        for e in notes:                                              # ref is a real written path
            self.assertTrue(Path(e.ref).exists())
        self.assertEqual(len(list(self.vault.rglob("*.md"))), 3)

    def test_publish_is_idempotent(self) -> None:
        g = self._goal_with_evidence()
        cfg = {"nexus_root": str(self.vault)}
        gs.apply_publish_evidence(self.env, g.id, cfg)
        ok, lines = gs.apply_publish_evidence(self.env, g.id, cfg)   # second run
        self.assertTrue(ok)
        self.assertIn("새로 mirror 할 evidence 없음", "\n".join(lines))
        self.assertEqual(len(list(self.vault.rglob("*.md"))), 3)     # no duplicate notes

    def test_publish_no_vault_is_honest(self) -> None:
        g = self._goal_with_evidence()
        ok, lines = gs.apply_publish_evidence(self.env, g.id, None)  # no nexus_root
        self.assertFalse(ok)
        self.assertIn("미연결", "\n".join(lines))
        self.assertEqual(len(list(self.vault.rglob("*.md"))), 0)     # nothing written (no fake)

    def test_role_mapping_in_written_note(self) -> None:
        g = self._goal_with_evidence()
        gs.apply_publish_evidence(self.env, g.id, {"nexus_root": str(self.vault)})
        exec_note = next(p for p in self.vault.rglob("*.md") if "execution" in p.name)
        text = exec_note.read_text(encoding="utf-8")
        self.assertIn("role: platform-runtime-engineer", text)      # execution → platform runtime
        self.assertIn("lane: goal", text)
        self.assertIn(f"goal_id: {g.id}", text)

    # --- router surface -------------------------------------------------------
    def _ctx(self, *, connected=True):
        cfg = {"nexus_root": str(self.vault)} if connected else {}
        return ConsoleContext(repo_root=Path("."), env=self.env, config=cfg)

    def test_router_publish_then_evidence_reflects_growth(self) -> None:
        g = self._goal_with_evidence()
        ctx = self._ctx()
        pub = route(parse_input(f"/goal publish {g.id}"), ctx)
        self.assertEqual(pub.kind, "info")
        self.assertIn("Nexus evidence 3건 기록", "\n".join(pub.lines))
        ev = route(parse_input(f"/goal evidence {g.id}"), ctx)
        self.assertIn("Nexus 기록 3건", "\n".join(ev.lines))
        self.assertIn("⮕nexus", "\n".join(ev.lines))

    def test_router_publish_disconnected_is_error(self) -> None:
        g = self._goal_with_evidence()
        res = route(parse_input(f"/goal publish {g.id}"), self._ctx(connected=False))
        self.assertEqual(res.kind, "error")
        self.assertIn("미연결", "\n".join(res.lines))


if __name__ == "__main__":
    unittest.main()
