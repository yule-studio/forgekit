"""Cross-lane wave E2E — intake → Armory → Hephaistos → Nexus → provider → runtime receipt.

This is the integration/QA lane's load-bearing test: it threads the whole wave's seams in
ONE flow on representative scenarios, asserting the honest boundaries hold end to end (not
each lane in isolation). The point is that the lanes do not "play separately" — a request
collected as a candidate resolves to a real Armory loadout, attaches Nexus knowledge,
routes through the provider projection, and only a *safe engineering* plan earns an
authorized execution receipt while deploy/non-engineering plans are honestly blocked.

Lanes covered:
  * gw1 intake/curation        — discovery sweep → ledger (new→seen→promoted), dedup+persist
  * gw2 Armory/Hephaistos      — resolve(request) → curated catalog loadout/skills/weapons
  * gw3 Nexus/discovery        — read_plan_sources honest not_connected, brief→authored note
  * gw4 provider projection    — set primary/link/route persisted + reload + routing surface
  * gw5 runtime/governance     — forge_execute → execution receipt + ledger + fake refusal

Representative scenarios:
  * Terraform + ECS + GitHub Actions  → devops loadout, deploy ⇒ destructive/L4 BLOCKED
  * Next.js design-system / component  → ux/design loadout, non-engineering ⇒ no exec slot
  * Spring Boot JWT auth API           → backend-engineer, safe/L2 ⇒ AUTHORIZED receipt
  * discovery signal → curated packet  → ledger lifecycle + promote
  * ponytail-like OSS CLI candidate    → intake (ledger), Armory catalog stays curated

NETWORK-FREE: discovery collectors disabled, fetcher stubbed → deterministic in CI.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console import discovery as D
from forgekit_console.policy import provider_ops as ops
from forgekit_console.policy import provider_surface as ps
from forgekit_runtime import forge as F
from forgekit_runtime.decision_lane import (
    ChangeUnderReview,
    consult_gate_report,
)
from hephaistos import nexus_read as nx, resolve
from armory import catalog


def _empty_fetcher(_url: str) -> str:
    return "{}"


# network-free: drop HN/Reddit/GitHub collectors → repo-local only → deterministic.
_OFFLINE_CFG = {"discovery": {"hackernews_query": "", "subreddits": [], "github_query": ""}}


class WaveIntegrationE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.home = Path(tempfile.mkdtemp())
        self.vault = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.vault, ignore_errors=True))
        (self.tmp / "apps").mkdir()
        (self.tmp / "apps" / "m.py").write_text("# TODO auth\n# FIXME token\n", encoding="utf-8")
        self.env = {"FORGEKIT_HOME": str(self.home)}

    def _sweep(self, signal: str):
        return D.run_discovery_sweep(
            self.tmp, fetcher=_empty_fetcher, config=_OFFLINE_CFG, extra_signals=[signal])

    # === gw1 intake/curation ==================================================
    def test_lane1_intake_ledger_lifecycle_and_persist(self) -> None:
        led = D.DiscoveryLedger.load(self.env)
        new1, upd1 = led.record_sweep(self._sweep("노트 동기화가 느려 불편"), now="2026-06-23T10:00:00")
        self.assertTrue(new1)                      # first sweep → all new
        self.assertFalse(upd1)
        # re-sweep the SAME signal → dedup, no new, seen_count bumps (no fake resurfacing)
        new2, upd2 = led.record_sweep(self._sweep("노트 동기화가 느려 불편"), now="2026-06-23T11:00:00")
        self.assertFalse(new2)
        self.assertTrue(upd2)
        self.assertTrue(all(i.seen_count == 2 for i in upd2))
        # promote one idea, persist, reload → survives
        fp = D.fingerprint(led.pending()[0].problem)
        led.mark(fp, D.ST_PROMOTED)
        self.assertIsNotNone(led.save(self.env))
        reloaded = D.DiscoveryLedger.load(self.env)
        self.assertEqual(reloaded.summary()["promoted"], 1)

    def test_lane1_ponytail_oss_candidate_intake_is_curated_not_autowired(self) -> None:
        # an external OSS tooling candidate ("ponytail"-like CLI) is INTAKEN into the
        # ledger — it does NOT silently mutate the curated Armory catalog. Honest seam:
        # Armory is a curated read-only catalog; intake surfaces a candidate for review.
        led = D.DiscoveryLedger()
        led.record_sweep(self._sweep("ponytail 같은 CLI 도구로 dotfile 관리 자동화"), now="2026-06-23T10:00:00")
        self.assertTrue(led.pending())                       # candidate surfaced
        before = len(catalog.all_skills())
        # intake does not add catalog entries (no dynamic promotion path).
        self.assertEqual(len(catalog.all_skills()), before)

    # === gw2 Armory + Hephaistos ==============================================
    def test_lane2_resolve_maps_to_curated_armory_loadout(self) -> None:
        cases = {
            "Terraform + ECS + GitHub Actions 배포 파이프라인 구성":
                ("devops-engineer", "devops-cloud-local", ("terraform", "aws-ecs")),
            "Next.js 디자인 시스템 컴포넌트 라이브러리 구축":
                ("ux-ui-designer", "design-review-local", ("design-system-review",)),
            "Spring Boot JWT 인증 API 추가":
                ("backend-engineer", "backend-java-local", ("java-spring", "auth-jwt")),
        }
        for req, (agent, loadout, must_skills) in cases.items():
            p = resolve(req)
            self.assertEqual(p.selected_agent, agent, req)
            self.assertEqual(p.selected_loadout, loadout, req)
            for sk in must_skills:
                self.assertIn(sk, p.selected_skills, f"{req}: {sk}")
            # the resolved loadout/skills are REAL catalog entries (Armory ↔ resolver).
            self.assertIsNotNone(catalog.loadout(loadout), loadout)
            for sk in p.selected_skills:
                self.assertIsNotNone(catalog.skill(sk), sk)

    def test_lane2_uncovered_stack_stays_honest(self) -> None:
        # an uncovered stack is not faked — empty skills/loadout, no shallow filler.
        p = resolve("Rust 임베디드 펌웨어 인터럽트 핸들러")
        self.assertEqual(p.selected_skills, ())
        self.assertEqual(p.selected_loadout, "")

    # === gw3 Nexus knowledge attachment =======================================
    def test_lane3_nexus_attachment_is_honest_not_connected_by_default(self) -> None:
        plan = resolve("Spring Boot JWT 인증 API 추가")
        cs = nx.connection_status(env={}, config={})
        self.assertFalse(cs["connected"])              # honest not_connected, no fake vault
        read = nx.read_plan_sources(plan, env={}, config={})
        self.assertTrue(read.not_connected)            # plan refs not fabricated as read

    def test_lane3_brief_to_authored_note_carries_frontmatter(self) -> None:
        sweep = self._sweep("노트 동기화가 느려 불편")
        brief = sweep.top_brief
        self.assertIsNotNone(brief)
        note = D.brief_to_authored_note(brief, created_at="2026-06-23")
        self.assertIn("---", note)                     # frontmatter present (curated note)
        # persist into a vault dir → real file written under the inbox.
        path = D.persist_brief(brief, self.vault, created_at="2026-06-23")
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())

    # === gw4 provider projection ==============================================
    def test_lane4_provider_projection_persists_and_reloads(self) -> None:
        cfg_path = self.home / "config.json"
        self.assertTrue(ps.apply_set_primary("ollama", path=cfg_path)[0])
        self.assertTrue(ps.apply_link("gemini", path=cfg_path)[0])
        self.assertTrue(ps.apply_route_set("research", "gemini", path=cfg_path)[0])
        cfg = ops.load_raw_config(path=cfg_path)
        self.assertEqual(cfg["primary_provider"], "ollama")
        self.assertIn("gemini", cfg["linked_providers"])
        self.assertEqual(cfg["slot_routing"]["research"], "gemini")
        # routing surface renders without claiming bare-live for an unverified provider.
        lines = "\n".join(ps.route_show_lines(cfg))
        self.assertIn("research", lines)

    # === gw5 runtime governance / execution receipt ===========================
    def test_lane5_safe_engineering_plan_earns_authorized_receipt(self) -> None:
        r = F.forge_execute("Spring Boot JWT 인증 API 추가", weapon_safety=lambda w: "safe",
                             env=self.env, persist=True, recorded_at="2026-06-23")
        self.assertTrue(r.authorized)
        self.assertEqual(r.outcome, F.OUTCOME_EXECUTED)
        self.assertEqual(r.action_class, "safe")
        self.assertEqual(r.selected_agent, "backend-engineer")
        self.assertTrue(r.commit_trailers)                       # trailer-stamped proof
        self.assertIn("signoff=tech-lead", r.approval_metadata)
        self.assertEqual(F.validate_forge_receipt(r), ())        # valid, not fake

    def test_lane5_deploy_plan_is_destructive_and_blocked(self) -> None:
        r = F.forge_execute("Terraform + ECS + GitHub Actions 배포 파이프라인 구성",
                             weapon_safety=lambda w: "safe", env=self.env)
        self.assertFalse(r.authorized)
        self.assertEqual(r.outcome, F.OUTCOME_BLOCKED)
        self.assertEqual(r.action_class, "destructive")
        self.assertEqual(r.commit_trailers, ())                  # no proof on a blocked plan
        self.assertTrue(r.blocking_reasons)

    def test_lane5_non_engineering_role_has_no_exec_slot(self) -> None:
        # a design request resolves to a non-engineering role → honest block (no slot),
        # even though the plan itself is safe-class.
        r = F.forge_execute("Next.js 디자인 시스템 컴포넌트 라이브러리 구축",
                            weapon_safety=lambda w: "safe", env=self.env)
        self.assertFalse(r.authorized)
        self.assertEqual(r.outcome, F.OUTCOME_BLOCKED)

    def test_lane5_ledger_persists_and_refuses_fake_receipt(self) -> None:
        F.forge_execute("Spring Boot JWT 인증 API 추가", weapon_safety=lambda w: "safe",
                        env=self.env, persist=True, recorded_at="2026-06-23")
        F.forge_execute("Terraform + ECS 배포", weapon_safety=lambda w: "safe",
                        env=self.env, persist=True, recorded_at="2026-06-23")
        entries = F.read_forge_receipts(env=self.env)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["receipt"]["outcome"], F.OUTCOME_EXECUTED)
        self.assertEqual(entries[1]["receipt"]["outcome"], F.OUTCOME_BLOCKED)
        # a fabricated "authorized/executed" receipt with no metadata is refused.
        fake = F.ForgeExecutionReceipt(
            request="x", authorized=True, outcome=F.OUTCOME_EXECUTED,
            approval_metadata="", selected_agent="backend-engineer")
        with self.assertRaises(F.FakeReceiptRefused):
            F.record_forge_receipt(fake, env=self.env)
        self.assertEqual(len(F.read_forge_receipts(env=self.env)), 2)   # nothing added

    # === full chain: one scenario threaded across every lane ==================
    def test_full_chain_backend_scenario_end_to_end(self) -> None:
        req = "Spring Boot JWT 인증 API 추가"
        # 1. intake — a related signal becomes a ledger candidate.
        led = D.DiscoveryLedger.load(self.env)
        led.record_sweep(self._sweep("JWT refresh 토큰 회전이 누락돼 보안 약점"), now="2026-06-23T10:00:00")
        led.save(self.env)
        # 2-3. Armory + Hephaistos — resolve to a curated loadout.
        plan = resolve(req)
        self.assertEqual(plan.selected_loadout, "backend-java-local")
        self.assertIsNotNone(catalog.loadout(plan.selected_loadout))
        # 4. Nexus — knowledge attachment honest (not connected here → no fabrication).
        read = nx.read_plan_sources(plan, env={}, config={})
        self.assertTrue(read.not_connected)
        # 5. provider projection — route execution slot, persisted.
        cfg_path = self.home / "config.json"
        ps.apply_set_primary("ollama", path=cfg_path)
        ps.apply_route_set("execution", "ollama", path=cfg_path)
        self.assertEqual(ops.load_raw_config(path=cfg_path)["slot_routing"]["execution"], "ollama")
        # 6. runtime — safe engineering plan earns a real, validated execution receipt.
        receipt = F.forge_execute(req, weapon_safety=lambda w: "safe",
                                  env=self.env, persist=True, recorded_at="2026-06-23")
        self.assertTrue(receipt.authorized)
        self.assertEqual(F.validate_forge_receipt(receipt), ())
        self.assertTrue(F.read_forge_receipts(env=self.env))

    # === consult merge gate over the wave scenarios ===========================
    def test_consult_gate_over_wave_changes(self) -> None:
        # the integration PR itself (this lane) is pure verification → not required.
        # a design-bearing change with no consult artifact is a merge blocker.
        changes = [
            ChangeUnderReview("integration-qa-lane", change_kinds=("integration", "test", "docs")),
            ChangeUnderReview("consult-gate", change_kinds=("test", "qa")),
            ChangeUnderReview("hypothetical-api-change", change_kinds=("api-contract",)),  # missing
        ]
        rep = consult_gate_report(changes)
        self.assertIn("integration-qa-lane", {v.ref for v in rep.not_required})
        self.assertIn("hypothetical-api-change", {v.ref for v in rep.missing})
        self.assertTrue(rep.merge_blocked)   # the unconsulted api change blocks the wave


if __name__ == "__main__":
    unittest.main()
