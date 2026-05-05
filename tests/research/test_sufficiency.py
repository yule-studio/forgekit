"""Sufficiency scoring tests (Part 3 scaffold)."""

from __future__ import annotations

import unittest
from datetime import datetime

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_orchestrator.agents.research_pack import (
    ResearchPack,
    ResearchSource,
)
from yule_orchestrator.agents.research_sufficiency import (
    DEFAULT_ROLE_TARGETS,
    RoleSufficiencyTarget,
    score_research_sufficiency,
    under_covered_roles,
)


def _source(url: str, *, source_type: str | None = None) -> ResearchSource:
    extra = {"source_type": source_type} if source_type else {}
    return ResearchSource(
        source_url=url,
        title=url,
        author_role="engineering-agent/tech-lead",
        extra=extra,
    )


class SufficiencyTests(unittest.TestCase):
    def test_no_pack_marked_insufficient(self) -> None:
        result = score_research_sufficiency(None)
        self.assertFalse(result.sufficient)
        self.assertEqual(result.distinct_url_count, 0)
        self.assertTrue(result.notes)

    def test_empty_pack_marked_insufficient(self) -> None:
        pack = ResearchPack(title="x", summary="")
        result = score_research_sufficiency(pack)
        self.assertFalse(result.sufficient)
        self.assertGreaterEqual(len(under_covered_roles(result)), 1)

    def test_dedupes_distinct_urls(self) -> None:
        pack = ResearchPack(
            title="x",
            sources=(
                _source("https://x"),
                _source("https://x"),  # duplicate
                _source("https://y", source_type="official_docs"),
            ),
        )
        result = score_research_sufficiency(pack)
        self.assertEqual(result.distinct_url_count, 2)

    def test_minimal_target_passes_with_official_docs(self) -> None:
        targets = (
            RoleSufficiencyTarget(
                role="tech-lead",
                min_sources=1,
                required_types=("official_docs",),
            ),
        )
        pack = ResearchPack(
            title="x",
            sources=(_source("https://docs.example.com", source_type="official_docs"),),
        )
        result = score_research_sufficiency(pack, role_targets=targets)
        self.assertTrue(result.sufficient)

    def test_under_covered_roles_returns_failing_roles(self) -> None:
        pack = ResearchPack(
            title="x",
            sources=(_source("https://only-url"),),  # raw url, no docs/refs
        )
        result = score_research_sufficiency(pack)
        roles = under_covered_roles(result)
        # Default targets are conservative — without docs/refs/github,
        # multiple roles should still be flagged.
        self.assertTrue(roles)
        self.assertIn("ai-engineer", roles)


if __name__ == "__main__":
    unittest.main()
