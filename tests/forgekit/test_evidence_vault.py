"""Goal evidence → Nexus vault accumulation (final-completion 축4). Pure / stdlib.

Proves the lane-D bridge:
- an append-only goal :class:`EvidenceRecord` becomes a curated, authored vault note
  (frontmatter + 5 sections) under a deterministic per-goal path;
- **no fake nexus connection** — no vault root → ``not_connected`` and nothing written;
- **append-only / idempotent** — re-running skips existing notes (no rewrite);
- end-to-end from the real goal store (tempdir) into a tempdir vault, surviving a fresh read
  (restart): the notes persist on disk.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from nexus.vault import evidence as ev
from forgekit_goal.store import GoalStore
from forgekit_goal.models import Goal


class _Rec:
    """A minimal evidence record (ts/kind/summary/ref) for the pure-path tests."""

    def __init__(self, ts="2026-06-22T10:00:00", kind="observation", summary="did a thing", ref=None):
        self.ts, self.kind, self.summary, self.ref = ts, kind, summary, ref


class NoteFormatTests(unittest.TestCase):
    def test_subpath_is_deterministic_and_per_goal(self) -> None:
        rec = _Rec(kind="execution")
        sub = ev.evidence_subpath("goal-abc123", 2, rec)
        self.assertEqual(sub, "10-projects/forgekit/evidence/goal-abc123/002-execution.md")
        self.assertEqual(sub, ev.evidence_subpath("goal-abc123", 2, rec))   # stable

    def test_note_content_is_authored_and_honest_about_missing_ref(self) -> None:
        content = ev.evidence_note_content("goal-x", "My Goal", 1, _Rec(ref=None))
        self.assertIn("kind: evidence", content)                 # authored frontmatter
        self.assertIn("## 핵심 요약", content)
        self.assertIn("ref 없음", content)                        # honest, not a fabricated ref
        with_ref = ev.evidence_note_content("goal-x", "My Goal", 1, _Rec(ref="PR#343"))
        self.assertIn("PR#343", with_ref)


class AccumulateRecordsTests(unittest.TestCase):
    def test_no_vault_is_not_connected_and_writes_nothing(self) -> None:
        res = ev.accumulate_records("goal-x", "G", [_Rec()], vault_root="")
        self.assertEqual(res.status, ev.STATUS_NOT_CONNECTED)
        self.assertFalse(res.connected)
        self.assertEqual(res.written, ())

    def test_write_evidence_note_no_vault_returns_none(self) -> None:
        self.assertIsNone(ev.write_evidence_note("g", "G", 1, _Rec(), vault_root=""))

    def test_connected_writes_then_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as vault:
            recs = [_Rec(kind="observation"), _Rec(kind="execution")]
            r1 = ev.accumulate_records("goal-x", "Axis 4", recs, vault_root=vault)
            self.assertEqual(r1.status, ev.STATUS_CONNECTED)
            self.assertEqual(len(r1.written), 2)
            self.assertEqual(len(r1.skipped), 0)
            self.assertEqual(len(list(Path(vault).rglob("*.md"))), 2)
            # re-run = append-only: existing notes skipped, none rewritten.
            r2 = ev.accumulate_records("goal-x", "Axis 4", recs, vault_root=vault)
            self.assertEqual(len(r2.written), 0)
            self.assertEqual(len(r2.skipped), 2)


class GoalStoreEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gstore = Path(tempfile.mkdtemp())
        self.vault = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.gstore, ignore_errors=True))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.vault, ignore_errors=True))
        self.env = {"FORGEKIT_HOME": str(self.gstore)}

    def _goal_with_evidence(self) -> str:
        st = GoalStore(env=self.env)
        g = Goal.create("Nexus runtime completion", intent="close axis 4")
        g = g.add_evidence("observation", "per-provider budget merged", ref="PR#343")
        g = g.add_evidence("execution", "evidence→vault bridge built", ref="nexus.vault.evidence")
        st.save(g)
        return g.id

    def test_goal_evidence_accumulates_to_vault(self) -> None:
        gid = self._goal_with_evidence()
        res = ev.accumulate_goal_evidence(gid, vault_root=str(self.vault), env=self.env)
        self.assertEqual(res.status, ev.STATUS_CONNECTED)
        self.assertEqual(res.evidence_count, 2)
        self.assertEqual(len(res.written), 2)
        # the real evidence summary made it into a real note (no fabrication).
        notes = sorted(self.vault.rglob("*.md"))
        self.assertEqual(len(notes), 2)
        self.assertIn("per-provider budget merged", notes[0].read_text(encoding="utf-8"))

    def test_unknown_goal_is_no_goal(self) -> None:
        res = ev.accumulate_goal_evidence("goal-nope", vault_root=str(self.vault), env=self.env)
        self.assertEqual(res.status, ev.STATUS_NO_GOAL)

    def test_no_vault_is_not_connected(self) -> None:
        gid = self._goal_with_evidence()
        self.assertEqual(ev.accumulate_goal_evidence(gid, vault_root="", env=self.env).status,
                         ev.STATUS_NOT_CONNECTED)

    def test_notes_persist_across_fresh_reaccumulate(self) -> None:
        # restart simulation: a brand-new store handle reads the same persisted goal + vault.
        gid = self._goal_with_evidence()
        ev.accumulate_goal_evidence(gid, vault_root=str(self.vault), env=self.env)
        fresh = GoalStore(env=self.env)                 # fresh handle == process restart
        res = ev.accumulate_goal_evidence(gid, vault_root=str(self.vault), env=self.env, store=fresh)
        self.assertEqual(res.status, ev.STATUS_CONNECTED)
        self.assertEqual(len(res.skipped), 2)           # prior notes survived on disk (append-only)


if __name__ == "__main__":
    unittest.main()
