"""Nexus root / Obsidian vault bootstrap — honest inspect + opt-in scaffold (no fake). Pure.

Proves the lane M2 completion:
- inspect_vault is honest across not_connected/missing/blocked/empty/connected, detects a REAL
  ``.obsidian/`` (never assumes Obsidian), counts notes (bounded), reports KB layout;
- scaffold reports the gap (create=False) and only creates KB dirs with create=True — NEVER
  fabricates ``.obsidian``;
- nexus_ops.apply_bootstrap persists the root (canonical config) + reports the real vault state,
  surviving reload;
- connection_status is enriched (is_vault / note_count) WITHOUT breaking its existing keys.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from hephaistos import nexus_vault as nv
from hephaistos import nexus_ops as nops
from hephaistos import nexus_read as nx


class InspectTests(unittest.TestCase):
    def test_none_root_is_not_connected(self) -> None:
        self.assertEqual(nv.inspect_vault(None).state, nv.VAULT_NOT_CONNECTED)

    def test_missing_path(self) -> None:
        self.assertEqual(nv.inspect_vault(Path("/no/such/forgekit/vault")).state, nv.VAULT_MISSING)

    def test_empty_readable_root_is_empty_not_connected_word(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            insp = nv.inspect_vault(Path(root))
            self.assertEqual(insp.state, nv.VAULT_EMPTY)   # readable but no notes
            self.assertTrue(insp.connected)                # empty still counts as a live root
            self.assertFalse(insp.is_obsidian)
            self.assertEqual(insp.note_count, 0)

    def test_notes_and_obsidian_and_kb_detected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            r = Path(root)
            (r / ".obsidian").mkdir()
            (r / "10-projects").mkdir()
            (r / "note.md").write_text("# hi", encoding="utf-8")
            (r / "10-projects" / "p.md").write_text("# p", encoding="utf-8")
            insp = nv.inspect_vault(r)
            self.assertEqual(insp.state, nv.VAULT_CONNECTED)
            self.assertTrue(insp.is_obsidian)              # REAL .obsidian, not assumed
            self.assertEqual(insp.note_count, 2)
            self.assertIn("10-projects", insp.present_dirs)


class ScaffoldTests(unittest.TestCase):
    def test_report_only_when_create_false(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            res = nv.scaffold_vault(Path(root), create=False)
            self.assertEqual(res.created, ())              # report-only
            self.assertEqual(len(list(Path(root).iterdir())), 0)   # nothing created

    def test_create_makes_kb_dirs_but_never_obsidian(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            res = nv.scaffold_vault(Path(root), create=True)
            self.assertEqual(set(res.created), set(nv.KB_LAYOUT))
            for d in nv.KB_LAYOUT:
                self.assertTrue((Path(root) / d).is_dir())
            self.assertFalse((Path(root) / ".obsidian").exists())   # no fake Obsidian vault

    def test_create_missing_root_is_honest_failure(self) -> None:
        res = nv.scaffold_vault(Path("/no/such/root"), create=True)
        self.assertFalse(res.ok)                            # no fabrication on a missing root


class ApplyBootstrapTests(unittest.TestCase):
    def test_persists_and_reports_and_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as vault:
            env = {"FORGEKIT_HOME": home}
            (Path(vault) / "seed.md").write_text("# seed", encoding="utf-8")
            ok, msg = nops.apply_bootstrap(vault, create=True, env=env)
            self.assertTrue(ok, msg)
            self.assertIn("nexus_root", msg)
            self.assertIn("scaffold", msg)
            # persisted to canonical config → survives a fresh read (restart): load the saved
            # config.json (what the console threads in as ctx.config) and confirm it's connected.
            import json
            from forgekit_config.paths import config_path
            saved = json.loads(config_path(env).read_text(encoding="utf-8"))
            self.assertEqual(saved["nexus_root"], vault)            # persisted
            cs = nx.connection_status(env, saved)
            self.assertTrue(cs["connected"])
            self.assertEqual(cs["root"], vault)
            # KB dirs really created.
            for d in nv.KB_LAYOUT:
                self.assertTrue((Path(vault) / d).is_dir())

    def test_empty_path_refused(self) -> None:
        self.assertFalse(nops.apply_bootstrap("", env={})[0])


class ConnectionStatusEnrichmentTests(unittest.TestCase):
    def test_enriched_keys_are_additive_and_backcompat(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / ".obsidian").mkdir()
            (Path(root) / "a.md").write_text("# a", encoding="utf-8")
            cs = nx.connection_status(env={"FORGEKIT_NEXUS_ROOT": root}, config={})
            # back-compat keys unchanged
            self.assertEqual(cs["status"], "exists")
            self.assertTrue(cs["connected"])
            # additive vault awareness
            self.assertTrue(cs["is_vault"])
            self.assertEqual(cs["note_count"], 1)
            self.assertFalse(cs["empty"])

    def test_not_connected_has_no_false_vault(self) -> None:
        cs = nx.connection_status(env={}, config={})
        self.assertFalse(cs["connected"])
        self.assertNotIn("is_vault", cs)        # only enriched when actually connected (no fake)


if __name__ == "__main__":
    unittest.main()
