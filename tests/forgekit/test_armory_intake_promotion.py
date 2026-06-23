"""Armory intake → promotion + Hephaistos context-aware selection (RWT2 lane).

Locks the real promotion path (candidate → SkillSpec, with non-placeholder + attach gates),
the runtime overlay (a promoted candidate is picked up by the resolver, then cleared), and
the context-aware selection: project facts drive exclusions, runtime constraints + harness
land in the packet, and EVERY pick/exclusion carries a SelectionEvidence row (no fake
smart-selection). The Terraform→ECS scenario (EKS 제외 / dev-first / keep-structure) is the
representative end-to-end case.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from armory import candidate as C
from armory import catalog
from armory.models import KIND_MCP, KIND_SKILL, KIND_TOOL
from hephaistos import resolve
from hephaistos.projection import selection_evidence_lines


def _complete_candidate(**over) -> C.ArmoryCandidate:
    base = dict(
        id="pulumi", name="Pulumi (IaC)", kind=KIND_SKILL, category="devops",
        summary="Pulumi 로 타입 안전 IaC 작성·preview",
        domains=("devops",), topics=("pulumi", "iac", "deploy"),
        signals=("pulumi",), when_to_use=("범용 언어 기반 IaC",),
        when_not_to_use=("HCL 고정 환경(→terraform)",),
        required_inputs=("대상 클라우드/스택",), expected_outputs=("Pulumi program + preview"),
        unsafe_boundary=("production up 무승인 금지",), capability_note="infra-as-code planner",
        commands=("pulumi preview",), verification=("pulumi preview",),
        related_loadouts=("devops-cloud-local",), related_roles=("devops-engineer",),
        source="discovery", source_ref="brief-42",
    )
    base.update(over)
    return C.ArmoryCandidate(**base)


class PromotionGateTests(unittest.TestCase):
    def test_complete_skill_promotes(self) -> None:
        r = C.promote_candidate(_complete_candidate())
        self.assertTrue(r.accepted, r.reasons)
        self.assertIsNotNone(r.spec)
        self.assertEqual(r.spec.id, "pulumi")
        self.assertEqual(r.spec.forbidden, ("production up 무승인 금지",))  # unsafe → forbidden
        self.assertTrue(any("승격됨" in e for e in r.evidence))

    def test_missing_unsafe_boundary_rejected(self) -> None:
        r = C.promote_candidate(_complete_candidate(unsafe_boundary=()))
        self.assertFalse(r.accepted)
        self.assertTrue(any("unsafe_boundary" in x for x in r.reasons))

    def test_placeholder_summary_rejected(self) -> None:
        r = C.promote_candidate(_complete_candidate(summary="TBD"))
        self.assertFalse(r.accepted)
        self.assertTrue(any("summary" in x for x in r.reasons))

    def test_vendor_capability_note_rejected(self) -> None:
        r = C.promote_candidate(_complete_candidate(capability_note="claude agent runner"))
        self.assertFalse(r.accepted)
        self.assertTrue(any("vendor" in x or "provider-neutral" in x for x in r.reasons))

    def test_tool_without_attach_rejected(self) -> None:
        r = C.promote_candidate(_complete_candidate(
            id="trivy", name="Trivy", kind=KIND_TOOL, signals=("trivy",),
            install_requirements=(), attach_requirements=()))
        self.assertFalse(r.accepted)
        self.assertTrue(any("install/attach" in x for x in r.reasons))

    def test_tool_with_install_promotes(self) -> None:
        r = C.promote_candidate(_complete_candidate(
            id="trivy", name="Trivy", kind=KIND_TOOL, signals=("trivy",),
            install_requirements=("brew install trivy",)))
        self.assertTrue(r.accepted, r.reasons)
        self.assertTrue(r.spec.needs_attach)

    def test_mcp_needs_provider_affinity(self) -> None:
        r = C.promote_candidate(_complete_candidate(
            id="pw-mcp", name="Playwright MCP", kind=KIND_MCP, signals=("playwright mcp",),
            install_requirements=("npx @playwright/mcp",), provider_affinity=()))
        self.assertFalse(r.accepted)
        self.assertTrue(any("provider_affinity" in x for x in r.reasons))

    def test_mcp_with_affinity_promotes(self) -> None:
        r = C.promote_candidate(_complete_candidate(
            id="pw-mcp", name="Playwright MCP", kind=KIND_MCP, signals=("playwright mcp",),
            install_requirements=("npx @playwright/mcp",), attach_requirements=("mcp connect",),
            provider_affinity=("claude-code", "codex")))
        self.assertTrue(r.accepted, r.reasons)
        self.assertEqual(r.spec.provider_affinity, ("claude-code", "codex"))


class OverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog.clear_overlay()

    def tearDown(self) -> None:
        catalog.clear_overlay()

    def test_promoted_candidate_resolved(self) -> None:
        # before promotion: 'pulumi' stack is uncovered → shallow.
        self.assertEqual(resolve("Pulumi 로 스택 구성").selected_skills, ())
        r = C.promote_candidate(_complete_candidate())
        catalog.register_promoted(r.spec)
        self.assertIn("pulumi", (s.id for s in catalog.all_skills()))
        p = resolve("Pulumi 로 스택 구성")
        self.assertIn("pulumi", p.selected_skills)
        # evidence row exists for the promoted pick (no fake).
        self.assertTrue(any(e.target == "pulumi" and e.decision == "selected"
                            for e in p.selection_evidence))

    def test_clear_overlay_reverts(self) -> None:
        catalog.register_promoted(C.promote_candidate(_complete_candidate()).spec)
        catalog.clear_overlay()
        self.assertNotIn("pulumi", (s.id for s in catalog.all_skills()))
        self.assertEqual(resolve("Pulumi 로 스택 구성").selected_skills, ())

    def test_promotion_idempotent_by_id(self) -> None:
        spec = C.promote_candidate(_complete_candidate()).spec
        catalog.register_promoted(spec)
        catalog.register_promoted(spec)
        self.assertEqual(sum(1 for s in catalog.all_skills() if s.id == "pulumi"), 1)


class SchemaTests(unittest.TestCase):
    def test_github_actions_seeded_with_kind_and_attach(self) -> None:
        sk = catalog.skill("github-actions")
        self.assertIsNotNone(sk)
        self.assertEqual(sk.kind, KIND_SKILL)
        self.assertEqual(sk.provider_affinity, ("github",))
        self.assertIn("actionlint", sk.install_requirements)

    def test_every_skill_serialises_new_fields(self) -> None:
        for sk in catalog.all_skills():
            d = sk.to_dict()
            for key in ("kind", "provider_affinity", "install_requirements",
                        "attach_requirements", "when_to_use", "required_inputs"):
                self.assertIn(key, d, f"{sk.id} to_dict missing {key}")


class ContextAwareSelectionTests(unittest.TestCase):
    SCENARIO = "Terraform으로 ECS 배포 환경 구성해야 돼. Claude Code한테 맡길 프롬프트 만들어줘."
    FACTS = ("EKS는 제외", "dev 환경부터", "기존 구조 보존")

    def _scenario(self):
        return resolve(self.SCENARIO, project_facts=self.FACTS,
                       runtime_constraints=("production apply 금지",), harness="claude-code")

    def test_terraform_ecs_loadout_and_tools(self) -> None:
        p = self._scenario()
        self.assertEqual(p.selected_loadout, "devops-cloud-local")
        for s in ("terraform", "aws-ecs", "docker", "github-actions"):
            self.assertIn(s, p.selected_skills)
        for w in ("terraform", "docker", "gh"):
            self.assertIn(w, p.packet_draft.selected_tools)

    def test_eks_excluded_with_evidence(self) -> None:
        p = self._scenario()
        self.assertIn("kubernetes", p.excluded_skills)
        self.assertNotIn("kubernetes", p.selected_skills)
        self.assertTrue(any(e.target == "kubernetes" and e.decision == "excluded"
                            for e in p.selection_evidence))
        self.assertTrue(any("제외" in f for f in p.packet_draft.forbidden_scope))

    def test_constraints_and_harness_in_packet(self) -> None:
        p = self._scenario()
        self.assertEqual(p.packet_draft.harness, "claude-code")
        self.assertIn("dev 환경부터", p.packet_draft.constraints)
        self.assertIn("기존 구조 보존", p.packet_draft.constraints)
        self.assertIn("production apply 금지", p.packet_draft.constraints)

    def test_every_selected_skill_has_evidence(self) -> None:
        p = self._scenario()
        rows = {e.target for e in p.selection_evidence if e.decision == "selected" and e.kind == "skill"}
        self.assertEqual(set(p.selected_skills), rows)  # no pick without a reason

    def test_shallow_request_has_no_fake_evidence(self) -> None:
        p = resolve("Rust 임베디드 펌웨어 베어메탈")
        self.assertEqual(p.selected_skills, ())
        skill_ev = [e for e in p.selection_evidence if e.kind == "skill"]
        self.assertEqual(skill_ev, [])  # nothing fabricated
        self.assertIn("근거 없음", selection_evidence_lines(p)[0])

    def test_backward_compatible_without_context(self) -> None:
        # the legacy 2-arg call still works (forge bridge path) and is unaffected.
        p = resolve("Spring Boot + JWT + MySQL 기반 관리자 API")
        self.assertEqual(p.selected_loadout, "backend-java-local")
        self.assertEqual(p.excluded_skills, ())
        self.assertEqual(p.packet_draft.harness, "")


if __name__ == "__main__":
    unittest.main()
