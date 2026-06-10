"""P0-J commit 3 — official docs seed tests."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.coding.official_docs_seed import (
    OfficialDocsSource,
    known_canonicals,
    seed_official_docs,
)
from yule_engineering.agents.coding.stack_detector import detect_stacks


# ---------------------------------------------------------------------------
# Core seed entries — naver-search-clone scenario
# ---------------------------------------------------------------------------


class NaverSearchCloneSeedTests(unittest.TestCase):
    def test_full_stack_detection_seeds_core_docs(self) -> None:
        text = "Next.js + NestJS + PostgreSQL + Docker Compose 회원가입/로그인/검색"
        detected = detect_stacks(text)
        seeds = seed_official_docs(detected.stacks)
        canonicals = {s.canonical for s in seeds}
        # Core 4 stacks must all be seeded. (Docker alias also fires
        # inside "Docker Compose" — that's expected; both seeds are
        # informative for the gateway.)
        for required in ("Next.js", "NestJS", "PostgreSQL", "Docker Compose"):
            self.assertIn(required, canonicals, required)
        self.assertGreaterEqual(len(seeds), 4)

    def test_nextjs_url_pinned(self) -> None:
        seeds = seed_official_docs(("Next.js",))
        self.assertEqual(seeds[0].url, "https://nextjs.org/docs")

    def test_nestjs_url_pinned(self) -> None:
        seeds = seed_official_docs(("NestJS",))
        self.assertEqual(seeds[0].url, "https://docs.nestjs.com")

    def test_postgresql_url_pinned(self) -> None:
        seeds = seed_official_docs(("PostgreSQL",))
        self.assertEqual(seeds[0].url, "https://www.postgresql.org/docs/current/")

    def test_docker_compose_url_pinned(self) -> None:
        seeds = seed_official_docs(("Docker Compose",))
        self.assertEqual(seeds[0].url, "https://docs.docker.com/compose/")


# ---------------------------------------------------------------------------
# Edge cases — empty / unknown / dedup
# ---------------------------------------------------------------------------


class SeedEdgeCaseTests(unittest.TestCase):
    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(seed_official_docs(()), ())

    def test_unknown_canonical_silently_skipped(self) -> None:
        seeds = seed_official_docs(("NotAStack", "Next.js"))
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0].canonical, "Next.js")

    def test_duplicate_canonical_deduped(self) -> None:
        seeds = seed_official_docs(("Next.js", "Next.js"))
        self.assertEqual(len(seeds), 1)

    def test_order_preserved(self) -> None:
        seeds = seed_official_docs(("PostgreSQL", "Next.js", "Docker"))
        self.assertEqual(
            [s.canonical for s in seeds],
            ["PostgreSQL", "Next.js", "Docker"],
        )


# ---------------------------------------------------------------------------
# Lexicon coverage
# ---------------------------------------------------------------------------


class LexiconCoverageTests(unittest.TestCase):
    """Most lexicon entries should have a docs seed (>= 30 covered)."""

    def test_minimum_canonical_count(self) -> None:
        # As of P0-J: 37 canonicals.
        self.assertGreaterEqual(len(known_canonicals()), 30)

    def test_core_stacks_all_present(self) -> None:
        core = (
            "Next.js",
            "NestJS",
            "PostgreSQL",
            "Docker",
            "Docker Compose",
            "React",
            "FastAPI",
            "Django",
            "Kubernetes",
            "GitHub Actions",
            "Redis",
            "JWT",
            "OAuth",
        )
        canonicals = set(known_canonicals())
        for name in core:
            self.assertIn(name, canonicals, name)

    def test_all_urls_start_with_https(self) -> None:
        seeds = seed_official_docs(known_canonicals())
        for entry in seeds:
            self.assertTrue(
                entry.url.startswith("https://"),
                f"{entry.canonical} url not https: {entry.url}",
            )

    def test_all_entries_have_source_type_official_docs(self) -> None:
        seeds = seed_official_docs(known_canonicals())
        for entry in seeds:
            self.assertEqual(entry.source_type, "official_docs")


# ---------------------------------------------------------------------------
# OfficialDocsSource shape
# ---------------------------------------------------------------------------


class SourceShapeTests(unittest.TestCase):
    def test_all_required_fields(self) -> None:
        seed = seed_official_docs(("Next.js",))[0]
        self.assertIsInstance(seed, OfficialDocsSource)
        self.assertTrue(seed.canonical)
        self.assertTrue(seed.title)
        self.assertTrue(seed.url)
        self.assertTrue(seed.domain)
        self.assertTrue(seed.snippet)


if __name__ == "__main__":
    unittest.main()
