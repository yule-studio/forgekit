"""Retrieval reuse-boost (memory-policy section 4 live wiring)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.retrieval_boost import (
    BOOST_CANONICAL,
    BOOST_DECISION,
    boost_for,
    rerank,
    to_references,
)


def _doc(note_kind="research", tags=(), extra=None):
    return SimpleNamespace(
        title="t", path="p.md", source_kind="obsidian",
        note_kind=note_kind, tags=tags, extra=extra or {},
    )


def _result(note_kind, tags, extra, score, snippet="snip", body="body", name="t"):
    doc = _doc(note_kind, tags, extra)
    doc.title = name
    doc.path = f"{name}.md"
    doc.body = body
    return SimpleNamespace(document=doc, score=score, snippet=snippet)


class BoostForTests(unittest.TestCase):
    def test_decision_boost(self) -> None:
        b, reasons = boost_for(_doc(note_kind="decision"))
        self.assertEqual(b, BOOST_DECISION)
        self.assertTrue(any("decision" in r for r in reasons))

    def test_canonical_from_extra_and_tag(self) -> None:
        self.assertEqual(boost_for(_doc(extra={"canonical": "true"}))[0], BOOST_CANONICAL)
        self.assertEqual(boost_for(_doc(tags=("canonical",)))[0], BOOST_CANONICAL)

    def test_status_and_reusable_stack(self) -> None:
        b, _ = boost_for(_doc(note_kind="decision", extra={"status": "decided", "reusable": "true"}))
        self.assertEqual(b, BOOST_DECISION + 0.5 + 1.0)

    def test_no_markers_zero(self) -> None:
        self.assertEqual(boost_for(_doc())[0], 0.0)


class RerankTests(unittest.TestCase):
    def test_canonical_outranks_better_bm25(self) -> None:
        # 'plain' has the best (lowest) bm25 but no boost; canonical wins
        results = [
            _result("research", (), {}, -1.3, name="plain"),  # best bm25, no boost
            _result("reference", ("canonical",), {"canonical": "true"}, -0.5, name="canon"),
        ]
        ranked = rerank(results)
        self.assertEqual(ranked[0].path, "canon.md")
        self.assertEqual(ranked[0].boost_score, BOOST_CANONICAL)
        self.assertGreater(len(ranked[0].why_retrieved), 0)

    def test_stable_tie_order(self) -> None:
        results = [_result("research", (), {}, -1.0), _result("research", (), {}, -1.0)]
        ranked = rerank(results)
        self.assertEqual(len(ranked), 2)

    def test_to_references_is_lean(self) -> None:
        results = [_result("decision", (), {}, -0.9, body="x" * 5000)]
        refs = to_references(results, limit=1)
        self.assertEqual(len(refs), 1)
        self.assertIn("why_retrieved", refs[0])
        self.assertNotIn("body", refs[0])  # reference carries snippet, not body


if __name__ == "__main__":
    unittest.main()
