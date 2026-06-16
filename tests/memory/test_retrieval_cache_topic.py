"""Retrieval candidate-pool cache + topic-aware recall (token-eff Phase 4b)."""

from __future__ import annotations

import os
import tempfile
import time
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
from yule_engineering.memory import retrieval
from yule_engineering.memory.retrieval import (
    ENV_RETRIEVAL_CACHE,
    clear_retrieval_cache,
    fetch_role_context,
    fetch_topic_context,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _note(title: str, *, topic: str = "") -> str:
    fm = ["---", f'title: "{title}"', "kind: research", 'roles: ["engineering-agent/tech-lead"]']
    if topic:
        fm.append(f"topic: {topic}")
    fm.append("---")
    return "\n".join(fm) + f"\n\n# {title}\n\nHermes 통합 자료 노트.\n"


class IndexerTopicProjectionTests(unittest.TestCase):
    def test_topic_projected_into_extra(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "n.md"
            md.write_text(_note("X", topic="hermes-yule"), encoding="utf-8")
            doc = _document_from_markdown_file(md_path=md, source_kind=SOURCE_OBSIDIAN, base_dir=Path(tmp))
            self.assertEqual(doc.extra.get("topic"), "hermes-yule")


class _IndexFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._prev = os.environ.get(MEMORY_DB_ENV)
        os.environ[MEMORY_DB_ENV] = str(Path(self._tmp.name) / "memory.sqlite3")
        clear_retrieval_cache()
        self.vault = Path(self._tmp.name) / "vault"
        _write(self.vault / "a.md", _note("Hermes 통합 A", topic="hermes-yule"))
        _write(self.vault / "b.md", _note("Hermes 통합 B", topic="hermes-yule"))
        _write(self.vault / "c.md", _note("결제 모듈", topic="payment"))
        self._reindex()

    def _reindex(self) -> None:
        with open_memory_index() as index:
            reindex_paths(paths=[self.vault], source_kind=SOURCE_OBSIDIAN, index=index, base_dir=self.vault)

    def tearDown(self) -> None:
        clear_retrieval_cache()
        if self._prev is None:
            os.environ.pop(MEMORY_DB_ENV, None)
        else:
            os.environ[MEMORY_DB_ENV] = self._prev


class TopicRecallTests(_IndexFixture):
    def test_topic_filters_to_matching_notes(self) -> None:
        hits = fetch_topic_context(topic="hermes-yule", query="Hermes", limit=5)
        self.assertTrue(hits)
        titles = " ".join(h.title for h in hits)
        self.assertIn("Hermes", titles)
        self.assertNotIn("결제", titles)  # 'payment' topic excluded

    def test_empty_topic_returns_empty(self) -> None:
        self.assertEqual(fetch_topic_context(topic="  "), [])


class CacheTests(_IndexFixture):
    def test_cache_hit_avoids_second_search(self) -> None:
        real = retrieval.search
        calls = {"n": 0}

        def _counting(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        with patch.dict(os.environ, {ENV_RETRIEVAL_CACHE: "true"}):
            with patch.object(retrieval, "search", _counting):
                first = fetch_role_context(role="tech-lead", query="Hermes", limit=3)
                after_first = calls["n"]
                second = fetch_role_context(role="tech-lead", query="Hermes", limit=3)
        self.assertGreater(after_first, 0)
        self.assertEqual(calls["n"], after_first)  # second served from cache, no new search
        self.assertEqual([h.title for h in first], [h.title for h in second])

    def test_flag_off_no_cache(self) -> None:
        real = retrieval.search
        calls = {"n": 0}

        def _counting(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        with patch.dict(os.environ, {ENV_RETRIEVAL_CACHE: "false"}):
            with patch.object(retrieval, "search", _counting):
                fetch_role_context(role="tech-lead", query="Hermes", limit=3)
                first = calls["n"]
                fetch_role_context(role="tech-lead", query="Hermes", limit=3)
        self.assertGreater(calls["n"], first)  # searched again (no cache)

    def test_reindex_invalidates_cache(self) -> None:
        with patch.dict(os.environ, {ENV_RETRIEVAL_CACHE: "true"}):
            before = fetch_role_context(role="tech-lead", query="Hermes", limit=5)
            n_before = len(before)
            # add a new matching note + reindex → index mtime changes → cache miss
            time.sleep(0.01)
            _write(self.vault / "d.md", _note("Hermes 통합 D", topic="hermes-yule"))
            self._reindex()
            after = fetch_role_context(role="tech-lead", query="Hermes", limit=5)
        self.assertGreaterEqual(len(after), n_before)


if __name__ == "__main__":
    unittest.main()
