"""P0-F commit 3 — collector / build_query_for_role wiring of the
canonicalizer.

Covers:

  1. ``build_query_for_role`` rewrites ``dRAG`` → ``RAG`` before
     dedup so the collector query is canonical, not raw.
  2. ``build_canonical_query_for_role`` returns the
     :class:`CanonicalQuery` envelope so the caller can persist
     audit metadata.
  3. ``CollectionOutcome`` carries
     ``raw_query`` / ``canonical_query`` / ``normalization_applied``
     / ``normalization_confidence`` / ``suppress_auto_publish``.
  4. mock-fallback + low-confidence → ``suppress_auto_publish=True``.
  5. mock-fallback + canonical-already (confidence 1.0) →
     ``suppress_auto_publish=False`` (no over-eager suppression).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.research.collector import (
    CollectionMode,
    CollectorConfig,
    auto_collect_or_request_more_input,
    build_canonical_query_for_role,
    build_query_for_role,
)
from yule_engineering.agents.research.query_canonicalizer import CanonicalQuery


class BuildQueryNormalizationTests(unittest.TestCase):
    def test_drag_typo_rewritten_to_rag(self) -> None:
        # ``build_query_for_role`` historically lowercases tokens for
        # dedup. The canonicalization invariant is "raw 'dRAG' must
        # not survive" — the rewritten token may end up lowercased
        # as ``rag/cag`` in the final query string.
        query = build_query_for_role(
            role="engineering-agent/ai-engineer",
            prompt="dRAG/CAG memory 구조 비교해줘",
            task_type="research",
        )
        self.assertNotIn("dRAG", query)
        self.assertIn("rag/cag", query.lower())

    def test_lowercase_acronym_normalised_then_lowercased(self) -> None:
        query = build_query_for_role(
            role="engineering-agent/backend-engineer",
            prompt="rag 와 cag 차이",
        )
        # rag/cag preserved (regardless of final casing).
        self.assertIn("rag", query.lower())
        self.assertIn("cag", query.lower())

    def test_canonical_envelope_round_trip(self) -> None:
        query, canonical = build_canonical_query_for_role(
            role="engineering-agent/ai-engineer",
            prompt="알엠 메모리 구조",
        )
        self.assertIsInstance(canonical, CanonicalQuery)
        self.assertTrue(canonical.normalization_applied)
        self.assertEqual(canonical.confidence, 0.7)
        # The canonical envelope preserves the rewritten case.
        self.assertIn("LLM", canonical.canonical)


class _StubMockCollector:
    """Minimal collector that mimics ``MockCollector`` for routing."""

    name = "mock"

    def search(self, query):  # noqa: ANN001
        return ()


class CollectionOutcomeMetadataTests(unittest.TestCase):
    """Verify the new audit fields land on the outcome."""

    def test_canonical_input_yields_confidence_one(self) -> None:
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/ai-engineer",
            prompt="RAG memory architecture comparison",
            task_type="research",
            collector=_StubMockCollector(),
            config=CollectorConfig(enabled=True, provider="mock", max_results=3),
        )
        self.assertEqual(outcome.normalization_confidence, 1.0)
        self.assertFalse(outcome.normalization_applied)
        self.assertFalse(outcome.suppress_auto_publish)
        # raw / canonical equal (no rewrite needed).
        self.assertEqual(outcome.raw_query, outcome.canonical_query)

    def test_drag_typo_rewritten_and_canonical_recorded(self) -> None:
        outcome = auto_collect_or_request_more_input(
            role="engineering-agent/ai-engineer",
            prompt="dRAG/CAG memory 구조 비교",
            task_type="research",
            collector=_StubMockCollector(),
            config=CollectorConfig(enabled=True, provider="mock", max_results=3),
        )
        # Raw recorded literally.
        self.assertIn("dRAG", outcome.raw_query)
        # Canonical query contains the corrected form.
        self.assertIn("RAG/CAG", outcome.canonical_query)
        self.assertTrue(outcome.normalization_applied)
        # mixed_case confidence is 0.8 < 0.7 threshold? No — 0.8 > 0.7,
        # so suppress_auto_publish is False (we only suppress under
        # the 0.7 cutoff). This is intentional: mixed-case rewrites
        # like dRAG→RAG are high-enough signal to publish.
        self.assertEqual(outcome.normalization_confidence, 0.8)
        self.assertFalse(outcome.suppress_auto_publish)

    def test_low_confidence_fuzzy_with_mock_suppresses_auto_publish(self) -> None:
        # Force a fuzzy rewrite (confidence 0.6) on a mock collector.
        # The canonicalizer's fuzzy rule only fires for length≥3
        # tokens with first-char match. We exercise it directly by
        # patching the canonicalize_query call to return a 0.6-confidence
        # rewrite, then assert the outcome suppresses publish.
        from yule_engineering.agents.research import (
            query_canonicalizer as qc,
        )
        from yule_engineering.agents.research.query_canonicalizer import (
            Replacement,
        )

        fake_canonical = CanonicalQuery(
            raw="rxg memory",
            canonical="RAG memory",
            applied=(
                Replacement(
                    raw="rxg",
                    canonical="RAG",
                    source="fuzzy",
                    confidence=0.6,
                ),
            ),
            confidence=0.6,
        )
        with patch.object(qc, "canonicalize_query", return_value=fake_canonical):
            outcome = auto_collect_or_request_more_input(
                role="engineering-agent/ai-engineer",
                prompt="rxg memory",
                task_type="research",
                collector=_StubMockCollector(),
                config=CollectorConfig(enabled=True, provider="mock", max_results=3),
            )
        self.assertTrue(outcome.normalization_applied)
        self.assertEqual(outcome.normalization_confidence, 0.6)
        # Mock + low-confidence → guard fires.
        self.assertTrue(outcome.suppress_auto_publish)

    def test_low_confidence_with_real_collector_does_not_suppress(self) -> None:
        # If the collector isn't the mock fallback, suppress stays
        # False even with low confidence — real providers can produce
        # useful results from approximate queries.
        class _RealCollectorStub:
            name = "tavily"

            def search(self, query):  # noqa: ANN001
                return ()

        from yule_engineering.agents.research import (
            query_canonicalizer as qc,
        )
        from yule_engineering.agents.research.query_canonicalizer import (
            Replacement,
        )

        fake_canonical = CanonicalQuery(
            raw="rxg memory",
            canonical="RAG memory",
            applied=(
                Replacement(
                    raw="rxg",
                    canonical="RAG",
                    source="fuzzy",
                    confidence=0.6,
                ),
            ),
            confidence=0.6,
        )
        with patch.object(qc, "canonicalize_query", return_value=fake_canonical):
            outcome = auto_collect_or_request_more_input(
                role="engineering-agent/ai-engineer",
                prompt="rxg memory",
                task_type="research",
                collector=_RealCollectorStub(),
                config=CollectorConfig(enabled=True, provider="tavily", max_results=3),
            )
        self.assertFalse(outcome.suppress_auto_publish)


if __name__ == "__main__":
    unittest.main()
