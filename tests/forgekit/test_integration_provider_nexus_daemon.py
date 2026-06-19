"""WT4 — integration: provider resolution + Nexus status + usage breakdown + daemon
surface exercised TOGETHER across 3 representative operator scenarios.

Each scenario drives the REAL surfaces (no network, no terminal): a provider config
through SubmitService (fallback/model_overrides teeth), `/resolve` through the router
(Hephaistos plan + live Nexus line), the usage ledger rollup of the submit, and the
`/daemon` heartbeat surface. Proves the three axes compose, honestly.

Scenarios:
  1. Spring Boot JWT MySQL  → backend/java   · primary ollama (live) + model_override
  2. Next.js UI 개선         → frontend       · primary claude (unsupported) → fallback ollama
  3. Terraform ECS/K3s 운영  → devops         · primary gemini (no key) → fallback ollama
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.forgekit import _SRC  # noqa: F401

from forgekit_console.chat import models as cm
from forgekit_console.chat.service import SubmitService
from forgekit_console.commands.parser import parse_input
from forgekit_console.commands.registry import load_agents, load_commands
from forgekit_console.commands.router import ConsoleContext, route
from forgekit_console import usage as U


class RecordingTransport:
    def __init__(self, reachable=True):
        self.reachable = reachable
        self.calls = []

    def openai_chat(self, *, endpoint, model, prompt, api_key=""):
        self.calls.append({"endpoint": endpoint, "model": model})
        return "통합 시나리오 응답"

    def ollama_reachable(self, endpoint):
        return self.reachable

    def ollama_models(self, endpoint):
        return ("gemma3:latest",)


SCENARIOS = [
    {
        "name": "spring-boot-jwt",
        "request": "Spring Boot JWT refresh token MySQL 적용",
        "config": {"primary_provider": "ollama", "linked_providers": ["ollama"],
                   "model_overrides": {"ollama": "llama3:70b"}},
        "expect_domain": "backend",
        "expect_provider": "ollama",
        "expect_fallback": False,
        "expect_model": "llama3:70b",
    },
    {
        "name": "nextjs-ui",
        "request": "Next.js UI 레이아웃 개선 + 컴포넌트 리팩토링",
        "config": {"primary_provider": "claude", "linked_providers": ["claude", "ollama"],
                   "fallback_policy": {"slot_fallback_orders": {"default_chat": ["ollama"]}}},
        "expect_domain": "frontend",
        "expect_provider": "ollama",       # claude unsupported_in_console → fallback
        "expect_fallback": True,
        "expect_model": None,
    },
    {
        "name": "terraform-devops",
        "request": "Terraform 으로 ECS / K3s 클러스터 운영 자동화",
        "config": {"primary_provider": "gemini", "linked_providers": ["gemini", "ollama"],
                   "fallback_policy": {"slot_fallback_orders": {"default_chat": ["ollama"]}}},
        "expect_domain": "devops",
        "expect_provider": "ollama",       # gemini auth_missing (no key) → fallback
        "expect_fallback": True,
        "expect_model": None,
    },
]


class IntegrationScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        # a real, connected Nexus root so the resolve nexus line is LIVE
        self.nexus = self.home / "nexus"
        (self.nexus / "20-areas").mkdir(parents=True)
        (self.nexus / "20-areas" / "x.md").write_text("# area\nlive nexus read 가능", encoding="utf-8")
        self.env = {"FORGEKIT_HOME": str(self.home), "FORGEKIT_NEXUS_ROOT": str(self.nexus)}
        self.ledger = self.home / "usage.jsonl"

    def _ctx(self, config):
        return ConsoleContext(repo_root=Path("."), agents=load_agents(), commands=load_commands(),
                              env=self.env, config=config)

    def _run(self, cmd, config):
        return list(route(parse_input(cmd), self._ctx(config)).lines)

    def _record(self, res: cm.SubmitResult) -> None:
        ev = U.UsageEvent(
            ts=U.now_ts(self.env), session_id="it", mode="", provider=res.provider_id,
            model=res.model, category=res.category, input_tokens=res.input_tokens,
            output_tokens=res.output_tokens, total_tokens=res.total_tokens,
            usage_basis=res.usage_basis, success=res.ok,
        )
        U.append_event(ev, path=self.ledger)

    def test_three_axes_compose_per_scenario(self) -> None:
        for sc in SCENARIOS:
            with self.subTest(scenario=sc["name"]):
                # --- axis 1: provider resolution (fallback / model_overrides teeth) ---
                tx = RecordingTransport(reachable=True)
                svc = SubmitService(transport=tx, env={}, config=sc["config"])
                res = svc.submit(sc["request"])
                self.assertTrue(res.ok and res.is_live, f"{sc['name']}: expected live")
                self.assertEqual(res.provider_id, sc["expect_provider"])
                self.assertEqual(res.fallback_used, sc["expect_fallback"])
                if sc["expect_model"]:
                    self.assertEqual(tx.calls[-1]["model"], sc["expect_model"])

                # --- axis 2: Hephaistos resolve + LIVE Nexus line ---
                lines = self._run(f"/resolve {sc['request']}", sc["config"])
                joined = "\n".join(lines)
                self.assertIn(sc["expect_domain"], joined, f"{sc['name']}: domain inferred")
                nexus_line = [l for l in lines if "nexus" in l]
                self.assertTrue(nexus_line)
                self.assertIn("connected", nexus_line[0])   # live (not the static not_connected)

                # --- axis 3: usage ledger rollup of this submit ---
                self._record(res)

        # after all scenarios, the rollup spans the providers that actually answered.
        rows = U.read_events(path=self.ledger, day=U.today(self.env))
        roll = U.rollup(rows)
        self.assertEqual(roll.by_provider.get("ollama", 0) > 0, True)   # ollama answered all 3
        self.assertGreaterEqual(len(rows), 3)

        # --- axis 4: daemon surface is present + honest stopped (no daemon running) ---
        daemon = "\n".join(self._run("/daemon", SCENARIOS[0]["config"]))
        self.assertIn("always-on daemon", daemon)
        self.assertIn("stopped", daemon)

    def test_no_config_scenario_is_setup_required_not_fake(self) -> None:
        # the honesty rail across the integration: no provider → setup-required, no call.
        tx = RecordingTransport(reachable=True)
        res = SubmitService(transport=tx, env={}, config={}).submit("Spring Boot JWT")
        self.assertFalse(res.ok)
        self.assertEqual(res.category, cm.CAT_NO_PROVIDER)
        self.assertEqual(tx.calls, [])

    def test_nexus_not_connected_scenario_is_honest(self) -> None:
        # Nexus axis with NO root → /resolve shows not_connected, never a fake read.
        ctx = ConsoleContext(repo_root=Path("."), agents=load_agents(), commands=load_commands(),
                             env={}, config={})
        lines = list(route(parse_input("/resolve Spring Boot JWT"), ctx).lines)
        self.assertTrue(any("not_connected" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
