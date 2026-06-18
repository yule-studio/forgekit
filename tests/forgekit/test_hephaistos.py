"""Hephaistos MVP core — resolve + verify + honest Nexus seam.

Proves the resolver deterministically forges an equip plan for the MVP java-spring
request (agent/skills/loadout/weapons/packet), that Nexus refs attach as not_connected
(never faked-read), that a stack with no armory coverage resolves honestly shallow, and
that loadout verify reflects the real (injected) environment.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.hephaistos import armory, models, resolver, verifier


class ResolveTests(unittest.TestCase):
    def test_spring_jwt_resolves_full_plan(self) -> None:
        p = resolver.resolve("Spring Boot JWT refresh token 구조 정리해줘")
        self.assertEqual((p.domain, p.language, p.framework, p.topic),
                         ("backend", "java", "spring-boot", "auth-jwt"))
        self.assertEqual(p.selected_agent, "backend-engineer")
        for sk in ("java-spring", "auth-jwt", "mysql"):
            self.assertIn(sk, p.selected_skills)
        self.assertEqual(p.selected_loadout, "backend-java-local")
        for w in ("openjdk", "gradle", "docker"):
            self.assertIn(w, p.required_weapons)

    def test_nexus_refs_attached_but_not_connected(self) -> None:
        p = resolver.resolve("Spring Boot JWT refresh token")
        self.assertTrue(p.nexus_refs)
        # honest: Nexus is NOT live-connected → every ref is not_connected, none faked read
        self.assertTrue(all(r.status == models.SRC_NOT_CONNECTED for r in p.nexus_refs))

    def test_packet_draft_is_structured(self) -> None:
        p = resolver.resolve("Spring Boot JWT refresh token")
        pk = p.packet_draft
        self.assertTrue(pk.goal and pk.scope and pk.forbidden_scope)
        self.assertTrue(pk.verification)            # verify commands present
        self.assertEqual(pk.approval_level, "L2_internal_approve")
        self.assertTrue(pk.nexus_refs)

    def test_uncovered_stack_resolves_shallow_honestly(self) -> None:
        # armory is the java-spring MVP — a frontend request has no skills (not faked)
        p = resolver.resolve("Next.js dashboard spacing 개선해줘")
        self.assertEqual(p.domain, "frontend")
        self.assertEqual(p.selected_skills, ())     # honest: no frontend skills yet
        self.assertEqual(p.selected_loadout, "")

    def test_deterministic(self) -> None:
        a = resolver.resolve("Spring Boot JWT").to_dict()
        b = resolver.resolve("Spring Boot JWT").to_dict()
        self.assertEqual(a, b)


class VerifyTests(unittest.TestCase):
    def test_ready_when_all_present(self) -> None:
        r = verifier.verify_loadout("backend-java-local", which=lambda b: "/usr/bin/" + b)
        self.assertEqual(r.status, verifier.READY)
        self.assertFalse(r.missing)

    def test_partial_when_some_missing(self) -> None:
        # gradle absent → partial, with an install next-step
        which = lambda b: None if b == "gradle" else "/usr/bin/" + b
        r = verifier.verify_loadout("backend-java-local", which=which)
        self.assertEqual(r.status, verifier.PARTIAL)
        self.assertIn("gradle", r.missing)
        self.assertTrue(r.next_steps)

    def test_missing_when_none(self) -> None:
        r = verifier.verify_loadout("backend-java-local", which=lambda b: None)
        self.assertEqual(r.status, verifier.MISSING)

    def test_unknown_loadout_blocked(self) -> None:
        self.assertEqual(verifier.verify_loadout("nope").status, verifier.BLOCKED)


class ArmoryQualityTests(unittest.TestCase):
    def test_skills_are_not_placeholders(self) -> None:
        # every skill must carry rules + at least one of commands/verification (실전형)
        for sk in armory.all_skills():
            self.assertTrue(sk.rules, f"{sk.id} has no rules")
            self.assertTrue(sk.commands or sk.verification, f"{sk.id} has no commands/verify")


if __name__ == "__main__":
    unittest.main()
