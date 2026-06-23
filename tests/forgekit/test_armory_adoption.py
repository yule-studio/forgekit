"""Armory intake 도입 검토 (armory.adoption) — 8축 artifact + 3축 검토 + verdict gate.

Proves the adoption registry is honest (every review fully filled + 3축 검토), the
adopt-now gate enforces consensus + install-plan (fake adoption 방지), the doc-quality
loadout is a real catalog entry (adopted, not installed), and the committed evidence
catalog matches code. Pure / offline.
"""

from __future__ import annotations

import dataclasses
import json
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from armory import adoption as A
from armory import adoption_registry as REG
from armory import catalog
from armory.models import KIND_MCP, KIND_TOOL
from forgekit_console.tui import render


def _review(**over):
    base = dict(
        candidate_id="x", name="X", kind=KIND_TOOL, source="https://example.com/x",
        current_pain="구체적 현재 통증 설명 문장",
        expected_benefit="구체적 기대 효과 설명 문장",
        overlap="기존 capability 와의 겹침 설명",
        operational_cost="설치/런타임 부담 설명",
        maintenance_risk="유지보수 리스크 설명",
        provider_runtime_fit="provider/runtime 적합성 설명",
        governance_security="governance/security 영향 설명",
        verdict=A.VERDICT_COLLECT_FIRST,
        reviewers=(
            A.ReviewerVerdict(A.AXIS_PM, A.VERDICT_COLLECT_FIRST, "pm 사유 문장"),
            A.ReviewerVerdict(A.AXIS_TECH_LEAD, A.VERDICT_COLLECT_FIRST, "tl 사유 문장"),
            A.ReviewerVerdict(A.AXIS_SPECIALIST, A.VERDICT_COLLECT_FIRST, "spec 사유 문장"),
        ),
    )
    base.update(over)
    return A.AdoptionReview(**base)


class RegistryTests(unittest.TestCase):
    def test_every_review_is_valid(self) -> None:
        bad = A.invalid_reviews(REG.adoption_registry())
        self.assertEqual(bad, (), f"invalid reviews: {[(r.candidate_id, why) for r, why in bad]}")

    def test_all_three_verdicts_present(self) -> None:
        reviews = REG.adoption_registry()
        self.assertGreaterEqual(len(A.by_verdict(reviews, A.VERDICT_ADOPT_NOW)), 1)
        self.assertGreaterEqual(len(A.by_verdict(reviews, A.VERDICT_COLLECT_FIRST)), 1)
        self.assertGreaterEqual(len(A.by_verdict(reviews, A.VERDICT_HOLD)), 1)

    def test_required_candidates_covered(self) -> None:
        ids = {r.candidate_id for r in REG.adoption_registry()}
        for required in ("ponytail", "context7", "mcp-fetch", "mcp-filesystem", "mcp-git",
                         "mcp-memory", "mcp-sequential-thinking", "vale", "textlint", "alex",
                         "write-good", "proselint", "browser-use"):
            self.assertIn(required, ids, f"missing candidate: {required}")

    def test_vale_is_adopt_now_with_install_plan(self) -> None:
        vale = next(r for r in REG.adoption_registry() if r.candidate_id == "vale")
        self.assertEqual(vale.verdict, A.VERDICT_ADOPT_NOW)
        self.assertTrue(vale.install_plan)  # attach kind adopt-now must declare install
        self.assertEqual(vale.loadout_id, "doc-quality-review-local")


class GateTests(unittest.TestCase):
    def test_thin_field_invalid(self) -> None:
        self.assertTrue(A.validate_review(_review(current_pain="x")))

    def test_missing_axis_invalid(self) -> None:
        r = _review(reviewers=(A.ReviewerVerdict(A.AXIS_PM, A.VERDICT_COLLECT_FIRST, "사유 문장 길이"),))
        self.assertTrue(any("검토 축 누락" in x for x in A.validate_review(r)))

    def test_adopt_now_requires_consensus(self) -> None:
        r = _review(
            verdict=A.VERDICT_ADOPT_NOW,
            install_plan=("brew install x",),
            reviewers=(
                A.ReviewerVerdict(A.AXIS_PM, A.VERDICT_ADOPT_NOW, "pm 사유 문장"),
                A.ReviewerVerdict(A.AXIS_TECH_LEAD, A.VERDICT_COLLECT_FIRST, "tl 반대 사유"),
                A.ReviewerVerdict(A.AXIS_SPECIALIST, A.VERDICT_ADOPT_NOW, "spec 사유 문장"),
            ))
        self.assertTrue(any("합의 아님" in x for x in A.validate_review(r)))

    def test_adopt_now_attach_kind_requires_install_plan(self) -> None:
        r = _review(
            kind=KIND_MCP, verdict=A.VERDICT_ADOPT_NOW, install_plan=(),
            reviewers=(
                A.ReviewerVerdict(A.AXIS_PM, A.VERDICT_ADOPT_NOW, "pm 사유 문장"),
                A.ReviewerVerdict(A.AXIS_TECH_LEAD, A.VERDICT_ADOPT_NOW, "tl 사유 문장"),
                A.ReviewerVerdict(A.AXIS_SPECIALIST, A.VERDICT_ADOPT_NOW, "spec 사유 문장"),
            ))
        self.assertTrue(any("install_plan 없음" in x for x in A.validate_review(r)))

    def test_adopt_now_valid_when_consensus_and_install(self) -> None:
        r = _review(
            kind=KIND_TOOL, verdict=A.VERDICT_ADOPT_NOW, install_plan=("brew install x",),
            reviewers=(
                A.ReviewerVerdict(A.AXIS_PM, A.VERDICT_ADOPT_NOW, "pm 사유 문장"),
                A.ReviewerVerdict(A.AXIS_TECH_LEAD, A.VERDICT_ADOPT_NOW, "tl 사유 문장"),
                A.ReviewerVerdict(A.AXIS_SPECIALIST, A.VERDICT_ADOPT_NOW, "spec 사유 문장"),
            ))
        self.assertEqual(A.validate_review(r), ())


class CatalogIntegrationTests(unittest.TestCase):
    def test_doc_quality_loadout_in_catalog(self) -> None:
        lo = catalog.loadout("doc-quality-review-local")
        self.assertIsNotNone(lo)
        self.assertEqual(lo.required_weapons, ("vale",))
        for w in ("proselint", "write-good", "alex", "textlint"):
            self.assertIn(w, lo.optional_weapons)
        self.assertIn("doc-quality-review", lo.recommended_skills)

    def test_doc_quality_weapons_declare_install_not_run(self) -> None:
        for wid in ("vale", "proselint", "write-good", "alex", "textlint"):
            w = catalog.weapon(wid)
            self.assertIsNotNone(w, wid)
            self.assertTrue(w.install_hint, f"{wid} no install_hint")  # declares install
            self.assertTrue(w.verify_command, f"{wid} no verify_command")  # how to check presence

    def test_doc_quality_skill_vendor_neutral(self) -> None:
        sk = catalog.skill("doc-quality-review")
        self.assertIsNotNone(sk)
        for vendor in ("claude", "codex", "gemini"):
            self.assertNotIn(vendor, sk.capability_note.lower())


class RenderTests(unittest.TestCase):
    def test_summary_lines(self) -> None:
        lines = render.armory_intake_lines(REG.adoption_registry())
        blob = "\n".join(lines)
        self.assertIn("armory intake", blob)
        self.assertIn("adopt-now", blob)
        self.assertIn("vale", blob.lower())

    def test_detail_lines(self) -> None:
        vale = next(r for r in REG.adoption_registry() if r.candidate_id == "vale")
        lines = render.armory_intake_lines(REG.adoption_registry(), detail=vale)
        blob = "\n".join(lines)
        self.assertIn("현재 pain", blob)
        self.assertIn("governance/security", blob)
        self.assertIn("pm", blob)


class EvidenceTests(unittest.TestCase):
    def test_committed_catalog_matches_code(self) -> None:
        path = (Path(__file__).resolve().parents[2] / "apps" / "forgekit-console" /
                "examples" / "armory-intake" / "adoption-catalog.json")
        self.assertTrue(path.exists(), "adoption-catalog.json missing")
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["reviews"], [r.to_dict() for r in REG.adoption_registry()])
        self.assertEqual(on_disk["summary"]["total"], len(REG.adoption_registry()))


if __name__ == "__main__":
    unittest.main()
