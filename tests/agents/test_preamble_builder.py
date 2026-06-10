"""F14 preamble builder + cache 회귀."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.preamble import (
    Preamble,
    PreambleBuilder,
    PreambleCache,
    PreambleSection,
    build_default_preamble,
    get_shared_cache,
)
from yule_engineering.agents.preamble.cache import reset_shared_cache_for_tests


class PreambleBuilderTests(unittest.TestCase):
    def _make_repo(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for rel, body in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        return root

    def test_builds_all_present_sections(self) -> None:
        root = self._make_repo({
            "policies/a.md": "alpha body",
            "policies/b.md": "bravo body",
        })
        sources = (("Alpha", "policies/a.md"), ("Bravo", "policies/b.md"))
        b = PreambleBuilder(repo_root=root, sources=sources)
        p = b.build()
        self.assertEqual(len(p.sections), 2)
        self.assertEqual(p.sections[0].title, "Alpha")
        self.assertEqual(p.sections[0].body, "alpha body")
        self.assertGreater(p.total_size_bytes, 0)

    def test_missing_file_yields_placeholder_section(self) -> None:
        root = self._make_repo({"policies/a.md": "alpha"})
        sources = (("Alpha", "policies/a.md"), ("Missing", "policies/missing.md"))
        p = PreambleBuilder(repo_root=root, sources=sources).build()
        self.assertEqual(len(p.sections), 2)
        self.assertIn("missing:", p.sections[1].body)
        self.assertEqual(p.sections[1].size_bytes, 0)

    def test_render_includes_section_titles_and_fingerprints(self) -> None:
        root = self._make_repo({"policies/a.md": "alpha", "policies/b.md": "bravo"})
        p = PreambleBuilder(
            repo_root=root,
            sources=(("Alpha", "policies/a.md"), ("Bravo", "policies/b.md")),
        ).build()
        rendered = p.render_markdown()
        self.assertIn("# agent-preamble", rendered)
        self.assertIn("## Alpha", rendered)
        self.assertIn("## Bravo", rendered)

    def test_render_truncates_long_section(self) -> None:
        big = "x" * 10_000
        root = self._make_repo({"policies/a.md": big})
        p = PreambleBuilder(
            repo_root=root, sources=(("Alpha", "policies/a.md"),),
        ).build()
        rendered = p.render_markdown(max_section_chars=500)
        self.assertIn("truncated", rendered)

    def test_manifest_maps_path_to_fingerprint(self) -> None:
        root = self._make_repo({"policies/a.md": "alpha"})
        p = PreambleBuilder(
            repo_root=root, sources=(("Alpha", "policies/a.md"),),
        ).build()
        m = p.manifest()
        self.assertIn("policies/a.md", m)
        self.assertEqual(len(m["policies/a.md"]), 8)

    def test_build_default_preamble_uses_repo_root(self) -> None:
        # 실제 repo 의 5 default source 가 read 됨 (최소 1 섹션 이상)
        p = build_default_preamble()
        self.assertGreaterEqual(len(p.sections), 5)


class PreambleCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_shared_cache_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "policies").mkdir()
        (self.root / "policies/a.md").write_text("alpha v1", encoding="utf-8")

    def _cache(self) -> PreambleCache:
        return PreambleCache(
            PreambleBuilder(
                repo_root=self.root,
                sources=(("A", "policies/a.md"),),
            )
        )

    def test_cache_returns_same_instance_until_invalidated(self) -> None:
        c = self._cache()
        a = c.get_or_build()
        b = c.get_or_build()
        self.assertIs(a, b)

    def test_cache_rebuilds_when_file_changes(self) -> None:
        c = self._cache()
        first = c.get_or_build()
        # mtime 변경 강제
        time.sleep(0.01)
        (self.root / "policies/a.md").write_text("alpha v2", encoding="utf-8")
        second = c.get_or_build()
        self.assertIsNot(first, second)
        self.assertIn("v2", second.sections[0].body)

    def test_invalidate_forces_rebuild(self) -> None:
        c = self._cache()
        a = c.get_or_build()
        c.invalidate()
        self.assertFalse(c.is_cached())
        b = c.get_or_build()
        self.assertIsNot(a, b)

    def test_shared_cache_is_singleton(self) -> None:
        reset_shared_cache_for_tests()
        a = get_shared_cache()
        b = get_shared_cache()
        self.assertIs(a, b)


if __name__ == "__main__":
    unittest.main()
