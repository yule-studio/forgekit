"""forgekit brain — layers/policy, personal auto-init, starter pack build."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.brain import pack as pack_mod
from forgekit_console.brain import personal as personal_mod
from forgekit_console.brain import policy, service
from forgekit_console.brain.models import (
    LAYER_PERSONAL,
    LAYER_SOURCE,
    LAYER_STARTER,
    LAYER_WORKING,
    BrainNote,
)


class PolicyTests(unittest.TestCase):
    def test_only_personal_writable(self) -> None:
        self.assertTrue(policy.is_writable(LAYER_PERSONAL))
        for layer in (LAYER_STARTER, LAYER_SOURCE, LAYER_WORKING):
            self.assertFalse(policy.is_writable(layer))
            with self.assertRaises(policy.BrainPolicyError):
                policy.assert_writable(layer)

    def test_default_write_target_is_personal(self) -> None:
        self.assertEqual(policy.default_write_target(), LAYER_PERSONAL)

    def test_runtime_read_order_excludes_source(self) -> None:
        order = policy.runtime_read_order()
        self.assertNotIn(LAYER_SOURCE, order)
        self.assertEqual(order[0], LAYER_WORKING)
        self.assertIn(LAYER_STARTER, order)

    def test_unknown_layer_raises(self) -> None:
        with self.assertRaises(policy.BrainPolicyError):
            policy.assert_writable("bogus")


class PersonalBrainTests(unittest.TestCase):
    def test_auto_init_skeleton_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "personal"
            brain = personal_mod.init_personal_brain(base)
            self.assertTrue(brain.is_initialized())
            for sub in ("notes", "decisions", "inbox"):
                self.assertTrue((base / sub).is_dir())
            # idempotent
            personal_mod.init_personal_brain(base)
            self.assertTrue(brain.index_path.exists())

    def test_write_note_frontmatter_and_kind_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = personal_mod.init_personal_brain(Path(tmp))
            path = brain.write_note(BrainNote("My Decision", "we chose X", kind="decision", tags=("infra",)))
            self.assertEqual(path.parent.name, "decisions")
            text = path.read_text(encoding="utf-8")
            self.assertIn("title: My Decision", text)
            self.assertIn("brain_layer: personal", text)
            self.assertIn("we chose X", text)
            self.assertEqual(brain.stats()["total"], 1)

    def test_open_uninitialized_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(personal_mod.open_personal_brain(Path(tmp) / "missing"))


class PackBuildTests(unittest.TestCase):
    def _vault(self, tmp: Path) -> Path:
        vault = tmp / "vault"
        (vault / "notes").mkdir(parents=True)
        (vault / "notes" / "a.md").write_text(
            "---\ntitle: Alpha\ntags: ops infra\n---\n\n" + "x" * 2000, encoding="utf-8"
        )
        (vault / "b.md").write_text("# Plain\n\nhello", encoding="utf-8")
        (vault / ".obsidian").mkdir()
        (vault / ".obsidian" / "skip.md").write_text("should be skipped", encoding="utf-8")
        return vault

    def test_build_pack_manifest_and_readonly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            vault, dest = self._vault(tmp), tmp / "starter"
            manifest = pack_mod.build_starter_pack(vault, dest, digest_chars=200)
            self.assertEqual(manifest.doc_count, 2)  # .obsidian skipped
            self.assertTrue((dest / pack_mod.MANIFEST_NAME).exists())
            self.assertTrue((dest / pack_mod.READONLY_MARKER).exists())
            self.assertTrue((dest / "digests" / "00000.txt").exists())
            # digest of the long doc is truncated
            titles = {e.title for e in manifest.entries}
            self.assertIn("Alpha", titles)        # from frontmatter title
            self.assertIn("b", titles)            # frontmatter-less → filename stem
            big = next(e for e in manifest.entries if e.title == "Alpha")
            self.assertLessEqual(big.digest_chars, 210)

    def test_pack_status_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pack_mod.build_starter_pack(self._vault(tmp), tmp / "starter")
            st = pack_mod.pack_status(tmp / "starter")
            self.assertEqual(st["doc_count"], 2)
            self.assertTrue(st["read_only"])

    def test_missing_source_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(pack_mod.PackBuildError):
                pack_mod.build_starter_pack(Path(tmp) / "nope", Path(tmp) / "out")

    def test_pack_status_none_when_unbuilt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(pack_mod.pack_status(Path(tmp)))


class ServiceTests(unittest.TestCase):
    def test_env_scoped_flow(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as vault:
            env = {"FORGEKIT_HOME": home}
            (Path(vault) / "x.md").write_text("# x\n\nbody", encoding="utf-8")
            # ensure_personal_brain / build_pack_from read env via os.environ; set it
            import os

            prev = os.environ.get("FORGEKIT_HOME")
            os.environ["FORGEKIT_HOME"] = home
            try:
                brain = service.ensure_personal_brain()
                self.assertTrue(brain.is_initialized())
                service.build_pack_from(Path(vault))
                st = service.brain_status()
                self.assertTrue(st["personal_initialized"])
                self.assertTrue(st["starter_built"])
                self.assertEqual(st["starter"]["doc_count"], 1)
            finally:
                if prev is None:
                    os.environ.pop("FORGEKIT_HOME", None)
                else:
                    os.environ["FORGEKIT_HOME"] = prev


if __name__ == "__main__":
    unittest.main()
