"""Armory intake — the evaluated external-candidate set (real candidates, not generic).

Proves the wave's named candidates are each carried as a real ``armory.candidate``
AdoptionReview (8축 + 3축, no review gaps), classified adopt-now/collect-first/hold by the
EXISTING gate (no new model), that adopt-now Vale yields a registrable spec while
collect-first/hold never do (no fake adoption / adopted≠equipped), that governance/security
candidates are held, and that the committed evidence matches code. Pure / offline.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from armory import catalog
from armory.candidate import ADOPT_NOW, COLLECT_FIRST, HOLD
from forgekit_console import armory_intake as AI
from forgekit_console.tui import render

_NAMED = ("ponytail", "context7", "mcp-fetch", "mcp-filesystem", "mcp-git", "mcp-memory",
          "mcp-sequential-thinking", "vale", "textlint", "alex", "write-good", "proselint",
          "browser-use")


class RegistryTests(unittest.TestCase):
    def test_named_candidates_all_evaluated(self) -> None:
        ids = {c.id for c, _ in AI.intake_candidates()}
        for cid in _NAMED:
            self.assertIn(cid, ids, f"candidate not evaluated: {cid}")

    def test_no_review_gaps(self) -> None:
        for c, rv in AI.intake_candidates():
            self.assertEqual(rv.review_gaps(), (), f"{c.id} review gaps: {rv.review_gaps()}")

    def test_three_axes_each(self) -> None:
        for c, rv in AI.intake_candidates():
            axes = {a.axis for a in rv.axis_reviews}
            self.assertEqual(axes, {"pm", "tech-lead", "specialist"}, f"{c.id} axes={axes}")

    def test_distribution(self) -> None:
        s = AI.intake_summary()
        self.assertEqual(s[ADOPT_NOW], 1)
        self.assertEqual(s[COLLECT_FIRST], 8)
        self.assertEqual(s[HOLD], 4)
        self.assertEqual(s["total"], 13)


class GateTests(unittest.TestCase):
    def test_vale_adopts_with_spec(self) -> None:
        res = {r.candidate_id: r for r in AI.intake_results()}["vale"]
        self.assertEqual(res.disposition, ADOPT_NOW)
        self.assertTrue(res.adopted)
        self.assertIsNotNone(res.spec)  # registrable SkillSpec

    def test_non_adopt_now_have_no_spec(self) -> None:
        # adopted ≠ equipped: collect-first / hold never yield a spec (no fake adoption)
        for r in AI.intake_results():
            if r.disposition != ADOPT_NOW:
                self.assertIsNone(r.spec, f"{r.candidate_id} {r.disposition} leaked a spec")
                self.assertFalse(r.adopted)

    def test_governance_candidates_held(self) -> None:
        held = {r.candidate_id for r in AI.by_disposition(HOLD)}
        for cid in ("mcp-filesystem", "mcp-git", "mcp-sequential-thinking", "browser-use"):
            self.assertIn(cid, held, f"{cid} should be hold")

    def test_doc_linters_collect_first(self) -> None:
        cf = {r.candidate_id for r in AI.by_disposition(COLLECT_FIRST)}
        for cid in ("proselint", "write-good", "alex", "textlint", "context7"):
            self.assertIn(cid, cf)


class CatalogTests(unittest.TestCase):
    def test_doc_quality_lint_loadout(self) -> None:
        lo = catalog.loadout("doc-quality-lint-local")
        self.assertIsNotNone(lo)
        self.assertEqual(lo.required_weapons, ("vale",))
        for w in ("proselint", "write-good", "alex", "textlint"):
            self.assertIn(w, lo.optional_weapons)
        self.assertIn("docs-quality", lo.recommended_skills)

    def test_lint_weapons_declare_install(self) -> None:
        for wid in ("vale", "proselint", "write-good", "alex", "textlint"):
            w = catalog.weapon(wid)
            self.assertIsNotNone(w, wid)
            self.assertTrue(w.install_hint and w.verify_command, f"{wid} missing install/verify")

    def test_tool_less_docs_loadout_untouched(self) -> None:
        # the pre-existing built-in loadout stays tool-less (we added a distinct one)
        lo = catalog.loadout("docs-writing-local")
        self.assertIsNotNone(lo)
        self.assertNotIn("vale", lo.required_weapons + lo.optional_weapons)


class RenderTests(unittest.TestCase):
    def test_summary(self) -> None:
        blob = "\n".join(render.armory_intake_lines(AI.intake_candidates(), AI.intake_results()))
        self.assertIn("armory intake", blob)
        self.assertIn("adopt-now", blob)
        self.assertIn("vale", blob.lower())

    def test_detail(self) -> None:
        blob = "\n".join(render.armory_intake_lines(
            AI.intake_candidates(), AI.intake_results(), detail_id="browser-use"))
        self.assertIn("governance/security", blob)
        self.assertIn("hold", blob)
        self.assertIn("pm", blob)


class EvidenceTests(unittest.TestCase):
    def test_committed_catalog_matches_code(self) -> None:
        path = (Path(__file__).resolve().parents[2] / "apps" / "forgekit-console" /
                "examples" / "armory-intake" / "adoption-catalog.json")
        self.assertTrue(path.exists(), "adoption-catalog.json missing")
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        code_reviews = [rv.to_dict() for _, rv in AI.intake_candidates()]
        self.assertEqual([c["review"] for c in on_disk["candidates"]], code_reviews)
        self.assertEqual(on_disk["summary"], AI.intake_summary())


if __name__ == "__main__":
    unittest.main()
