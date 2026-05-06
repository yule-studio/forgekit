from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.obsidian_export import ExportPath, ObsidianNote
from yule_orchestrator.agents.obsidian_writer import (
    ENV_VAULT_PATH,
    ObsidianWriteError,
    resolve_vault_root,
    write_note,
)


def _note(folder: str = "Agents/Engineering/Research", filename: str = "2026-04-30_stripe.md") -> ObsidianNote:
    return ObsidianNote(
        path=ExportPath(folder=folder, filename=filename),
        content="---\ntitle: Stripe\n---\n\n# Stripe\n",
        frontmatter={"title": "Stripe"},
    )


class ResolveVaultRootTestCase(unittest.TestCase):
    def test_returns_resolved_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = resolve_vault_root(env={ENV_VAULT_PATH: tmp})
            self.assertEqual(root, Path(tmp).resolve())

    def test_override_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = resolve_vault_root(env={ENV_VAULT_PATH: "/nonexistent"}, override=tmp)
            self.assertEqual(root, Path(tmp).resolve())

    def test_missing_env_raises(self) -> None:
        with self.assertRaises(ObsidianWriteError) as ctx:
            resolve_vault_root(env={})
        self.assertIn(ENV_VAULT_PATH, str(ctx.exception))

    def test_blank_env_raises(self) -> None:
        with self.assertRaises(ObsidianWriteError):
            resolve_vault_root(env={ENV_VAULT_PATH: "   "})

    def test_relative_path_raises(self) -> None:
        with self.assertRaises(ObsidianWriteError) as ctx:
            resolve_vault_root(env={ENV_VAULT_PATH: "relative/vault"})
        self.assertIn("absolute", str(ctx.exception))

    def test_missing_directory_raises(self) -> None:
        with self.assertRaises(ObsidianWriteError) as ctx:
            resolve_vault_root(env={ENV_VAULT_PATH: "/definitely/does/not/exist/yule-vault"})
        self.assertIn("does not exist", str(ctx.exception))

    def test_file_path_raises(self) -> None:
        with tempfile.NamedTemporaryFile() as tmpfile:
            with self.assertRaises(ObsidianWriteError) as ctx:
                resolve_vault_root(env={ENV_VAULT_PATH: tmpfile.name})
            self.assertIn("not a directory", str(ctx.exception))

    def test_expanduser_works(self) -> None:
        home = Path(os.path.expanduser("~")).resolve()
        root = resolve_vault_root(env={ENV_VAULT_PATH: "~"})
        self.assertEqual(root, home)


class WriteNoteTestCase(unittest.TestCase):
    def test_writes_file_under_vault_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            result = write_note(_note(), vault)
            self.assertTrue(result.written)
            self.assertFalse(result.dry_run)
            self.assertTrue(result.target_path.exists())
            self.assertEqual(
                result.target_path,
                (vault / "Agents/Engineering/Research/2026-04-30_stripe.md").resolve(),
            )
            self.assertIn("# Stripe", result.target_path.read_text(encoding="utf-8"))

    def test_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            self.assertFalse((vault / "Agents/Engineering/Research").exists())
            write_note(_note(), vault)
            self.assertTrue((vault / "Agents/Engineering/Research").is_dir())

    def test_first_collision_auto_suffixes_to_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            first = write_note(_note(), vault)
            self.assertTrue(first.written)
            self.assertFalse(first.suffix_applied)

            second = write_note(_note(), vault)
            self.assertTrue(second.written)
            self.assertTrue(second.suffix_applied)
            self.assertEqual(second.target_path.name, "2026-04-30_stripe_2.md")
            self.assertEqual(
                second.original_target_path,
                (vault / "Agents/Engineering/Research/2026-04-30_stripe.md").resolve(),
            )
            # Original file is untouched
            self.assertEqual(
                first.target_path.read_text(encoding="utf-8"),
                "---\ntitle: Stripe\n---\n\n# Stripe\n",
            )

    def test_second_collision_auto_suffixes_to_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            write_note(_note(), vault)
            write_note(_note(), vault)  # creates _2.md

            third = write_note(_note(), vault)
            self.assertTrue(third.written)
            self.assertTrue(third.suffix_applied)
            self.assertEqual(third.target_path.name, "2026-04-30_stripe_3.md")

    def test_overwrite_keeps_original_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            first = write_note(_note(), vault)
            replacement = ObsidianNote(
                path=ExportPath(folder="Agents/Engineering/Research", filename="2026-04-30_stripe.md"),
                content="---\ntitle: Stripe v2\n---\n\n# Stripe v2\n",
                frontmatter={"title": "Stripe v2"},
            )
            result = write_note(replacement, vault, overwrite=True)
            self.assertTrue(result.written)
            self.assertFalse(result.suffix_applied)
            self.assertEqual(result.target_path, first.target_path)
            self.assertIn("Stripe v2", result.target_path.read_text(encoding="utf-8"))
            # No _2.md was created
            self.assertFalse(
                (vault / "Agents/Engineering/Research/2026-04-30_stripe_2.md").exists()
            )

    def test_dry_run_with_collision_reports_final_suffixed_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            write_note(_note(), vault)  # occupy original name

            result = write_note(_note(), vault, dry_run=True)
            self.assertFalse(result.written)
            self.assertTrue(result.dry_run)
            self.assertTrue(result.suffix_applied)
            self.assertEqual(result.target_path.name, "2026-04-30_stripe_2.md")
            # Dry-run must not create the suffixed file
            self.assertFalse(result.target_path.exists())

    def test_overwrite_replaces_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            write_note(_note(), vault)
            replacement = ObsidianNote(
                path=ExportPath(folder="Agents/Engineering/Research", filename="2026-04-30_stripe.md"),
                content="---\ntitle: Stripe v2\n---\n\n# Stripe v2\n",
                frontmatter={"title": "Stripe v2"},
            )
            result = write_note(replacement, vault, overwrite=True)
            self.assertTrue(result.written)
            self.assertIn("Stripe v2", result.target_path.read_text(encoding="utf-8"))

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            result = write_note(_note(), vault, dry_run=True)
            self.assertFalse(result.written)
            self.assertTrue(result.dry_run)
            self.assertFalse(result.target_path.exists())

    def test_path_traversal_via_parent_segments_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            evil = ObsidianNote(
                path=ExportPath(folder="../escape", filename="leak.md"),
                content="x",
                frontmatter={},
            )
            with self.assertRaises(ObsidianWriteError) as ctx:
                write_note(evil, vault)
            self.assertIn("outside the vault root", str(ctx.exception))

    def test_path_traversal_via_symlink_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            outside.mkdir()
            vault = Path(tmp) / "vault"
            vault.mkdir()
            (vault / "Agents").symlink_to(outside, target_is_directory=True)

            note = ObsidianNote(
                path=ExportPath(folder="Agents/Engineering/Research", filename="leak.md"),
                content="x",
                frontmatter={},
            )
            with self.assertRaises(ObsidianWriteError):
                write_note(note, vault)

    def test_parent_directory_creation_failure_is_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "Agents").write_text("not a directory", encoding="utf-8")

            with self.assertRaises(ObsidianWriteError) as ctx:
                write_note(_note(), vault)

            self.assertIn("Could not prepare parent directories", str(ctx.exception))

    def test_write_failure_is_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            target = vault / "Agents/Engineering/Research/2026-04-30_stripe.md"
            target.mkdir(parents=True)

            with self.assertRaises(ObsidianWriteError) as ctx:
                write_note(_note(), vault, overwrite=True)

            self.assertIn("Could not write Obsidian note", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
