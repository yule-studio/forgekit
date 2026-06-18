"""Armory breadth (Hephaistos PR3) — manifest/loadout validity + resolver realism.

Locks the 6 representative scenarios to real selections, enforces referential integrity
(loadout skills/weapons exist), forbids placeholder manifests, and proves the language
gate (FastAPI → python, not java). Uncovered stacks stay honestly shallow.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.hephaistos import armory, resolver


class ManifestValidationTests(unittest.TestCase):
    def test_skills_have_real_contract(self) -> None:
        for sk in armory.all_skills():
            self.assertTrue(sk.category, f"{sk.id} no category")
            self.assertTrue(sk.summary, f"{sk.id} no summary")
            self.assertTrue(sk.when_to_use, f"{sk.id} no when_to_use")
            self.assertTrue(sk.commands or sk.verification, f"{sk.id} no commands/verify")
            self.assertTrue(sk.signals, f"{sk.id} no signals")
            self.assertTrue(sk.capability_note, f"{sk.id} no capability_note")
            # provider-neutral: capability_note must not name a vendor
            for vendor in ("claude", "codex", "gemini", "gpt-4", "openai gpt"):
                self.assertNotIn(vendor, sk.capability_note.lower(), f"{sk.id} vendor-locked")

    def test_loadout_referential_integrity(self) -> None:
        skill_ids = {s.id for s in armory.all_skills()}
        weapon_ids = {w.id for w in armory.all_weapons()}
        for lo in armory.all_loadouts():
            self.assertTrue(lo.goal, f"{lo.id} no goal")
            self.assertTrue(lo.recommended_skills, f"{lo.id} no recommended skills")
            for s in (*lo.recommended_skills, *lo.optional_skills, *lo.blocked_skills):
                self.assertIn(s, skill_ids, f"{lo.id} → unknown skill {s}")
            for w in (*lo.required_weapons, *lo.optional_weapons):
                self.assertIn(w, weapon_ids, f"{lo.id} → unknown weapon {w}")

    def test_breadth_coverage(self) -> None:
        self.assertGreaterEqual(len(armory.all_skills()), 20)
        self.assertGreaterEqual(len(armory.all_loadouts()), 8)
        self.assertGreaterEqual(len(armory.categories()), 7)


class ResolverBreadthTests(unittest.TestCase):
    def _r(self, req):
        return resolver.resolve(req)

    def test_spring_jwt_mysql(self) -> None:
        p = self._r("Spring Boot + JWT + MySQL 기반 관리자 API")
        self.assertEqual(p.selected_agent, "backend-engineer")
        self.assertEqual(p.selected_loadout, "backend-java-local")
        for s in ("java-spring", "auth-jwt", "mysql"):
            self.assertIn(s, p.selected_skills)

    def test_fastapi_redis_language_gated(self) -> None:
        p = self._r("FastAPI + Redis + background worker")
        self.assertEqual(p.selected_loadout, "backend-python-local")
        self.assertIn("python-fastapi", p.selected_skills)
        self.assertIn("redis", p.selected_skills)
        self.assertNotIn("java-spring", p.selected_skills)   # language gate — no java contamination

    def test_nextjs_frontend(self) -> None:
        p = self._r("Next.js 프론트 UI 간격/레이아웃 개선")
        self.assertEqual(p.selected_agent, "frontend-engineer")
        self.assertEqual(p.selected_loadout, "frontend-react-local")
        self.assertIn("nextjs", p.selected_skills)

    def test_terraform_ecs(self) -> None:
        p = self._r("Terraform + ECS 배포 가이드")
        self.assertEqual(p.selected_loadout, "devops-cloud-local")
        self.assertIn("terraform", p.selected_skills)
        self.assertIn("aws-ecs", p.selected_skills)

    def test_web_security_review(self) -> None:
        p = self._r("웹 보안 점검 / auth review")
        self.assertEqual(p.selected_loadout, "security-review-local")
        self.assertIn("web-security-review", p.selected_skills)

    def test_figma_design_system(self) -> None:
        p = self._r("Figma 참고 기반 디자인 시스템 점검")
        self.assertEqual(p.selected_loadout, "design-review-local")
        self.assertIn("design-system-review", p.selected_skills)

    def test_uncovered_is_shallow(self) -> None:
        p = self._r("Rust 임베디드 펌웨어")
        self.assertEqual(p.selected_skills, ())              # honest not_covered

    def test_packet_has_why_and_unsafe(self) -> None:
        p = self._r("Spring Boot JWT")
        self.assertTrue(p.packet_draft.scope)                # why/rules non-empty
        self.assertTrue(p.packet_draft.forbidden_scope)      # unsafe boundary non-empty


if __name__ == "__main__":
    unittest.main()
