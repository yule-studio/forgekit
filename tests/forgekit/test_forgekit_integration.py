"""Integration pass (WT4) — provider config → resolve → nexus status → usage breakdown.

Threads WT1 (nexus live status) + WT2 (provider link/route) + WT3 (per-provider usage)
into one operator flow, on representative scenarios, asserting honest seams hold end to
end (not_connected nexus, language-gated resolve, per-provider live/estimate usage).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.hephaistos import nexus_read as nx, resolver
from forgekit_console.policy import provider_ops as ops
from forgekit_console.policy import provider_surface as ps
from forgekit_console.usage import breakdown


class IntegrationFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.cfg = self.tmp / "config.json"

    def test_provider_config_flow_persists(self) -> None:
        # set primary → link → route, each persisted (WT2)
        self.assertTrue(ps.apply_set_primary("ollama", path=self.cfg)[0])
        self.assertTrue(ps.apply_link("gemini", path=self.cfg)[0])
        self.assertTrue(ps.apply_route_set("research", "gemini", path=self.cfg)[0])
        cfg = ops.load_raw_config(path=self.cfg)
        self.assertEqual(cfg["primary_provider"], "ollama")
        self.assertIn("gemini", cfg["linked_providers"])
        self.assertEqual(cfg["slot_routing"]["research"], "gemini")

    def test_resolve_then_nexus_status(self) -> None:
        # WT-hephaistos resolve + WT1 nexus status (not_connected by default — honest)
        plan = resolver.resolve("Spring Boot + JWT + MySQL 관리자 API")
        self.assertEqual(plan.selected_loadout, "backend-java-local")
        cs = nx.connection_status(env={}, config={})
        self.assertFalse(cs["connected"])                 # honest not_connected, no fake
        read = nx.read_plan_sources(plan, env={}, config={})
        self.assertTrue(read.not_connected)

    def test_representative_scenarios(self) -> None:
        cases = {
            "Spring Boot + JWT + MySQL 관리자 API": ("backend-engineer", "backend-java-local"),
            "Next.js 대시보드 레이아웃 개선": ("frontend-engineer", "frontend-react-local"),
            "Terraform + ECS 배포 가이드": ("devops-engineer", "devops-cloud-local"),
        }
        for req, (agent, loadout) in cases.items():
            p = resolver.resolve(req)
            self.assertEqual(p.selected_agent, agent, req)
            self.assertEqual(p.selected_loadout, loadout, req)
            self.assertTrue(p.selected_skills, req)        # not shallow for covered stacks

    def test_usage_breakdown_per_provider(self) -> None:
        rows = [{"provider": "ollama", "model": "m", "mode": "interactive",
                 "total_tokens": 30, "input_tokens": 10, "output_tokens": 20, "usage_basis": "live"},
                {"provider": "gemini", "model": "g", "mode": "research",
                 "total_tokens": 50, "input_tokens": 20, "output_tokens": 30, "usage_basis": "estimate"}]
        by = {k.key: k for k in breakdown.breakdown_by(rows, "provider")}
        self.assertEqual(by["ollama"].live_tokens, 30)
        self.assertEqual(by["gemini"].estimate_tokens, 50)  # live/estimate per-provider, separate


if __name__ == "__main__":
    unittest.main()
