"""Hephaistos execution core — equip / Nexus-enrich / ponytail / adoption (wave lane).

Locks the execution upgrade: the ponytail anti-overbuild lens (3 verdicts), the 8-field /
3-axis adoption review (adopt-now/collect-first/hold, no single-axis adopt), the adopted-vs-
equipped split, and the 3 representative scenarios (Terraform→ECS, FE design system, docs
prose) each proving selected/rejected + why + constraints + verification + expected outputs +
runtime/approval. Deterministic: ``which`` + Nexus root injected; no repo/git/network.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from armory import candidate as C
from armory import catalog
from armory.models import KIND_MCP
from hephaistos import forge_execution_plan, ponytail
from hephaistos.projection import execution_lines

_PRESENT = {"terraform", "docker", "node", "npm", "git", "code", "vscode"}
_WHICH = lambda b: ("/usr/bin/" + b) if b in _PRESENT else None  # noqa: E731


# ── ponytail anti-overbuild lens ──────────────────────────────────────────────
class PonytailTests(unittest.TestCase):
    def test_waived_when_proportionate(self) -> None:
        v = ponytail.ponytail_review(request="docs 정리", selected_skills=("docs-quality",),
                                     selected_tools=(), constraints=(), forbidden_scope=())
        self.assertEqual(v.verdict, ponytail.WAIVED_WITH_REASON)
        self.assertFalse(v.needs_escalation)

    def test_review_required_on_unguarded_prod(self) -> None:
        v = ponytail.ponytail_review(request="prod 배포", selected_skills=("aws-ecs",),
                                     selected_tools=("awscli",), constraints=(),
                                     forbidden_scope=("production service 직접 변경",))
        self.assertEqual(v.verdict, ponytail.REVIEW_REQUIRED)
        self.assertTrue(v.needs_escalation)

    def test_prod_guarded_is_not_review(self) -> None:
        v = ponytail.ponytail_review(request="dev 배포", selected_skills=("aws-ecs",),
                                     selected_tools=("awscli",), constraints=("dev 환경부터",),
                                     forbidden_scope=("production apply 금지(승인 필요)",))
        self.assertNotEqual(v.verdict, ponytail.REVIEW_REQUIRED)

    def test_consult_required_on_new_adoption(self) -> None:
        v = ponytail.ponytail_review(request="x", selected_skills=("a",), selected_tools=("a",),
                                     constraints=("c",), new_adoptions=("trivy-scan",))
        self.assertEqual(v.verdict, ponytail.CONSULT_REQUIRED)

    def test_consult_required_on_unconstrained_broad(self) -> None:
        v = ponytail.ponytail_review(request="x", selected_skills=("a", "b", "c", "d"),
                                     selected_tools=("a",), constraints=(),
                                     domains=("backend", "frontend"))
        self.assertEqual(v.verdict, ponytail.CONSULT_REQUIRED)

    def test_every_verdict_has_findings_or_waive_reason(self) -> None:
        v = ponytail.ponytail_review(request="x", selected_skills=("a",), selected_tools=("a",),
                                     constraints=("c",))
        self.assertTrue(v.reason)  # never a silent pass


# ── adoption review: 8-field + 3-axis gate ────────────────────────────────────
def _full_review(**over) -> C.AdoptionReview:
    base = dict(
        candidate_id="trivy-scan", current_pain="이미지 취약점 수동 점검", expected_benefit="CI 게이트 자동화",
        overlap_with_existing="web-security-review 와 부분 겹침(런타임 스캔은 신규)",
        operational_cost="CI 시간 +30s", maintenance_risk="DB 업데이트 의존",
        provider_runtime_fit="github actions 와 적합", governance_security_impact="secret 불필요, 읽기 전용",
        adopt_timing_reason="pain 크고 overlap 작음 → adopt-now",
        axis_reviews=(C.AxisReview(C.AXIS_PM, "pm", C.ADOPT_NOW, "가치 높음"),
                      C.AxisReview(C.AXIS_TECH_LEAD, "tech-lead", C.ADOPT_NOW, "유지비 수용"),
                      C.AxisReview(C.AXIS_SPECIALIST, "security-engineer", C.ADOPT_NOW, "보안 이득")))
    base.update(over)
    return C.AdoptionReview(**base)


class AdoptionReviewTests(unittest.TestCase):
    def test_full_clear_is_adopt_now(self) -> None:
        self.assertEqual(_full_review().disposition(), C.ADOPT_NOW)

    def test_missing_specialist_axis_holds(self) -> None:
        r = _full_review(axis_reviews=(C.AxisReview(C.AXIS_PM, "pm", C.ADOPT_NOW),
                                       C.AxisReview(C.AXIS_TECH_LEAD, "tl", C.ADOPT_NOW)))
        self.assertEqual(r.disposition(), C.HOLD)
        self.assertTrue(any("specialist" in g for g in r.review_gaps()))

    def test_missing_field_holds(self) -> None:
        self.assertEqual(_full_review(operational_cost="").disposition(), C.HOLD)

    def test_any_hold_axis_holds(self) -> None:
        r = _full_review(axis_reviews=_full_review().axis_reviews[:2] +
                         (C.AxisReview(C.AXIS_SPECIALIST, "sec", C.HOLD, "위험"),))
        self.assertEqual(r.disposition(), C.HOLD)

    def test_collect_first_when_an_axis_says_so(self) -> None:
        r = _full_review(axis_reviews=_full_review().axis_reviews[:2] +
                         (C.AxisReview(C.AXIS_SPECIALIST, "sec", C.COLLECT_FIRST, "근거 더"),))
        self.assertEqual(r.disposition(), C.COLLECT_FIRST)

    def test_adopt_candidate_couples_schema_and_review(self) -> None:
        catalog.clear_overlay()
        cand = C.ArmoryCandidate(
            id="trivy-scan", name="Trivy", kind=KIND_MCP, category="security",
            summary="이미지/IaC 취약점 스캔", signals=("trivy",), when_to_use=("CI 스캔",),
            unsafe_boundary=("스캔 무시 prod push 금지",), capability_note="vulnerability scanner",
            install_requirements=("brew install trivy",), provider_affinity=("github",),
            verification=("trivy --version",))
        # adopt-now + valid schema → spec returned (ready to register).
        res = C.adopt_candidate(cand, _full_review())
        self.assertTrue(res.adopted)
        self.assertIsNotNone(res.spec)
        # collect-first → no spec (no fake adoption), evidence kept.
        res2 = C.adopt_candidate(cand, _full_review(
            axis_reviews=_full_review().axis_reviews[:2] +
            (C.AxisReview(C.AXIS_SPECIALIST, "s", C.COLLECT_FIRST),)))
        self.assertFalse(res2.adopted)
        self.assertEqual(res2.disposition, C.COLLECT_FIRST)
        self.assertIsNone(res2.spec)


# ── adopted vs equipped ───────────────────────────────────────────────────────
class EquipTests(unittest.TestCase):
    def test_tool_less_skill_is_ready(self) -> None:
        ep = forge_execution_plan("README 문서 prose 다듬기", env={}, which=_WHICH)
        self.assertIn("docs-quality", ep.plan.selected_skills)
        self.assertEqual(ep.equip.readiness, "ready")
        self.assertEqual(ep.equip.not_equipped, ())   # nothing to equip (built-in)

    def test_adopted_not_equipped_surfaced(self) -> None:
        ep = forge_execution_plan("Terraform 으로 ECS 배포", env={}, which=_WHICH)
        # awscli/gh not in _PRESENT → adopted but not equipped (honest gap).
        self.assertIn("aws-ecs", ep.plan.selected_skills)
        self.assertTrue(set(ep.equip.not_equipped) & {"awscli", "gh"})
        self.assertNotEqual(ep.equip.readiness, "ready")


# ── 3 representative scenarios ─────────────────────────────────────────────────
class ScenarioTerraformEcsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ep = forge_execution_plan(
            "Terraform 으로 ECS 배포 환경 구성. Claude Code 한테 맡길 프롬프트.",
            project_facts=("EKS는 제외", "dev 환경부터", "기존 구조 보존"),
            runtime_constraints=("production apply 금지",), harness="claude-code",
            env={}, which=_WHICH)

    def test_selected_and_rejected(self) -> None:
        for s in ("terraform", "aws-ecs", "docker", "github-actions"):
            self.assertIn(s, self.ep.plan.selected_skills)
        cats = {r.target: r.category for r in self.ep.plan.rejected_candidates}
        self.assertEqual(cats.get("kubernetes"), "project-fact")   # why-not: EKS 제외

    def test_constraints_and_verification(self) -> None:
        self.assertIn("dev 환경부터", self.ep.packet.constraints)
        self.assertTrue(any("terraform" in v for v in self.ep.verification_plan))

    def test_expected_outputs_and_runtime(self) -> None:
        self.assertTrue(self.ep.expected_outputs)
        self.assertTrue(any("승인" in r for r in self.ep.runtime_approval))  # prod → approval
        self.assertEqual(self.ep.packet.harness, "claude-code")


class ScenarioFrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ep = forge_execution_plan(
            "Next.js 디자인 시스템 토큰 간격 일관성 UI refactor",
            project_facts=("기존 토큰 유지", "컴포넌트 단위로"), env={}, which=_WHICH)

    def test_design_loadout_no_backend_leak(self) -> None:
        self.assertEqual(self.ep.plan.selected_loadout, "design-review-local")
        self.assertNotIn("node-nestjs", self.ep.plan.selected_skills)   # domain gate
        self.assertTrue(any(r.target == "node-nestjs" and r.category == "domain-gate"
                            for r in self.ep.plan.rejected_candidates))

    def test_design_skill_present(self) -> None:
        self.assertIn("design-system-review", self.ep.plan.selected_skills)


class ScenarioDocsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ep = forge_execution_plan("README 문서 품질 개선 prose 다듬기", env={}, which=_WHICH)

    def test_built_in_no_tool_equip(self) -> None:
        self.assertEqual(self.ep.plan.selected_skills, ("docs-quality",))
        self.assertEqual(self.ep.equip.not_equipped, ())          # built-in suffices, no tool
        self.assertEqual(self.ep.ponytail.verdict, ponytail.WAIVED_WITH_REASON)

    def test_loadout_leads_to_real_packet(self) -> None:
        # loadout is not a name list — it produces an actual packet with goal + scope.
        self.assertTrue(self.ep.packet.goal)
        self.assertTrue(self.ep.packet.scope)
        self.assertTrue(self.ep.expected_outputs)


# ── projection is read-only / honest ──────────────────────────────────────────
class ProjectionTests(unittest.TestCase):
    def test_execution_lines_reflect_core(self) -> None:
        ep = forge_execution_plan("Terraform 으로 ECS 배포", project_facts=("EKS는 제외",),
                                  env={}, which=_WHICH)
        text = "\n".join(execution_lines(ep))
        self.assertIn("hephaistos execute", text)
        self.assertIn("ponytail", text)
        self.assertIn("equip", text)


if __name__ == "__main__":
    unittest.main()
