"""Token-efficiency core — estimator + slimming transforms (token_budget)."""

from __future__ import annotations

import unittest

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from tests import _bootstrap  # noqa: F401

from yule_engineering.agents.harness.token_budget import (
    build_policy_bundle,
    compact_decisions,
    digest_text,
    estimate_tokens,
    reference_sources,
)
from types import SimpleNamespace


class EstimatorTests(unittest.TestCase):
    def test_deterministic_chars_over_4(self) -> None:
        self.assertEqual(estimate_tokens("a" * 8), 2)
        self.assertEqual(estimate_tokens("a" * 9), 3)  # ceil
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens(None), 0)


class DigestTests(unittest.TestCase):
    def test_digest_keeps_heading_and_first_para(self) -> None:
        text = "# 제목\n\n- bullet\n\n첫 문단 내용입니다.\n둘째 줄."
        d = digest_text(text)
        self.assertIn("제목", d)
        self.assertIn("첫 문단", d)
        self.assertNotIn("bullet", d)

    def test_digest_caps_length(self) -> None:
        d = digest_text("# h\n\n" + "x" * 1000, max_chars=50)
        self.assertLessEqual(len(d), 60)
        self.assertTrue(d.endswith("…"))


class PolicyBundleTests(unittest.TestCase):
    def _docs(self):
        body = "# 정책\n\n규칙 본문. " + ("상세 " * 200)
        return [SimpleNamespace(label="policy", path=f"p/{i}.md", content=body) for i in range(5)]

    def test_digest_smaller_than_full(self) -> None:
        full = build_policy_bundle(self._docs(), mode="full")
        digest = build_policy_bundle(self._docs(), mode="digest")
        self.assertEqual(full.fed_tokens, full.full_tokens)
        self.assertLess(digest.fed_tokens, full.fed_tokens)
        self.assertGreater(digest.saved_tokens, 0)
        self.assertEqual(digest.doc_count, 5)


class CompactDecisionsTests(unittest.TestCase):
    def _decisions(self, n=12):
        out = []
        for i in range(n):
            kind = "decision" if i == 1 else "take"
            out.append({"role": f"r{i}", "kind": kind, "summary": "의견 본문 " * 30, "entry_id": f"a{i}"})
        return out

    def test_noop_under_threshold(self) -> None:
        small = [{"role": "r", "kind": "take", "summary": "짧음"}]
        c = compact_decisions(small, threshold_tokens=1200)
        self.assertFalse(c.applied)
        self.assertEqual(c.saved_tokens, 0)

    def test_folds_over_threshold_and_saves(self) -> None:
        c = compact_decisions(self._decisions(), threshold_tokens=300, keep_recent=4)
        self.assertTrue(c.applied)
        self.assertGreater(c.saved_tokens, 0)
        self.assertGreater(c.folded_count, 0)

    def test_protected_recent_and_decision_preserved(self) -> None:
        decisions = self._decisions(12)
        c = compact_decisions(decisions, threshold_tokens=300, keep_recent=4)
        # most-recent 4 kept verbatim (no 'folded' flag)
        for d in c.decisions[-4:]:
            self.assertNotIn("folded", d)
        # the kind=decision entry (index 1) is preserved verbatim
        decision_entries = [d for d in c.decisions if d.get("kind") == "decision"]
        self.assertTrue(decision_entries)
        self.assertNotIn("folded", decision_entries[0])


class ReferenceSourcesTests(unittest.TestCase):
    def test_reference_mode_shrinks(self) -> None:
        src = {"title": "T", "summary": "긴 요약 " * 80, "sources": [f"u{i}" for i in range(20)]}
        ref = reference_sources(src, max_items=5, max_summary_chars=100)
        self.assertLess(ref.post_tokens, ref.pre_tokens)
        self.assertGreater(ref.saved_tokens, 0)
        self.assertEqual(len(ref.slim["sources"]), 5)
        self.assertLessEqual(len(ref.slim["summary"]), 101)


if __name__ == "__main__":
    unittest.main()
