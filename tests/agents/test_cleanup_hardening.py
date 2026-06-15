"""Cleanup hardening — sqlite sidecars / secrets / new transient paths (item D)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.cleanup import (
    CLEANUP_SAFE_MARKER,
    Classification,
    classify,
    run_cleanup,
)


class HardenedClassifyTests(unittest.TestCase):
    def test_sqlite_wal_shm_journal_preserved(self) -> None:
        for name in ("cache.sqlite3-wal", "cache.sqlite3-shm", "cache.sqlite3-journal"):
            cls, _r, _ = classify(f".cache/yule/{name}", is_dir=False)
            self.assertEqual(cls, Classification.PRESERVE, name)

    def test_secrets_and_env_preserved(self) -> None:
        for rel in (".env", "config/.env.local", "keys/server.pem", "id_rsa.key", "creds/credentials.json"):
            cls, _r, _ = classify(rel, is_dir=False)
            self.assertEqual(cls, Classification.PRESERVE, rel)

    def test_lockfile_preserved(self) -> None:
        cls, _r, _ = classify("poetry.lock", is_dir=False)
        self.assertEqual(cls, Classification.PRESERVE)

    def test_git_internal_preserved(self) -> None:
        cls, _r, _ = classify(".git/config", is_dir=False)
        self.assertEqual(cls, Classification.PRESERVE)


class NewTransientPathTests(unittest.TestCase):
    def test_new_export_dir_preserves_db_deletes_tmp(self) -> None:
        # A *new* transient location must not break preserve rules: the sqlite
        # operational store survives, sibling .tmp scratch is reclaimable.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exp = root / ".cache" / "yule" / "exports"
            exp.mkdir(parents=True)
            (exp / "session.sqlite3").write_text("db", encoding="utf-8")
            (exp / "session.sqlite3-wal").write_text("wal", encoding="utf-8")
            (exp / "scratch.tmp").write_text("x", encoding="utf-8")
            receipt = run_cleanup(root, execute=True, confirm=True)
            self.assertTrue((exp / "session.sqlite3").exists())
            self.assertTrue((exp / "session.sqlite3-wal").exists())
            self.assertFalse((exp / "scratch.tmp").exists())

    def test_marker_cannot_promote_protected_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # a .env carrying the marker must still be preserved (suffix wins)
            (root / ".env").write_text(f"SECRET=1 {CLEANUP_SAFE_MARKER}", encoding="utf-8")
            receipt = run_cleanup(root, execute=True, confirm=True)
            self.assertTrue((root / ".env").exists())
            protected = {e.rel_path for e in receipt.protected}
            self.assertIn(".env", protected)


if __name__ == "__main__":
    unittest.main()
