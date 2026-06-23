"""Provider projection lane — vendor-neutral tool candidate → provider ecosystem routing.

Pins the lane wired in issue #406:
  * the deterministic rules engine (forgekit_provider.projection) routes a ToolCandidate to
    Claude/Codex/Gemini projection targets OR the Ollama backend slot — NEVER mixing the two;
  * MCP projects to MCP-capable harnesses only (Ollama excluded); hooks never reach Gemini;
  * backend/local-inference capabilities resolve to the Ollama slot with no projection targets;
  * the connect/attach surface renders honest attach/connect/verify (no fake connector);
  * the console bridge maps real armory skills/weapons to verdicts and the /resolve packet.
"""

from __future__ import annotations

import unittest

from tests.forgekit import _SRC  # noqa: F401  (puts app + package srcs on path)

from forgekit_provider.projection import models as pm
from forgekit_provider.projection.rules import project


def _cand(**kw):
    base = dict(id="x", name="X", taxonomy_kind=pm.KIND_SKILL, capability_class=pm.CAP_EXECUTION)
    base.update(kw)
    return pm.ToolCandidate(**base)


class RulesEngineTests(unittest.TestCase):
    def test_execution_skill_projects_to_codex_primary(self):
        v = project(_cand(capability_class=pm.CAP_EXECUTION))
        self.assertEqual(v.primary_target, pm.TARGET_CODEX)
        self.assertIn(pm.TARGET_CODEX, v.projection_targets)
        self.assertFalse(v.is_backend)
        self.assertEqual(v.backend_role, "")

    def test_research_skill_projects_to_gemini(self):
        v = project(_cand(capability_class=pm.CAP_RESEARCH))
        self.assertEqual(v.primary_target, pm.TARGET_GEMINI)

    def test_security_gate_projects_to_claude(self):
        v = project(_cand(capability_class=pm.CAP_SECURITY_GATE))
        self.assertEqual(v.primary_target, pm.TARGET_CLAUDE)

    def test_backend_capability_routes_to_ollama_not_a_projection(self):
        for cap in pm.BACKEND_CAPABILITIES:
            v = project(_cand(capability_class=cap))
            self.assertTrue(v.is_backend, cap)
            self.assertEqual(v.backend_role, pm.BACKEND_OLLAMA, cap)
            # the hard rail: a backend slot is NEVER a projection target.
            self.assertEqual(v.projection_targets, (), cap)
            self.assertEqual(v.primary_target, "", cap)

    def test_backend_kind_routes_to_ollama(self):
        v = project(_cand(taxonomy_kind=pm.KIND_BACKEND, capability_class=pm.CAP_EXECUTION))
        self.assertTrue(v.is_backend)
        self.assertEqual(v.backend_role, pm.BACKEND_OLLAMA)

    def test_ollama_never_appears_as_a_projection_target(self):
        # exhaustive: across every (kind, capability) pair, ollama is never in projection_targets.
        for kind in pm.TAXONOMY_KINDS:
            for cap in pm.CAPABILITY_CLASSES:
                v = project(_cand(taxonomy_kind=kind, capability_class=cap))
                self.assertNotIn(pm.BACKEND_OLLAMA, v.projection_targets,
                                 f"{kind}/{cap} leaked ollama into projection_targets")
                # and projection_targets are always a subset of the 3 real targets.
                for t in v.projection_targets:
                    self.assertIn(t, pm.PROJECTION_TARGETS, f"{kind}/{cap} → {t}")

    def test_mcp_projects_to_all_capable_harnesses_excluding_ollama(self):
        v = project(_cand(taxonomy_kind=pm.KIND_MCP, capability_class=pm.CAP_TOOL_USE))
        self.assertEqual(set(v.projection_targets), set(pm.PROJECTION_TARGETS))
        self.assertFalse(v.is_backend)
        self.assertNotIn(pm.BACKEND_OLLAMA, v.projection_targets)

    def test_hook_never_projects_to_gemini(self):
        v = project(_cand(taxonomy_kind=pm.KIND_HOOK, capability_class=pm.CAP_SECURITY_GATE))
        self.assertNotIn(pm.TARGET_GEMINI, v.projection_targets)
        self.assertIn(pm.TARGET_CLAUDE, v.projection_targets)

    def test_runtime_plugin_is_vendor_neutral_no_single_primary(self):
        v = project(_cand(taxonomy_kind=pm.KIND_RUNTIME_PLUGIN, capability_class=pm.CAP_EXPLORATION))
        self.assertTrue(v.is_neutral_runtime)
        self.assertEqual(v.primary_target, "")
        self.assertEqual(v.backend_role, "")

    def test_every_plan_has_attach_connect_verify(self):
        v = project(_cand(taxonomy_kind=pm.KIND_SKILL, capability_class=pm.CAP_EXECUTION))
        self.assertTrue(v.plans)
        for p in v.plans:
            self.assertTrue(p.attach and p.connect and p.verify)

    def test_honest_no_connector_flag_for_codex_hook(self):
        # codex hook projection has no generated connector yet → flagged honestly.
        v = project(_cand(taxonomy_kind=pm.KIND_HOOK, capability_class=pm.CAP_VERIFICATION))
        codex_plan = v.plan_for(pm.TARGET_CODEX)
        if codex_plan is not None:
            self.assertFalse(codex_plan.has_connector)

    def test_verify_command_carried_into_verify_condition(self):
        v = project(_cand(verify_command="java -version"))
        self.assertTrue(any("java -version" in p.verify for p in v.plans))

    def test_verdict_serialisable(self):
        d = project(_cand()).to_dict()
        self.assertIn("projection_targets", d)
        self.assertIn("backend_role", d)
        self.assertIn("is_backend", d)


class ConnectAttachSurfaceTests(unittest.TestCase):
    def test_attach_lines_label_projection_vs_backend(self):
        from forgekit_provider_connect import attach

        proj_lines = "\n".join(attach.attach_lines(_cand(capability_class=pm.CAP_EXECUTION)))
        self.assertIn("primary projection", proj_lines)

        back_lines = "\n".join(attach.attach_lines(_cand(capability_class=pm.CAP_SUMMARIZATION)))
        self.assertIn("backend slot", back_lines)
        self.assertNotIn("primary projection", back_lines)

    def test_project_candidate_entry(self):
        from forgekit_provider_connect import attach

        v = attach.project_candidate(id="figma", name="Figma", kind=pm.KIND_MCP,
                                     capability=pm.CAP_TOOL_USE)
        self.assertEqual(set(v.projection_targets), set(pm.PROJECTION_TARGETS))

    def test_setup_surface_carries_projection_legend(self):
        from forgekit_provider_connect import attach, surface

        class _P:
            def cli_authenticated(self, p): return True
            def api_key(self, p, env=None): return ""
            def daemon_reachable(self, e): return False
            def installed_models(self, e): return ()

        text = "\n".join(surface.setup_status_lines({}, probe=_P()))
        self.assertIn("backend slot", text)
        self.assertIn(attach.PROJECTION_LEGEND.split(".")[0][:10], text)


class ConsoleBridgeTests(unittest.TestCase):
    def test_real_armory_skill_maps_to_a_verdict(self):
        from forgekit_console import provider_projection as pp
        from armory import catalog

        spec = catalog.skill("java-spring")
        self.assertIsNotNone(spec)
        cand = pp.candidate_from_skill(spec)
        v = project(cand)
        # "tool-using executor" → execution → codex projection (not a backend).
        self.assertEqual(v.primary_target, pm.TARGET_CODEX)
        self.assertFalse(v.is_backend)

    def test_figma_skill_maps_to_mcp(self):
        from forgekit_console import provider_projection as pp
        from armory import catalog

        cand = pp.candidate_from_skill(catalog.skill("figma-read"))
        self.assertEqual(cand.taxonomy_kind, pm.KIND_MCP)

    def test_weapon_is_executor_environment_not_ollama_backend(self):
        from forgekit_console import provider_projection as pp
        from armory import catalog

        cand = pp.candidate_from_weapon(catalog.weapon("docker"))
        v = project(cand)
        # a CLI weapon is wielded on the executor (Codex) plane, NOT routed to ollama.
        self.assertFalse(v.is_backend)
        self.assertEqual(v.primary_target, pm.TARGET_CODEX)
        self.assertTrue(any("docker --version" in p.verify for p in v.plans))

    def test_packet_projection_lines_for_resolved_plan(self):
        from forgekit_console import provider_projection as pp
        from hephaistos import resolver

        plan = resolver.resolve("Spring Boot JWT refresh token")
        lines = pp.packet_projection_lines(plan)
        self.assertTrue(lines)
        text = "\n".join(lines)
        self.assertIn("provider projection", text)
        # every selected skill should produce a target marker.
        self.assertTrue(any("→" in ln or "backend:" in ln for ln in lines))

    def test_packet_projection_empty_when_no_tools(self):
        from forgekit_console import provider_projection as pp

        class _Empty:
            selected_skills = ()
            required_weapons = ()

        self.assertEqual(pp.packet_projection_lines(_Empty()), ())

    def test_attach_detail_unknown_id_is_honest(self):
        from forgekit_console import provider_projection as pp

        lines = pp.attach_detail_lines("does-not-exist")
        self.assertIn("armory skill/weapon 이 아님", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
