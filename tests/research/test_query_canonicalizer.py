"""P0-F commit 2 — engineering-domain query canonicalizer.

Covers:

  1. Empty / whitespace input → empty canonical.
  2. Already-canonical input → no Replacement, confidence 1.0.
  3. Per-token exact match (case-insensitive) → confidence 1.0.
  4. Mixed-case rule — dRAG / aLLM / jWt → drop noise, conf 0.8.
  5. Multi-token rules — ci cd / ci-cd / ci/cd → CI/CD, conf 1.0.
  6. Korean alias — 알엠 / 씨아이씨디 → conf 0.7.
  7. Bounded fuzzy edit-distance 1 — only when ≥3 chars, single
     winner, first-char match.
  8. Compound canonicals — CAG/RAG / RAG-CAG / dRAG/CAG → preserve.
  9. Non-lexicon English (Drag and drop) → untouched.
"""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.research.query_canonicalizer import (
    CanonicalQuery,
    Replacement,
    canonicalize_query,
)


class EmptyInputTests(unittest.TestCase):
    def test_empty_string(self) -> None:
        r = canonicalize_query("")
        self.assertEqual(r.canonical, "")
        self.assertEqual(r.applied, ())
        self.assertEqual(r.confidence, 1.0)
        self.assertFalse(r.normalization_applied)

    def test_whitespace_only(self) -> None:
        r = canonicalize_query("   \n  ")
        self.assertEqual(r.canonical, "")
        self.assertEqual(r.applied, ())


class AlreadyCanonicalTests(unittest.TestCase):
    def test_canonical_acronym_preserved(self) -> None:
        r = canonicalize_query("RAG memory")
        self.assertEqual(r.canonical, "RAG memory")
        self.assertEqual(r.applied, ())
        self.assertEqual(r.confidence, 1.0)

    def test_compound_canonical_preserved(self) -> None:
        r = canonicalize_query("CAG/RAG 비교")
        self.assertEqual(r.canonical, "CAG/RAG 비교")
        self.assertEqual(r.confidence, 1.0)


class ExactMatchTests(unittest.TestCase):
    def test_lowercase_acronym_uppercased(self) -> None:
        r = canonicalize_query("rag vs cag")
        self.assertEqual(r.canonical, "RAG vs CAG")
        self.assertEqual(r.confidence, 1.0)
        sources = {a.source for a in r.applied}
        self.assertEqual(sources, {"exact"})

    def test_oauth_renders_as_OAuth(self) -> None:
        r = canonicalize_query("oauth 토큰")
        self.assertEqual(r.canonical, "OAuth 토큰")

    def test_grpc_renders_as_gRPC(self) -> None:
        r = canonicalize_query("grpc 도입")
        self.assertEqual(r.canonical, "gRPC 도입")


class MixedCaseTests(unittest.TestCase):
    def test_dRAG_to_RAG(self) -> None:
        r = canonicalize_query("dRAG memory")
        self.assertEqual(r.canonical, "RAG memory")
        self.assertEqual(r.confidence, 0.8)
        self.assertEqual(len(r.applied), 1)
        self.assertEqual(r.applied[0].source, "mixed_case")

    def test_aLLM_to_LLM(self) -> None:
        r = canonicalize_query("aLLM 도입")
        self.assertEqual(r.canonical, "LLM 도입")
        self.assertEqual(r.confidence, 0.8)

    def test_compound_dRAG_CAG_drops_noise(self) -> None:
        r = canonicalize_query("dRAG/CAG memory 구조")
        self.assertEqual(r.canonical, "RAG/CAG memory 구조")
        self.assertEqual(r.confidence, 0.8)

    def test_title_case_drag_not_rewritten(self) -> None:
        # "Drag" has only one uppercase char — mixed_case rule
        # requires length ≥2 so it shouldn't fire.
        r = canonicalize_query("Drag and drop")
        self.assertEqual(r.canonical, "Drag and drop")
        self.assertEqual(r.confidence, 1.0)


class MultiTokenTests(unittest.TestCase):
    def test_ci_cd_to_CICD(self) -> None:
        r = canonicalize_query("ci cd 파이프라인")
        self.assertEqual(r.canonical, "CI/CD 파이프라인")
        self.assertEqual(r.confidence, 1.0)

    def test_ci_hyphen_cd_to_CICD(self) -> None:
        r = canonicalize_query("ci-cd 도입")
        self.assertEqual(r.canonical, "CI/CD 도입")

    def test_combined_multitoken_and_exact(self) -> None:
        r = canonicalize_query("llm jwt ci cd 셋업")
        self.assertEqual(r.canonical, "LLM JWT CI/CD 셋업")
        self.assertEqual(r.confidence, 1.0)


class KoreanAliasTests(unittest.TestCase):
    def test_algam_to_LLM(self) -> None:
        r = canonicalize_query("알엠 메모리")
        self.assertEqual(r.canonical, "LLM 메모리")
        self.assertEqual(r.confidence, 0.7)
        self.assertEqual(r.applied[0].source, "korean")

    def test_korean_alias_combined_with_exact(self) -> None:
        r = canonicalize_query("알엠 jwt 비교")
        self.assertEqual(r.canonical, "LLM JWT 비교")
        # min(0.7 korean, 1.0 exact) = 0.7
        self.assertEqual(r.confidence, 0.7)


class FuzzyTests(unittest.TestCase):
    def test_too_short_for_fuzzy(self) -> None:
        # 2-char tokens never enter fuzzy.
        r = canonicalize_query("rg memory")
        self.assertEqual(r.canonical, "rg memory")
        self.assertEqual(r.confidence, 1.0)

    def test_fuzzy_first_char_mismatch_skipped(self) -> None:
        # "zag" first char 'Z' — no lexicon entry starts with Z.
        r = canonicalize_query("zag memory")
        self.assertEqual(r.canonical, "zag memory")
        self.assertEqual(r.confidence, 1.0)


class ConfidenceAggregationTests(unittest.TestCase):
    def test_min_of_replacement_confidences(self) -> None:
        # exact (1.0) + mixed_case (0.8) + korean (0.7) → min 0.7.
        r = canonicalize_query("rag 알엠 dRAG")
        self.assertEqual(r.confidence, 0.7)


class ApiSurfaceTests(unittest.TestCase):
    """Confirm exported dataclasses + entry-point shape are stable."""

    def test_canonical_query_has_expected_fields(self) -> None:
        r = canonicalize_query("rag")
        self.assertIsInstance(r, CanonicalQuery)
        self.assertTrue(hasattr(r, "raw"))
        self.assertTrue(hasattr(r, "canonical"))
        self.assertTrue(hasattr(r, "applied"))
        self.assertTrue(hasattr(r, "confidence"))
        self.assertTrue(hasattr(r, "normalization_applied"))

    def test_replacement_dataclass(self) -> None:
        r = canonicalize_query("rag")
        self.assertGreaterEqual(len(r.applied), 1)
        rep = r.applied[0]
        self.assertIsInstance(rep, Replacement)
        self.assertEqual(rep.raw, "rag")
        self.assertEqual(rep.canonical, "RAG")
        self.assertIn(rep.source, {"exact", "mixed_case", "fuzzy", "korean", "multitoken"})


if __name__ == "__main__":
    unittest.main()
