"""Toolchain version-switching guard — repo-local detection + loadout profile +
mise switch/verify/drift with approval gating. Pure / CI-safe.

Proves the control-plane toolchain layer:
- detects repo-local version pins from real manifest formats (no guessing);
- turns a Hephaistos loadout into a concrete toolchain profile;
- verify/drift compare against the manager's ACTUAL active versions;
- switch is approval-gated (global/install/destructive need explicit approve) and
  NEVER fakes — with no manager it refuses honestly;
- the console routes /toolchain detect|recommend|verify|drift|switch.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from tests.forgekit import _SRC  # noqa: F401

from forgekit_toolchain import detect, profile, plan, surface
from forgekit_toolchain import models as m


def reader(files: Dict[str, str]):
    return lambda rel: files.get(rel)


class FakeManager:
    """Deterministic mise stand-in — records run() argv, no real IO."""

    name = "mise"

    def __init__(self, *, present=True, active: Dict[str, str] = None, fail=()):
        self._present, self._active, self._fail = present, dict(active or {}), set(fail)
        self.ran: List[Tuple[str, ...]] = []

    def available(self) -> bool:
        return self._present

    def current(self) -> Dict[str, str]:
        return dict(self._active)

    def run(self, argv: Sequence[str]) -> Tuple[int, str]:
        self.ran.append(tuple(argv))
        return (1, "boom") if tuple(argv) in self._fail else (0, "ok")


class DetectTests(unittest.TestCase):
    def test_tool_versions_and_dedup_precedence(self) -> None:
        reqs = detect.detect_requirements(".", reader=reader({
            ".tool-versions": "node 20.11.0\npython 3.13.1  # comment\n\nruby 3.3.0",
            ".nvmrc": "18.0.0",  # lower precedence → must NOT override .tool-versions node
        }))
        got = {r.tool: r.version for r in reqs}
        self.assertEqual(got["node"], "20.11.0")        # .tool-versions wins over .nvmrc
        self.assertEqual(got["python"], "3.13.1")
        self.assertEqual(got["ruby"], "3.3.0")

    def test_mise_toml_tools_section(self) -> None:
        reqs = detect.detect_requirements(".", reader=reader({
            ".mise.toml": '[tools]\nnode = "20"\npython = ["3.13"]\ngo = { version = "1.22" }\n[env]\nx="y"',
        }))
        got = {r.tool: r.version for r in reqs}
        self.assertEqual(got, {"node": "20", "python": "3.13", "go": "1.22"})

    def test_single_file_and_derived_sources(self) -> None:
        node = detect.detect_requirements(".", reader=reader({".nvmrc": "v20.11.0"}))
        self.assertEqual(node[0].tool, "node")
        self.assertEqual(node[0].version, "20.11.0")    # leading v stripped
        gomod = detect.detect_requirements(".", reader=reader({"go.mod": "module x\n\ngo 1.22\n"}))
        self.assertEqual((gomod[0].tool, gomod[0].version), ("go", "1.22"))
        pkg = detect.detect_requirements(".", reader=reader({
            "package.json": '{"engines": {"node": ">=20", "pnpm": "9"}}'}))
        self.assertEqual({r.tool: r.version for r in pkg}, {"node": ">=20", "pnpm": "9"})

    def test_no_manifests_is_empty_not_invented(self) -> None:
        self.assertEqual(detect.detect_requirements(".", reader=reader({})), [])


class ProfileTests(unittest.TestCase):
    def test_loadout_to_profile_extracts_versions(self) -> None:
        java = profile.profile_for_loadout("backend-java-local")
        self.assertEqual(java.tool("java").version, "21")           # from "local JDK 21"
        py = profile.profile_for_loadout("backend-python-local")
        self.assertEqual(py.tool("python").version, "3.13")
        react = profile.profile_for_loadout("frontend-react-local")
        self.assertEqual(react.tool("node").version, "lts")         # alias kept honest

    def test_infra_weapons_are_not_toolchain_runtimes(self) -> None:
        java = profile.profile_for_loadout("backend-java-local")
        self.assertIsNone(java.tool("docker"))                      # docker is not a runtime
        self.assertIsNone(java.tool("mysql"))

    def test_unknown_loadout_is_none(self) -> None:
        self.assertIsNone(profile.profile_for_loadout("does-not-exist"))

    def test_merge_repo_wins_loadout_fills(self) -> None:
        det = profile.profile_from_requirements("repo", [m.ToolRequirement("node", "20", m.SRC_NVMRC)])
        lo = profile.profile_for_loadout("frontend-react-local")
        merged = profile.merge_profiles(det, lo)
        self.assertEqual(merged.tool("node").version, "20")         # repo pin wins over loadout lts


class VerifyTests(unittest.TestCase):
    def _profile(self):
        return profile.profile_from_requirements("p", [
            m.ToolRequirement("node", "20", m.SRC_NVMRC),
            m.ToolRequirement("python", "3.13", m.SRC_PYTHON_VERSION),
            m.ToolRequirement("go", "", m.SRC_GO)])   # unpinned

    def test_match_mismatch_missing_unpinned(self) -> None:
        mgr = FakeManager(active={"node": "20.11.0", "python": "3.11.0"})
        rep = plan.verify(self._profile(), manager=mgr)
        by = {s.tool: s.state for s in rep.statuses}
        self.assertEqual(by["node"], m.STATE_MATCH)        # 20 ⊇ 20.11.0
        self.assertEqual(by["python"], m.STATE_MISMATCH)   # 3.13 vs active 3.11
        self.assertEqual(by["go"], m.STATE_MISSING)        # not active
        self.assertEqual(rep.verdict, "drift")

    def test_manager_missing_is_honest_not_in_sync(self) -> None:
        rep = plan.verify(self._profile(), manager=FakeManager(present=False))
        self.assertEqual(rep.verdict, "manager-missing")
        self.assertFalse(rep.in_sync)                       # NEVER reports in-sync without a manager
        self.assertTrue(all(s.state == m.STATE_MANAGER_MISSING for s in rep.statuses))

    def test_drift_filters_to_problems(self) -> None:
        mgr = FakeManager(active={"node": "20.0.0", "python": "3.13.2"})
        rep = plan.drift(self._profile(), manager=mgr)
        self.assertEqual([s.tool for s in rep.drifted], ["go"])  # node match, python match, go missing


class SwitchGateTests(unittest.TestCase):
    def _prof(self):
        return profile.profile_from_requirements("p", [m.ToolRequirement("node", "20", m.SRC_NVMRC)])

    def test_plan_local_when_present_wrong_version(self) -> None:
        mgr = FakeManager(active={"node": "18.0.0"})
        sp = plan.plan_switch(self._prof(), manager=mgr, scope="local")
        self.assertEqual([a.scope for a in sp.actions], [m.SCOPE_LOCAL])
        self.assertFalse(sp.needs_approval)
        self.assertEqual(sp.actions[0].command, ("mise", "use", "node@20"))

    def test_plan_install_when_missing_is_gated(self) -> None:
        mgr = FakeManager(active={})    # node not installed
        sp = plan.plan_switch(self._prof(), manager=mgr, scope="local")
        scopes = [a.scope for a in sp.actions]
        self.assertIn(m.SCOPE_INSTALL, scopes)
        self.assertTrue(sp.needs_approval)   # install (network/disk) needs approval

    def test_plan_global_is_gated(self) -> None:
        mgr = FakeManager(active={"node": "18.0.0"})
        sp = plan.plan_switch(self._prof(), manager=mgr, scope="global")
        self.assertTrue(sp.needs_approval)
        self.assertTrue(any("--global" in a.command for a in sp.gated))

    def test_already_satisfied_is_noop(self) -> None:
        mgr = FakeManager(active={"node": "20.11.0"})
        self.assertEqual(plan.plan_switch(self._prof(), manager=mgr).actions, ())


class ApplySwitchHonestyTests(unittest.TestCase):
    def _files(self):
        return reader({".nvmrc": "20"})

    def test_no_manager_refuses_without_fake(self) -> None:
        ok, lines = surface.apply_switch(".", manager=FakeManager(present=False), reader=self._files())
        self.assertFalse(ok)
        body = "\n".join(lines)
        self.assertIn("fake switch 하지 않음", body)
        self.assertIn("mise", body)

    def test_local_switch_executes(self) -> None:
        mgr = FakeManager(active={"node": "18.0.0"})
        ok, lines = surface.apply_switch(".", manager=mgr, reader=self._files())
        self.assertTrue(ok)
        self.assertIn(("mise", "use", "node@20"), mgr.ran)     # really ran the local pin
        self.assertIn("사후 검증", "\n".join(lines))

    def test_gated_action_blocked_until_approved(self) -> None:
        mgr = FakeManager(active={})    # missing → install (gated)
        ok, lines = surface.apply_switch(".", manager=mgr, reader=self._files(), approve=False)
        self.assertFalse(ok)
        self.assertEqual(mgr.ran, [])                          # executed NOTHING without approval
        self.assertIn("승인 필요", "\n".join(lines))

    def test_gated_action_runs_with_approve(self) -> None:
        mgr = FakeManager(active={})
        ok, lines = surface.apply_switch(".", manager=mgr, reader=self._files(), approve=True)
        self.assertTrue(any(c[:2] == ("mise", "install") for c in mgr.ran))   # install ran


class ConsoleRoutingTests(unittest.TestCase):
    def _route(self, line: str):
        from forgekit_console.commands.parser import parse_input
        from forgekit_console.commands.router import build_default_context, route
        ctx = build_default_context(Path("."))
        return "\n".join(route(parse_input(line), ctx).lines)

    def test_routes_detect_recommend_verify_switch(self) -> None:
        self.assertIn("toolchain", self._route("/toolchain detect").lower() + "manifest")
        self.assertIn("backend-java-local", self._route("/toolchain recommend backend-java-local"))
        # mise absent on CI → verify/switch must be honest, never fake
        self.assertIn("미설치", self._route("/toolchain verify backend-java-local"))
        self.assertIn("fake switch 하지 않음", self._route("/toolchain switch backend-java-local"))


if __name__ == "__main__":
    unittest.main()
