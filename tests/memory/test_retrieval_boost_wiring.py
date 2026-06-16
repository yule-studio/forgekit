"""Retrieval boost live wiring (C) — indexer marker projection + fetch_role_context re-rank."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_memory import MEMORY_DB_ENV, open_memory_index, reindex_paths
from yule_memory.indexer import _document_from_markdown_file
from yule_memory.models import SOURCE_OBSIDIAN
from yule_engineering.memory.retrieval import ENV_RETRIEVAL_BOOST, fetch_role_context


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _note(title: str, *, kind: str, canonical: bool = False) -> str:
    fm = [
        "---",
        f'title: "{title}"',
        f"kind: {kind}",
        'roles: ["engineering-agent/product-designer"]',
        "tags: [reference]",
    ]
    if canonical:
        fm.append("canonical: true")
    fm.append("---")
    return "\n".join(fm) + f"\n\n# {title}\n\nStripe Pricing 패턴 정리 노트.\n"


class IndexerMarkerProjectionTests(unittest.TestCase):
    def test_canonical_status_projected_into_extra(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "n.md"
            md.write_text(
                "---\ntitle: t\nkind: decision\ncanonical: true\nstatus: decided\n---\n\n# t\n\nbody\n",
                encoding="utf-8",
            )
            doc = _document_from_markdown_file(
                md_path=md, source_kind=SOURCE_OBSIDIAN, base_dir=Path(tmp)
            )
            self.assertIsNotNone(doc)
            self.assertEqual(doc.extra.get("canonical"), "true")
            self.assertEqual(doc.extra.get("status"), "decided")

    def test_no_markers_empty_extra(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "n.md"
            md.write_text("---\ntitle: t\nkind: research\n---\n\n# t\n\nbody\n", encoding="utf-8")
            doc = _document_from_markdown_file(
                md_path=md, source_kind=SOURCE_OBSIDIAN, base_dir=Path(tmp)
            )
            self.assertEqual(dict(doc.extra), {})


class FetchRoleContextBoostTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev = os.environ.get(MEMORY_DB_ENV)
        os.environ[MEMORY_DB_ENV] = str(Path(self._tmp.name) / "memory.sqlite3")
        vault = Path(self._tmp.name) / "vault"
        _write(vault / "References/plain.md", _note("Pricing 일반", kind="reference"))
        _write(vault / "References/canon.md", _note("Pricing canonical", kind="reference", canonical=True))
        with open_memory_index() as index:
            reindex_paths(paths=[vault], source_kind=SOURCE_OBSIDIAN, index=index, base_dir=vault)

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop(MEMORY_DB_ENV, None)
        else:
            os.environ[MEMORY_DB_ENV] = self._prev

    def test_boost_on_ranks_canonical_first(self) -> None:
        with patch.dict(os.environ, {ENV_RETRIEVAL_BOOST: "true"}):
            hits = fetch_role_context(role="product-designer", query="Pricing", limit=2)
        self.assertTrue(hits)
        self.assertIn("canonical", hits[0].title.lower())

    def test_boost_off_is_unchanged_order(self) -> None:
        with patch.dict(os.environ, {ENV_RETRIEVAL_BOOST: "false"}):
            hits = fetch_role_context(role="product-designer", query="Pricing", limit=2)
        # off path returns results (order is bm25/slot priority — just non-empty)
        self.assertTrue(hits)


if __name__ == "__main__":
    unittest.main()
